"""Phase 0 因子助手：自然语言 → LLM → 生成 BaseFactor 子类源码 → 落盘。

流水线（单次 / 无会话 / 无评估）：

1. 前端传 ``description``（中文自然语言） + 可选 ``hints``（用户补充说明）
2. 构造带"接口契约 + few-shot"的 system prompt，走 OpenAI 兼容 ``chat/completions``
3. LLM 返回严格 JSON：``{factor_id, display_name, category, description, default_params, code}``
4. **AST 白名单** 校验代码安全（禁 ``os/subprocess/socket``、禁 ``exec/eval/__import__``）
5. 写入 ``backend/factors/llm_generated/<factor_id>.py``
6. 返回前端：保存路径 + 生成元数据 + 代码（供前端展示）

**刻意不做** 的事（Phase 0 定义即极简）：
- 不自动注册到 FactorRegistry（等 hot_reload watchdog 下一次扫描或用户手动刷新）
- 不跑 eval（Phase 1 再做）
- 不支持会话 / 反问（Phase 2 再做）
- 不做 rate limit（受 OPENAI_TIMEOUT_S 兜底；上限由 provider 侧定）

安全兜底：AST 校验失败就不写文件、直接把问题抛回前端；写文件前还会做
``compile(code, ...)`` 确保语法合法。落盘覆盖策略：**禁止覆盖已存在文件**——
手写因子改名撞车时必须手动改名，不让 LLM 悄悄覆盖。
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# 因子落盘目录（相对 backend/ 根固定，不走 settings.factors_dir——后者可被用户改成
# 别的路径，但 llm_generated 子包必须与 Python 导入路径 backend.factors.llm_generated
# 对应，否则 FactorRegistry 扫不到）。
_LLM_FACTORS_DIR = (
    Path(__file__).resolve().parent.parent / "factors" / "llm_generated"
)

# BaseFactor 允许的 category 枚举；和 FactorList.vue 的 categoryLabels 保持一致，
# LLM 不按这个来我们宁可失败也不给前端一个"自己造的分类"。
_ALLOWED_CATEGORIES = ("reversal", "momentum", "volatility", "volume", "custom")

# 代码校验白名单：只放因子计算真正需要的
_ALLOWED_IMPORT_TOP = {
    "__future__",
    "pandas",
    "numpy",
    "math",
    "typing",
    "backend",  # 仅允许 backend.factors.base（见 _check_import_from）
}
_ALLOWED_FROM_IMPORTS = {
    "__future__": {"*"},  # annotations 等，任意
    "backend.factors.base": {"BaseFactor", "FactorContext"},
    "typing": {"*"},
    "pandas": {"*"},
    "numpy": {"*"},
    "math": {"*"},
}
# 禁用的 built-in name —— 出现在代码里就拒绝
_DENY_NAMES = {
    "exec",
    "eval",
    "__import__",
    "compile",
    "open",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
}

# factor_id 合法正则：snake_case，和手写因子习惯保持一致
_FACTOR_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")


class FactorAssistantError(Exception):
    """因子助手处理失败。``message`` 直接返回给前端，不要带敏感信息。"""


@dataclass
class GeneratedFactor:
    """factor_assistant 的最终产物。"""

    factor_id: str
    display_name: str
    category: str
    description: str
    hypothesis: str
    default_params: dict
    code: str
    saved_path: str


# ---------------------------- Prompt 构造 ----------------------------
#
# System prompt 是 Phase 0 最关键的"杠杆"——接口契约没讲清楚，模型就会：
# - import 乱七八糟的第三方库（被 AST 扫描拦下但浪费一次调用）
# - 把 category 写成随便的字符串
# - 忘记 required_warmup 或 load_panel 的 start 前移
#
# 所以这里把硬约束写全、放一个 few-shot，用 JSON mode 强制结构化输出。

_SYSTEM_PROMPT = """\
你是一个严谨的 Python 量化因子工程师。用户用中文描述一个选股因子，你需要把它翻译成
符合项目 `BaseFactor` 接口的 Python 源码，以便直接放进 backend/factors/ 下运行。

