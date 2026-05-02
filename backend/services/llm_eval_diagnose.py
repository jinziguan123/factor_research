"""LLM 解读评估 Payload 模块（L2.C 借鉴 RD-Agent 反馈循环）。

把规则版 `_build_eval_feedback` 升级到 LLM 版：让 LLM 看到完整 payload
（IC 系列 / 分组累计净值 / IC 衰减 / 健康度 / Alphalens 增强等），
结合因子 hypothesis 给出**结构化诊断 + 可执行建议**，文本质量比规则版
高得多（能解读"IC 衰减太快"、"分组反转"等规则版漏掉的语义）。

接口设计：
- ``diagnose_with_llm(structured, payload, hypothesis, factor_id) -> str``
  返回拼好的诊断文本（已含换行）；失败时抛异常让上层 catch 回落规则版
- prompt 输入裁剪到合理长度（payload 部分字段如 IC 系列只取摘要）
- 输出 JSON：``{"summary": "...", "actions": ["..."]}``

不做的事（YAGNI）：
- 不做 prompt 缓存：单次调用 ≤ 30s，相同 run 不会重复诊断
- 不引入新 LLM 客户端：复用 ``factor_assistant._call_openai_compatible``
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
你是一位严谨的量化研究 reviewer。我会给你一份因子评估报告（包括 hypothesis +
关键指标 + payload 摘要），请用中文给出：

1. **整体诊断**（≤120 字）：这个因子表现如何？预测力强弱？方向稳定吗？
   多空可用否？是否符合用户写的 hypothesis？
2. **可执行建议**（2-4 条）：基于诊断给出**具体动作**——
   例如"IC 衰减极快，建议 forward_periods 从 [1,5] 缩到 [1,2]"、
   "多空 Sharpe 为负但 hypothesis 写的是反转，应取负号"、
   "分组单调但顶组特别突出，可改用 top_n 替代 qcut 提升集中度"。

【硬约束】
- 严格输出 JSON：{"summary": "...", "actions": ["...", "..."]}
- 不要用 Markdown 代码块包裹
- 不要加任何 JSON 之外的解释文字
- summary 简洁；actions 数组每条 ≤80 字、必须可执行

【判定参考阈值（同规则版）】
- |IC| < 0.02 弱，0.02-0.05 一般，≥ 0.05 显著
- |IC_IR| < 0.3 不稳，≥ 0.5 稳健
- 多空 Sharpe < 0 = 方向反转；≥ 1.0 = 实战可用
- 换手 > 0.5 = 实盘成本高
"""


def _safe_finite(v: Any) -> Any:
    """payload 里偶尔会有 NaN/inf 漏网（虽然 eval_service 已清洗）；
    再过一遍变成 None，否则 json.dumps allow_nan=False 会炸。"""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _trim_payload(payload: dict) -> dict:
    """裁剪 payload，留下 LLM 看了有用的字段；超长时间序列只保留头尾若干点。

    完整 payload 包含 IC 序列、分组累计净值时间序列等，每条几百-上千个点；
    全发给 LLM 浪费 token 且收益有限。规则：长度 > 30 的时间序列保留前 5 + 后 5
    + 每 N 取一个采样点，并附标签让 LLM 知道是采样而非全量。

    递归处理嵌套结构——防止 ``time_series.per_symbol.data`` 这类深层大列表漏网。
    """
    def _maybe_trim(val: Any) -> Any:
        if isinstance(val, list):
            if len(val) <= 30:
                # 短列表仍然递归处理内部元素（元素可能是 dict）
                return [_maybe_trim(v) for v in val]
            head = [_maybe_trim(v) for v in val[:5]]
            tail = [_maybe_trim(v) for v in val[-5:]]
            step = max(1, (len(val) - 10) // 8)
            mid = [_maybe_trim(v) for v in val[5:-5:step][:8]]
            return {"_sampled_from": len(val), "values": head + mid + tail}
        if isinstance(val, dict):
            return {kk: _maybe_trim(vv) for kk, vv in val.items()}
        return _safe_finite(val)

    return {k: _maybe_trim(v) for k, v in payload.items()}


def _build_user_prompt(
    structured: dict, payload: dict, hypothesis: str, factor_id: str,
) -> str:
    """组装给 LLM 的 user message。

    包含三段：因子身份 / 关键扁平指标 / 裁剪后的 payload 摘要。
    """
    metrics = {
        k: _safe_finite(structured.get(k))
        for k in (
            "ic_mean", "ic_std", "ic_ir", "ic_win_rate", "ic_t_stat",
            "rank_ic_mean", "rank_ic_std", "rank_ic_ir",
            "turnover_mean", "long_short_sharpe", "long_short_annret",
        )
    }
    trimmed_payload = _trim_payload(payload)

    return (
        f"【因子】 {factor_id}\n"
        f"【研究假设】 {hypothesis or '（未填）'}\n\n"
        f"【关键指标（structured）】\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
        f"【完整 payload（已裁剪长序列）】\n"
        f"{json.dumps(trimmed_payload, ensure_ascii=False, default=str)}\n\n"
        "请按 system 规定的 JSON 格式输出诊断 + 建议。"
    )


def diagnose_with_llm(
    structured: dict,
    payload: dict,
    hypothesis: str,
    factor_id: str,
) -> str:
    """用 LLM 解读评估结果，返回拼好的诊断文本（含换行）。

    抛异常时上层应 catch 回落到规则版（见 eval_service._build_eval_feedback）。
    本函数本身不做 try/except——让失败信号清晰传播到调用方。
    """
    # Lazy import：避免本模块顶层强依赖 factor_assistant（虽然实际两者都已 mature）
    from backend.services.factor_assistant import _call_openai_compatible

    user_prompt = _build_user_prompt(structured, payload, hypothesis, factor_id)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    logger.info(
        "llm_eval_diagnose: factor_id=%s payload_keys=%d", factor_id, len(payload),
    )
    raw = _call_openai_compatible(messages)
    return _format_llm_response(raw)


def _format_llm_response(raw: str) -> str:
    """把 LLM 的 JSON 响应转成多行文本。

    抛 ValueError 表示响应不合规（缺字段 / 非 JSON）。调用方应 catch 转成
    回落规则版的信号。
    """
    # 兼容 LLM 偶尔包 ```json ... ``` 的情况
    text = raw.strip()
    if text.startswith("```"):
        # 去掉首尾 fence
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
    except ValueError as e:
        raise ValueError(f"LLM 响应非合法 JSON: {e}; raw[:200]={raw[:200]!r}") from e

    if not isinstance(obj, dict):
        raise ValueError(f"LLM 响应不是 JSON 对象: {type(obj).__name__}")

    summary = str(obj.get("summary", "")).strip()
    actions = obj.get("actions", [])
    if not isinstance(actions, list):
        actions = []

    if not summary and not actions:
        raise ValueError("LLM 响应 summary 和 actions 均为空")

    lines: list[str] = []
    if summary:
        lines.append(f"📋 {summary}")
    for i, act in enumerate(actions, 1):
        act_text = str(act).strip()
        if act_text:
            lines.append(f"💡 建议 {i}：{act_text}")
    return "\n".join(lines)
