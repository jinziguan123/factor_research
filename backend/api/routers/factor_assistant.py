"""因子助手（Phase 0）路由：自然语言 → LLM → 生成 + 落盘 BaseFactor 子类。

只提供一个 endpoint：``POST /api/factor_assistant/translate``。

错误映射（全部从 ``FactorAssistantError`` 的 message 字面特征识别——service 层抛
错时已经按"哪一步失败"措辞，router 层保持零业务逻辑，只做 HTTP 语义）：

- 未配置 API key → 503  （部署问题，前端提示管理员）
- LLM 网络层 / 上游 5xx → 502 （上游问题，可让用户重试）
- LLM 输出格式 / 校验失败 → 400 （模型输出烂，建议换措辞重试）
- 文件已存在 → 409 （用户层可操作：换 factor_id 或删旧文件）
- 其它：走全局 500 handler

【L1.1 自动评估】（借鉴 RD-Agent 的 Validation Agent）：
当 ``auto_eval_pool_id`` 给定时，生成因子后自动派发一个轻量评估
（最近 60 个交易日 + 沿用因子 default_params + n_groups=5），返回
``auto_eval_run_id``。前端展示"📊 自动评估进行中"链接，用户可点
进 EvalDetail 看 IC / 健康度。eval_service 跑完会把诊断写到
fr_factor_eval_runs.feedback_text。

Phase 0 不做：
- 不触发 FactorRegistry 重扫——留给热加载 watchdog / 前端 reload 按钮；
- 不返回 SSE 进度——单次调用 ≤ 60s，前端 loading spinner 足够。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, model_validator

from backend.api.schemas import ok
from backend.runtime.entries import eval_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.services.factor_assistant import (
    FactorAssistantError,
    evolve_factor as _evolve_factor_service,
    negate_factor_save,
    translate_and_save,
)
from backend.services.params_hash import params_hash
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/factor_assistant", tags=["factor_assistant"])


# 单张图 data URI 的字符串长度上限。base64 编码比原图膨胀 ~1.37x，
# 2.5M 字符 ≈ 1.8MB 原图，够 K 线截图用。上限主要是兜底误操作 / 恶意请求；
# 真正的交互提示由前端负责。
_IMAGE_MAX_DATA_URI_LEN = 2_500_000
_IMAGE_MAX_COUNT = 4


class TranslateIn(BaseModel):
    """``POST /api/factor_assistant/translate`` 请求体。

    ``description`` 走中文自然语言，``hints`` 可留空——预留给 Phase 2 的追问上下文。

    ``images``：可选 data URI（``data:image/...;base64,...``）列表，最多 4 张，每张 ≤ 2MB。
    走同步一把梭，不落盘、随请求体传输，用完即抛——完全不引入存储/清理成本。
    """

    description: str = Field(..., min_length=4, max_length=2000)
    hints: str | None = Field(default=None, max_length=2000)
    images: list[str] | None = Field(
        default=None,
        description="可选 data URI 列表，最多 4 张，每张 ≤ 2MB",
    )
    auto_eval_pool_id: int | None = Field(
        default=None,
        description="可选；给定 pool_id 时生成因子后自动派发一个轻量 IC 评估（60 天窗口）",
    )

    @model_validator(mode="after")
    def _check_images(self) -> "TranslateIn":
        if not self.images:
            return self
        if len(self.images) > _IMAGE_MAX_COUNT:
            raise ValueError(
                f"images 最多 {_IMAGE_MAX_COUNT} 张，当前 {len(self.images)} 张"
            )
        for i, uri in enumerate(self.images):
            if not isinstance(uri, str) or not uri.startswith("data:image/"):
                raise ValueError(
                    f"images[{i}] 必须是 `data:image/...;base64,...` 格式的 data URI"
                )
            if len(uri) > _IMAGE_MAX_DATA_URI_LEN:
                raise ValueError(
                    f"images[{i}] 过大（{len(uri)} 字符），请压缩到 ≤ 2MB 再上传"
                )
        return self


def _map_error_to_status(err: FactorAssistantError) -> int:
    """把 service 层抛出的错误按 message 关键字映射到 HTTP status。

    service 层的 docstring 里已经列出了"哪种错对应哪种 HTTP 语义"——这里就是那张表的实现。
    用关键字而不是 error code：保持 service 层可独立被 CLI/脚本调用，不耦合 HTTP。
    """
    msg = str(err)
    if "OPENAI_API_KEY" in msg:
        return 503
    if "网络层" in msg or "返回错误状态" in msg or "响应结构异常" in msg:
        return 502
    if "已存在" in msg:
        return 409
    # 剩下全是 LLM 输出校验类（JSON 不合法 / 字段缺失 / AST 校验失败等）
    return 400


def _dispatch_auto_eval(
    factor_id: str, pool_id: int, default_params: dict, bt: BackgroundTasks,
) -> str | None:
    """生成因子后派发一个轻量评估；返回 ``run_id``，失败时返回 None。

    评估配置（L1.1 minimal）：
    - 时间窗口：最近 60 个自然日（不严格交易日，让 service 层去对齐）
    - 参数：用因子 default_params（不让用户挑参，先看默认值好不好）
    - forward_periods=[1, 5]、n_groups=5（典型量化默认）

    与正常 ``POST /api/evals`` 的差别：
    - 不要求用户给完整时间区间 + forward_periods 等参数；
    - 失败不抛 HTTP 错——auto-eval 只是"附加福利"，主流程已成功。
    """
    try:
        reg = FactorRegistry()
        reg.scan_and_register()
        version = reg.latest_version_from_db(factor_id)
        end_date = date.today()
        start_date = end_date - timedelta(days=60)
        forward_periods = [1, 5]
        n_groups = 5
        run_id = uuid.uuid4().hex
        phash = params_hash(default_params)

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fr_factor_eval_runs
                    (run_id, factor_id, factor_version, params_hash, params_json,
                     pool_id, freq, start_date, end_date, forward_periods, n_groups,
                     split_date, status, progress, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
                    """,
                    (
                        run_id, factor_id, version, phash,
                        json.dumps(default_params, ensure_ascii=False),
                        pool_id, "1d", start_date, end_date,
                        ",".join(str(x) for x in forward_periods), n_groups,
                        None,  # split_date
                        datetime.now(),
                    ),
                )
            c.commit()

        # 派发到 ProcessPool（与 POST /api/evals 同构）；body 用 dict 形式传
        body = {
            "factor_id": factor_id,
            "params": default_params,
            "pool_id": pool_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "freq": "1d",
            "forward_periods": forward_periods,
            "n_groups": n_groups,
            "split_date": None,
        }
        bt.add_task(submit, eval_entry, run_id, body)
        return run_id
    except Exception:
        # auto-eval 不应阻塞主流程；DB 不通 / pool 不存在等问题降级为"不派发"
        import logging
        logging.getLogger(__name__).exception(
            "auto-eval dispatch failed for factor_id=%s pool_id=%s",
            factor_id, pool_id,
        )
        return None