【接口契约（必须严格遵守）】
1. 文件开头：`from __future__ import annotations` + `import pandas as pd` + 必要时 `import numpy as np`
2. 必须从 `backend.factors.base` 导入：`from backend.factors.base import BaseFactor, FactorContext`
3. 定义一个继承自 BaseFactor 的类。必填类属性：
   - factor_id: 小写 snake_case 字符串，3-48 个字符，全局唯一
   - display_name: 中文可读名（≤20 字）
   - category: **只能**从 ["reversal", "momentum", "volatility", "volume", "custom"] 选一
   - description: 中文简介（≤80 字，**事实陈述**："因子做什么"）
   - hypothesis: 研究假设（≤200 字，**主观直觉**："为什么相信这个因子有 alpha"）
     必须包含三件事：方向判断（值大 / 值小 → 未来收益正 / 负）+ 经济学直觉（行为
     金融学 / 微观结构 / 信息不对称等机制）+ 适用前提（什么市场环境会失效）。
     例："反转假设——短期超买后续 3-5 日易回调；机制是噪声交易者过度反应；牛市
     后段单边趋势市会显著失效。"
   - default_params: 所有参数的默认值字典
   - params_schema: 参数的 schema 字典（type/default/min/max/desc），用于前端渲染
   - supported_freqs: ("1d",)  —— MVP 只支持日频
4. 必须实现两个方法：
   - `required_warmup(self, params: dict) -> int`：返回自然日（注意不是交易日）预热天数。
     习惯按 `int(window_in_trading_days * 1.5) + 10` 换算（× 1.5 折自然日 + 10 天长假 buffer）。
   - `compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame`：
     * 唯一允许的数据入口：`ctx.data.load_panel(ctx.symbols, start_date, end_date, freq="1d", field=<one of: close|open|high|low|volume|amount_k>, adjust=<"qfq"|"none">)`
     * 复权默认用 "qfq"；价格类字段用 qfq，成交量类字段用 none
     * start_date 必须是 `(ctx.start_date - pd.Timedelta(days=warmup)).date()` 形式以包含预热期
     * 返回前必须 `.loc[ctx.start_date:]` 切掉预热期
     * 返回宽表 DataFrame：index=DatetimeIndex(trade_date), columns=symbol, values=float

【因子质量规范（会直接决定评估指标是否合理，务必遵守）】

因子是用来做 **截面选股** 的：每个交易日上，每只股票得到一个分数，评估模块按此跑 IC、
分组收益、多空组合。因子值必须在截面上有稠密方差，否则 qcut 分组退化、多空无法构造。

1. **方向假设必须明确**：在 `description` 里用一句话说清"因子值越 X，预期未来 N 日收益越 Y"。
   例："因子值越大，预期未来 1 日收益越正（反转假设）。"
   只描述"识别某某形态"而不声明方向是 **不合格** 的——形态 ≠ alpha。

2. **禁止稀疏信号陷阱（最常见错误）**：不要把多个"是否满足阈值"的子项 `clip(lower=0)` 后相乘。
   这会让绝大多数 (日期×股票) 的因子值恰好为 0，直接毁掉所有评估指标。
   正确姿势：
   - 子项之间用 **加法** 组合，**不要** 用乘法
   - 需要"越接近阈值越好"的语义用 `sigmoid` / `tanh` / 线性变换平滑表达，**不要** 用硬 `clip`
   - 子项量纲不一致时先按日期做 **截面 z-score** 再相加：
     ```python
     def _cs_zscore(df):
         mu, sigma = df.mean(axis=1), df.std(axis=1)
         return df.sub(mu, axis=0).div(sigma.where(sigma > 0), axis=0)
     factor = _cs_zscore(feat_a) + _cs_zscore(feat_b) + _cs_zscore(feat_c)
     ```

3. **避免硬阈值魔数**：不要把"涨幅 ≥ 15% 才算强势"这种判断写成 `clip` 阈值；
   这种相对强弱判断应该交给截面排序（z-score / rank）自动完成。

4. **默认做连续因子**。确实需要事件驱动（如财报、除权）时在 description 第一行明确标注
   `[事件驱动]`，并理解评估指标（IC / 多空 / 换手率）会天然不好看。