@router.post("/translate")
def translate(body: TranslateIn, bt: BackgroundTasks) -> dict:
    """把自然语言描述翻译成 BaseFactor 子类源码并落盘。

    成功返回 ``{code:0, data:{factor_id, display_name, category, description,
    default_params, code, saved_path, auto_eval_run_id?}}``——``code`` 字段供
    前端展示，``saved_path`` 让用户知道文件落在哪里；下次 FactorRegistry 扫描
    （热加载 / 手动刷新）就会自动注册该因子，不需要重启服务。

    若 ``auto_eval_pool_id`` 给定，会同步派发一次 60 天 IC 评估，``auto_eval_run_id``
    供前端跳转 EvalDetail 查看进度。
    """
    try:
        gen = translate_and_save(body.description, body.hints, body.images)
    except FactorAssistantError as e:
        raise HTTPException(status_code=_map_error_to_status(e), detail=str(e))

    auto_eval_run_id: str | None = None
    if body.auto_eval_pool_id is not None:
        auto_eval_run_id = _dispatch_auto_eval(
            gen.factor_id, body.auto_eval_pool_id, gen.default_params, bt,
        )

    return ok(
        {
            "factor_id": gen.factor_id,
            "display_name": gen.display_name,
            "category": gen.category,
            "description": gen.description,
            "hypothesis": gen.hypothesis,
            "default_params": gen.default_params,
            "code": gen.code,
            "saved_path": gen.saved_path,
            "auto_eval_run_id": auto_eval_run_id,
        }
    )


class NegateIn(BaseModel):
    """``POST /api/factor_assistant/negate`` 请求体。"""

    factor_id: str = Field(..., min_length=3, max_length=48)
    auto_eval_pool_id: int | None = Field(
        default=None,
        description="可选；给定时反向因子落盘后自动派发 60 天 IC 评估",
    )