【硬性禁令】
- 不得 import 除 `__future__`, `pandas`, `numpy`, `math`, `typing`, `backend.factors.base` 外的任何模块
- 不得使用 `exec` / `eval` / `__import__` / `open` / `input` / `compile` / `globals` / `locals`
- 不得访问文件系统、网络、环境变量、子进程
- 不得打印、日志、调 print()
- 不得读写 ctx 未提供的任何属性

【输出格式】
严格返回 JSON 对象（**不要** 用 Markdown 代码块包裹，**不要** 加任何解释文字）：
{
  "factor_id": "...",
  "display_name": "...",
  "category": "reversal|momentum|volatility|volume|custom",
  "description": "...",
  "hypothesis": "...",
  "default_params": {...},
  "code": "<完整的 .py 文件内容字符串，必须能被 python 直接 import 执行>"
}

注意：``code`` 里 BaseFactor 子类**也必须**带 ``hypothesis`` 类属性（与 JSON 顶
层 hypothesis 一致），这样 FactorRegistry 热加载时能从源码读到，跟 JSON 落库
两条路径保持一致。

【示例】
用户需求：跳过最近 5 天，计算再往前 120 天的涨幅。

你的 JSON：
{
  "factor_id": "example_momentum_120_5",
  "display_name": "120日跳5日动量（示例）",
  "category": "momentum",
  "description": "跳过最近 5 天，计算更早 120 日的累计涨幅。",
  "hypothesis": "中长期动量假设——历史涨幅强的票未来 1 周延续概率高；跳过最近 5 天回避短期反转噪声。机制是机构资金惯性建仓 + 散户跟风。在熊市末端 / 风格切换期会失效。",
  "default_params": {"window": 120, "skip": 5},
  "code": "\\"\\"\\"示例：跳跃式动量因子。\\"\\"\\"\\nfrom __future__ import annotations\\n\\nimport pandas as pd\\n\\nfrom backend.factors.base import BaseFactor, FactorContext\\n\\n\\nclass ExampleMomentum120_5(BaseFactor):\\n    factor_id = \\"example_momentum_120_5\\"\\n    display_name = \\"120日跳5日动量（示例）\\"\\n    category = \\"momentum\\"\\n    description = \\"跳过最近 5 天，计算更早 120 日的累计涨幅。\\"\\n    hypothesis = \\"中长期动量假设——历史涨幅强的票未来 1 周延续概率高；跳过最近 5 天回避短期反转噪声。机制是机构资金惯性建仓 + 散户跟风。在熊市末端 / 风格切换期会失效。\\"\\n    default_params = {\\"window\\": 120, \\"skip\\": 5}\\n    params_schema = {\\n        \\"window\\": {\\"type\\": \\"int\\", \\"default\\": 120, \\"min\\": 5, \\"max\\": 504, \\"desc\\": \\"动量窗口（交易日）\\"},\\n        \\"skip\\": {\\"type\\": \\"int\\", \\"default\\": 5, \\"min\\": 0, \\"max\\": 60, \\"desc\\": \\"跳过最近 N 日\\"},\\n    }\\n    supported_freqs = (\\"1d\\",)\\n\\n    def required_warmup(self, params: dict) -> int:\\n        window = int(params.get(\\"window\\", self.default_params[\\"window\\"]))\\n        skip = int(params.get(\\"skip\\", self.default_params[\\"skip\\"]))\\n        return int((window + skip) * 1.5) + 10\\n\\n    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:\\n        window = int(params.get(\\"window\\", self.default_params[\\"window\\"]))\\n        skip = int(params.get(\\"skip\\", self.default_params[\\"skip\\"]))\\n        warmup = self.required_warmup(params)\\n        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()\\n        close = ctx.data.load_panel(ctx.symbols, data_start, ctx.end_date.date(), freq=\\"1d\\", field=\\"close\\", adjust=\\"qfq\\")\\n        if close.empty:\\n            return pd.DataFrame()\\n        factor = close.shift(skip) / close.shift(skip + window) - 1\\n        return factor.loc[ctx.start_date:]\\n"
}
"""


def _build_user_prompt(description: str, hints: str | None) -> str:
    """把用户输入拼成给 LLM 的 user message。hints 可选，留着给 Phase 2 做追问用。"""
    text = f"用户需求：{description.strip()}"
    if hints and hints.strip():
        text += f"\n\n补充信息：{hints.strip()}"
    text += "\n\n请按上面的 JSON 格式输出。记得 factor_id 要能表达这个因子的核心意图。"
    return text


def _build_user_content(
    description: str, hints: str | None, images: list[str] | None, protocol: str
) -> str | list[dict]:
    """按是否有图 + 协议分支，构造 user message 的 content。

    - 无图 → 返回原来的纯文本字符串（兼容两种协议）。
    - 有图 → 返回 content 数组；在文本段开头额外加一句引导语，让 LLM 不要把图当装饰，
      而是把它当做"用户关于因子形态的示例"去理解。
    - Responses API 的图片分片 type 是 ``input_image`` / ``input_text``；Chat Completions
      是 ``image_url`` / ``text``——两套词表，按 ``protocol`` 分发。
    """
    text = _build_user_prompt(description, hints)
    if not images:
        return text

    # prepend instead of append：让 LLM 在读图前先知道这些图是"参考"而不是"目标形态"，
    # 避免它直接去拟合某一张图的特定走势。
    preface = (
        f"【附带参考截图】用户上传了 {len(images)} 张截图（通常是 K 线形态示例），"
        f"请结合图像中的价格 / 成交量特征理解其因子意图。图像是 *参考样例*，"
        f"你的任务仍是给出可泛化的因子表达，而不是专门拟合这几张图。"
    )
    text = f"{preface}\n\n{text}"

    if protocol == "responses":
        parts: list[dict] = [{"type": "input_text", "text": text}]
        for uri in images:
            parts.append({"type": "input_image", "image_url": uri})
        return parts

    # chat_completions：图片分片用 {"type":"image_url","image_url":{"url": data_uri}}
    parts = [{"type": "text", "text": text}]
    for uri in images:
        parts.append({"type": "image_url", "image_url": {"url": uri}})
    return parts


# ---------------------------- LLM 调用 ----------------------------


def _post_and_validate(url: str, payload: dict) -> dict:
    """共享的 HTTP 调用 + 响应健康检查。两种协议分支都会先走这里，再各自解 body。"""
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=settings.openai_timeout_s) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise FactorAssistantError(f"调用 LLM 失败（网络层）：{e}") from e

    if resp.status_code >= 400:
        logger.warning("LLM returned %d: %s", resp.status_code, resp.text[:500])
        raise FactorAssistantError(
            f"LLM 返回错误状态 {resp.status_code}；详情请看后端日志"
        )

    # 2xx 但 content-type 不是 JSON —— 最常见是 OPENAI_BASE_URL 漏了 /v1，
    # 中转把请求路由到网关的 SPA 首页或普通 HTML 错误页。
    ctype = resp.headers.get("content-type", "").lower()
    if "json" not in ctype:
        logger.warning(
            "LLM returned non-JSON content-type=%s; body[:300]=%r",
            ctype,
            resp.text[:300],
        )
        raise FactorAssistantError(
            f"上游不是 JSON 响应（content-type={ctype!r}）；"
            f"十之八九是 OPENAI_BASE_URL 配错——确保以 /v1 结尾，形如 "
            f"https://your-proxy.com/v1"
        )

    try:
        return resp.json()
    except ValueError as e:
        body_preview = (resp.text or "")[:500]
        logger.warning(
            "LLM response parse failed (status=%d): %s; body[:500]=%r",
            resp.status_code,
            e,
            body_preview,
        )
        raise FactorAssistantError(
            f"LLM 响应非法 JSON：{e}；HTTP {resp.status_code} "
            f"body[:300]={body_preview[:300]!r}"
        ) from e


def _call_chat_completions(messages: list[dict]) -> str:
    """老协议 ``POST /v1/chat/completions`` —— 绝大多数中转和 gpt-4o/4o-mini 的默认。"""
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": settings.openai_model,
        "messages": messages,
        "temperature": 0.2,
        # 部分中转默认 stream=true 会返回 SSE，显式关。
        "stream": False,
    }
    # reasoning 系列（o1/o3/gpt-5）不支持 response_format；关掉靠 prompt 约束。
    if settings.openai_response_format_json:
        payload["response_format"] = {"type": "json_object"}

    data = _post_and_validate(url, payload)

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        body_preview = json.dumps(data, ensure_ascii=False)[:500]
        logger.warning("chat.completions missing content: %s; body=%r", e, body_preview)
        raise FactorAssistantError(
            f"LLM 响应结构异常（choices[0].message.content 缺失）：{e}；"
            f"body[:300]={body_preview[:300]!r}。"
            f"常见原因：用了 reasoning 模型（o1/o3/gpt-5 家族）走老 Chat Completions，"
            f"把 content 吞空——改用 OPENAI_API_PROTOCOL=responses 切到新协议。"
        ) from e


def _call_responses(messages: list[dict]) -> str:
    """新协议 ``POST /v1/responses`` —— gpt-5/o1/o3 等 reasoning 模型专用。

    请求里 ``input`` 允许直接复用 Chat Completions 的 ``[{role, content: str}]`` 结构
    （中转 / 官方都支持字符串 content）；返回的 ``output[]`` 里会有 reasoning 节点和
    message 节点，只提 message 的 output_text 文本。
    """
    url = settings.openai_base_url.rstrip("/") + "/responses"
    payload: dict = {
        "model": settings.openai_model,
        # messages 复用 chat 的结构；role 允许 system/user/assistant/developer。
        "input": messages,
        "stream": False,
        # 默认 "high" 会把输出预算全烧在隐藏思考里，导致 output[] 空——
        # "medium" 给可见回答留预算；简单任务也够用。
        "reasoning": {"effort": "medium"},
    }
    # Responses API 的 JSON mode 在 ``text.format`` 下；reasoning 模型这条一般也不支持。
    if settings.openai_response_format_json:
        payload["text"] = {"format": {"type": "json_object"}}

    data = _post_and_validate(url, payload)

    # 从 output[] 提 message 节点里的 output_text
    parts: list[str] = []
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))

    if parts:
        return "".join(parts)

    # output[] 里没 message 节点 —— token 全耗在 reasoning 或 provider 侧掉了。
    usage = data.get("usage", {}) or {}
    details = usage.get("output_tokens_details", {}) or {}
    body_preview = json.dumps(data, ensure_ascii=False)[:500]
    logger.warning("Responses API: no visible message; body=%r", body_preview)
    raise FactorAssistantError(
        f"LLM 没产出可见回答（output[] 里无 message 节点）；"
        f"output_tokens={usage.get('output_tokens', 0)}, "
        f"reasoning_tokens={details.get('reasoning_tokens', 0)}。"
        f"常见原因：reasoning.effort 太高把预算全烧在思考里；"
        f"或 prompt 不够明确让模型觉得没啥好说的。"
    )


def _call_openai_compatible(messages: list[dict]) -> str:
    """按 ``OPENAI_API_PROTOCOL`` 分发到老 Chat Completions 或新 Responses API。

    测试里会 monkeypatch 这个函数替换成桩，所以这一层只做协议分发，不做其它逻辑。
    """
    if not settings.openai_api_key:
        raise FactorAssistantError(
            "LLM 未配置：请在 backend/.env 中填写 OPENAI_API_KEY"
        )

    proto = (settings.openai_api_protocol or "chat_completions").lower()
    if proto == "responses":
        return _call_responses(messages)
    if proto == "chat_completions":
        return _call_chat_completions(messages)
    raise FactorAssistantError(
        f"OPENAI_API_PROTOCOL 非法：{proto!r}；"
        f"可选 'chat_completions'（默认）或 'responses'"
    )


def _parse_llm_json(raw: str) -> dict:
    """从 LLM 文本里抽出 JSON。JSON mode 下直接 loads；否则剥 ```json 围栏。"""
    text = raw.strip()
    # 部分中转不支持 response_format 会返回 ```json ... ``` 包裹形式，兜底剥掉
    if text.startswith("```"):
        # 去掉首行 ``` 或 ```json
        lines = text.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            # 去掉末尾 ```
            while lines and lines[-1].strip().startswith("```"):
                lines.pop()
            text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise FactorAssistantError(
            f"LLM 输出不是合法 JSON：{e}；前 200 字：{text[:200]!r}"
        ) from e
    if not isinstance(obj, dict):
        raise FactorAssistantError("LLM 输出 JSON 不是对象（期望 dict）")
    return obj