@router.post("/negate")
def negate(body: NegateIn, bt: BackgroundTasks) -> dict:
    """L2.A 反向因子：对一个已存在因子做"取负"得到镜像版本。

    用于评估诊断显示"多空 Sharpe 为负——试将因子取负号"时的"反向"按钮。
    流程：
    1. 用 FactorRegistry 找到原因子源码（必须可读）；
    2. 调 ``negate_factor_save`` 做 AST 改写（factor_id 加 ``_neg``、类名带
       ``Neg``、display_name 加"（取负）"、hypothesis 加"已取负"前缀、
       compute 方法所有 return 包 USub）；
    3. 落盘到 ``backend/factors/llm_generated/<orig>_neg.py``；
    4. 若 ``auto_eval_pool_id`` 给定，立即派发一次 IC 评估。

    错误：
    - 原因子不存在 / 源码读不到 → 404
    - 反转后文件已存在（重复点） → 409
    - 反转后代码 AST 校验失败（罕见——原源码用了非白名单特性） → 400
    """
    import inspect

    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        inst = reg.get(body.factor_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"factor_id={body.factor_id} 未注册")

    src_file = inspect.getsourcefile(inst.__class__)
    if not src_file:
        raise HTTPException(status_code=500, detail=f"无法定位 {body.factor_id} 的源文件")
    try:
        orig_code = open(src_file, encoding="utf-8").read()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取原源码失败：{e}") from e

    try:
        gen = negate_factor_save(body.factor_id, orig_code)
    except FactorAssistantError as e:
        raise HTTPException(status_code=_map_error_to_status(e), detail=str(e)) from e

    auto_eval_run_id: str | None = None
    if body.auto_eval_pool_id is not None:
        auto_eval_run_id = _dispatch_auto_eval(
            gen.factor_id, body.auto_eval_pool_id, gen.default_params, bt,
        )

    return ok(
        {
            "factor_id": gen.factor_id,
            "display_name": gen.display_name,
            "category": gen.category,
            "description": gen.description,
            "hypothesis": gen.hypothesis,
            "default_params": gen.default_params,
            "code": gen.code,
            "saved_path": gen.saved_path,
            "auto_eval_run_id": auto_eval_run_id,
        }
    )


# ---------------------------- L2.D 因子进化 ----------------------------


class EvolveIn(BaseModel):
    """``POST /api/factor_assistant/evolve`` 请求体。"""

    parent_factor_id: str = Field(..., min_length=3, max_length=64)
    parent_eval_run_id: str | None = Field(
        default=None,
        description="可选；指定基于哪条评估反馈进化（路由会读 feedback_text + 关键指标）",
    )
    extra_hint: str | None = Field(
        default=None, max_length=500,
        description="可选；用户额外指令，如'想要更短窗口'",
    )
    auto_eval_pool_id: int | None = Field(
        default=None,
        description="可选；给定时新因子立刻派发 60 天 IC 评估",
    )


@router.post("/evolve")
def evolve(body: EvolveIn, bt: BackgroundTasks) -> dict:
    """L2.D 因子进化：基于 parent + 评估反馈生成下一代。"""
    import inspect

    from backend.services.factor_assistant import (
        _read_factor_meta_for_evolve,
        _update_lineage as _update_lineage_fn,
    )

    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        inst = reg.get(body.parent_factor_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"parent_factor_id={body.parent_factor_id} 未注册",
        )

    src_file = inspect.getsourcefile(inst.__class__)
    if not src_file:
        raise HTTPException(
            status_code=500, detail=f"无法定位 {body.parent_factor_id} 的源文件",
        )
    try:
        with open(src_file, encoding="utf-8") as f:
            parent_src = f.read()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取父代源码失败：{e}") from e

    try:
        gen = _evolve_factor_service(
            parent_factor_id=body.parent_factor_id,
            parent_source_code=parent_src,
            parent_eval_run_id=body.parent_eval_run_id,
            extra_hint=body.extra_hint,
        )
    except FactorAssistantError as e:
        raise HTTPException(
            status_code=_map_error_to_status(e), detail=str(e),
        ) from e

    # 让新因子写进 fr_factor_meta（默认 generation=1 / parent=NULL）
    reg.scan_and_register()

    # 计算 root + generation：用同 root 下最大 generation + 1，避免连续从
    # 同一父代进化时撞重名（service 层已经按这个算了 new_factor_id）
    parent_meta = _read_factor_meta_for_evolve(body.parent_factor_id)
    new_generation = parent_meta["max_generation_in_lineage"] + 1
    new_root = parent_meta["root_factor_id"]

    _update_lineage_fn(
        factor_id=gen.factor_id,
        parent_factor_id=body.parent_factor_id,
        parent_eval_run_id=body.parent_eval_run_id,
        generation=new_generation,
        root_factor_id=new_root,
    )

    auto_eval_run_id: str | None = None
    if body.auto_eval_pool_id is not None:
        auto_eval_run_id = _dispatch_auto_eval(
            gen.factor_id, body.auto_eval_pool_id, gen.default_params, bt,
        )

    return ok(
        {
            "factor_id": gen.factor_id,
            "display_name": gen.display_name,
            "category": gen.category,
            "description": gen.description,
            "hypothesis": gen.hypothesis,
            "default_params": gen.default_params,
            "code": gen.code,
            "saved_path": gen.saved_path,
            "parent_factor_id": body.parent_factor_id,
            "parent_eval_run_id": body.parent_eval_run_id,
            "generation": new_generation,
            "root_factor_id": new_root,
            "auto_eval_run_id": auto_eval_run_id,
        }
    )