# ---------------------------- JSON 结构校验 ----------------------------


def _validate_llm_payload(obj: dict) -> dict:
    """校验 LLM 返回的字段齐整 + 类型合法，返回标准化后的 dict。"""
    # hypothesis 是 RD-Agent 借鉴：因子的研究假设作为一等公民。LLM 必须填，
    # 旧手写因子未填留空仅在 fr_factor_meta 层兼容（hypothesis 列 NULL）。
    required = (
        "factor_id", "display_name", "category", "description", "hypothesis", "code",
    )
    missing = [k for k in required if k not in obj]
    if missing:
        raise FactorAssistantError(f"LLM 返回缺少字段：{missing}")

    factor_id = str(obj["factor_id"]).strip()
    if not _FACTOR_ID_RE.match(factor_id):
        raise FactorAssistantError(
            f"factor_id 不合法：{factor_id!r}；应为 3-48 位 snake_case"
        )

    category = str(obj["category"]).strip()
    if category not in _ALLOWED_CATEGORIES:
        raise FactorAssistantError(
            f"category 非法：{category!r}；合法值：{_ALLOWED_CATEGORIES}"
        )

    default_params = obj.get("default_params", {})
    if not isinstance(default_params, dict):
        raise FactorAssistantError("default_params 必须是 JSON 对象")

    code = str(obj["code"])
    if not code.strip():
        raise FactorAssistantError("code 字段为空")

    hypothesis = str(obj["hypothesis"]).strip()
    if not hypothesis:
        raise FactorAssistantError(
            "hypothesis 字段不能为空——必须说明研究假设（方向 + 机制 + 适用前提）"
        )

    return {
        "factor_id": factor_id,
        "display_name": str(obj["display_name"]).strip()[:50],
        "category": category,
        "description": str(obj["description"]).strip()[:200],
        "hypothesis": hypothesis[:500],
        "default_params": default_params,
        "code": code,
    }


# ---------------------------- AST 白名单校验 ----------------------------


def _check_import(node: ast.Import) -> None:
    """``import foo`` / ``import foo.bar`` —— 顶级模块名必须在白名单里。"""
    for alias in node.names:
        top = alias.name.split(".")[0]
        if top not in _ALLOWED_IMPORT_TOP:
            raise FactorAssistantError(
                f"不允许的 import：{alias.name!r}（仅允许 "
                f"{sorted(_ALLOWED_IMPORT_TOP)}）"
            )


def _check_import_from(node: ast.ImportFrom) -> None:
    """``from X import Y`` —— 限定 X 必须在白名单里，且 Y 符合 X 的允许符号。"""
    if node.module is None:
        # 形如 `from . import x`，禁用——不允许相对导入
        raise FactorAssistantError("禁止相对导入（from . import ...）")
    top = node.module.split(".")[0]
    if top not in _ALLOWED_IMPORT_TOP:
        raise FactorAssistantError(f"不允许的 import：from {node.module!r}")
    # 深入一层：backend 只允许 backend.factors.base
    if top == "backend" and node.module != "backend.factors.base":
        raise FactorAssistantError(
            f"不允许的 import：from {node.module!r}；"
            "只允许 from backend.factors.base import BaseFactor, FactorContext"
        )


def _check_call_name(node: ast.Call) -> None:
    """拒绝调用黑名单里的 built-in。只看直接调用如 ``exec(...)``；
    ``X.exec()`` 形式不拦（属性调用，能白名单内模块调的都是安全的）。
    """
    if isinstance(node.func, ast.Name) and node.func.id in _DENY_NAMES:
        raise FactorAssistantError(f"禁止调用 {node.func.id}(...)")


def _validate_code_ast(code: str) -> None:
    """编译 + AST 白名单 扫描。

    - ``compile()`` 先保证语法合法；LLM 漏掉冒号 / 括号的常见毛病直接暴露
    - 再遍历 AST 抓 import / Call / 危险节点
    - 不做 ``exec`` 落地——AST 分析完毕即可，**绝不**在后端进程里 import 这个文件，
      否则一次注入就能劫持 API 进程。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise FactorAssistantError(
            f"代码语法错误：{e.msg} (line {e.lineno})"
        ) from e

    # 语义冒烟：能 compile 的才能 import 成功
    try:
        compile(tree, filename="<llm_factor>", mode="exec")
    except SyntaxError as e:
        raise FactorAssistantError(f"代码 compile 失败：{e.msg}") from e

    has_base_factor_class = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _check_import(node)
        elif isinstance(node, ast.ImportFrom):
            _check_import_from(node)
        elif isinstance(node, ast.Call):
            _check_call_name(node)
        elif isinstance(node, ast.ClassDef):
            # 至少有一个继承 BaseFactor 的类
            for base in node.bases:
                base_name = (
                    base.id
                    if isinstance(base, ast.Name)
                    else (base.attr if isinstance(base, ast.Attribute) else None)
                )
                if base_name == "BaseFactor":
                    has_base_factor_class = True
        # Attribute 访问 __dunder__（除 __future__ 的场景）在因子代码里极少合法，
        # 但 pandas 里偶尔会出现 df.__class__ 这类——只拦显式的 __import__ / __builtins__
        elif isinstance(node, ast.Attribute):
            if node.attr in ("__import__", "__builtins__", "__subclasses__"):
                raise FactorAssistantError(
                    f"禁止访问危险属性：.{node.attr}"
                )

    if not has_base_factor_class:
        raise FactorAssistantError(
            "代码中未找到 `class X(BaseFactor):` 定义；"
            "生成的因子必须继承 BaseFactor"
        )


# ---------------------------- 落盘 ----------------------------


def _save_factor_file(factor_id: str, code: str) -> Path:
    """把 code 写到 ``backend/factors/llm_generated/<factor_id>.py``；
    文件已存在则 **拒绝覆盖**，让用户显式改 factor_id 或手动删旧文件。
    """
    _LLM_FACTORS_DIR.mkdir(parents=True, exist_ok=True)
    target = _LLM_FACTORS_DIR / f"{factor_id}.py"
    if target.exists():
        raise FactorAssistantError(
            f"因子文件已存在：{target.name}；请换一个 factor_id 或先删除旧文件"
        )
    # 末尾补换行，避免某些编辑器 / lint 抱怨
    body = code if code.endswith("\n") else code + "\n"
    target.write_text(body, encoding="utf-8")
    return target


# ---------------------------- Public API ----------------------------


def translate_and_save(
    description: str,
    hints: str | None = None,
    images: list[str] | None = None,
) -> GeneratedFactor:
    """一次性把自然语言描述变成落盘的因子文件。

    ``images`` 为 data URI 列表（前端把截图转成 base64 data URI 传进来），
    用来让 vision-capable 的模型"看"到用户心里的 K 线形态；router 层已校验大小和张数。

    会抛 ``FactorAssistantError`` 的已知失败点：

    - .env 没配 OPENAI_API_KEY → 503 级
    - LLM 调用超时 / 5xx → 502 级
    - 返回不是 JSON / 缺字段 / category 非法 → 400 级（模型输出问题）
    - 代码 AST 校验失败 → 400 级
    - 落盘文件已存在 → 409 级

    router 层据此映射 HTTP status。
    """
    if not description or not description.strip():
        raise FactorAssistantError("description 不能为空")

    protocol = (settings.openai_api_protocol or "chat_completions").lower()
    user_content = _build_user_content(description, hints, images, protocol)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    logger.info(
        "factor_assistant: translate desc=%r hints=%r images=%d model=%s protocol=%s",
        description[:100],
        (hints or "")[:100],
        len(images or []),
        settings.openai_model,
        protocol,
    )

    raw = _call_openai_compatible(messages)
    obj = _parse_llm_json(raw)
    payload = _validate_llm_payload(obj)
    _validate_code_ast(payload["code"])

    saved = _save_factor_file(payload["factor_id"], payload["code"])
    logger.info(
        "factor_assistant: saved factor_id=%s path=%s",
        payload["factor_id"],
        saved,
    )
    return GeneratedFactor(
        factor_id=payload["factor_id"],
        display_name=payload["display_name"],
        category=payload["category"],
        description=payload["description"],
        hypothesis=payload["hypothesis"],
        default_params=payload["default_params"],
        code=payload["code"],
        saved_path=str(saved),
    )
