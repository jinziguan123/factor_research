"""API 层的 Pydantic DTO + 统一响应助手。

所有请求体用显式 ``BaseModel`` 声明：
- 借助 Pydantic v2 自带校验，路由层拿到的就是已清洗的数据；
- 前端 / 测试读 schemas 就能知道契约，不用翻 router 实现。

响应包装统一为：
- 成功：``{"code": 0, "data": ...}``
- 失败：``{"code": <status>, "message": <msg>, "detail": ...}``
router 内部返回 ``ok(...)``，异常由 ``api/main.py`` 里的全局 handler 转成 ``fail`` 结构，
保证成功 / 失败路径都遵循同一 envelope，前端判错只看 ``code != 0``。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------- 请求体 DTO ----------------------------


class CreateEvalIn(BaseModel):
    """``POST /api/evals`` 请求体。

    - ``params`` 缺省 → 用因子 ``default_params``（由 router 在调用 worker 前补齐）。
    - ``forward_periods`` 代表 IC 曲线关心的前瞻天数集合；``n_groups`` 约束在 [2, 20]，
      避免前端误传 0 / 1 / 超大值（eval_service._build_weights / qcut 也会挡住但前置更快）。
    """

    factor_id: str
    params: dict | None = None
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    forward_periods: list[int] = [1, 5, 10]
    n_groups: int = Field(default=5, ge=2, le=20)


class CreateBacktestIn(BaseModel):
    """``POST /api/backtests`` 请求体。

    - ``position`` 只能是 ``"top"`` 或 ``"long_short"``（_build_weights 会再校验一次）；
    - ``cost_bps`` 默认 3bp = 万 3；``init_cash`` 默认 1000 万。
    """

    factor_id: str
    params: dict | None = None
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    n_groups: int = Field(default=5, ge=2, le=20)
    rebalance_period: int = Field(default=1, ge=1)
    # 不用 Literal 约束，避免 Pydantic v2 在枚举错误上输出 422 而非 400 —— 交给
    # _build_weights 抛 ValueError 走全局 handler 统一转 500；前端侧也能由下拉框兜底。
    position: str = "top"
    cost_bps: float = 3.0
    init_cash: float = 1e7


class PoolIn(BaseModel):
    """``POST /api/pools`` / ``PUT /api/pools/{pid}`` 请求体。"""

    name: str
    description: str | None = None
    symbols: list[str] = []


class PoolImportIn(BaseModel):
    """``POST /api/pools/{pid}:import`` 请求体。

    ``text`` 支持任意空白 / 逗号 / 分号混合分隔；router 层按 ``re.split`` 解析。
    """

    text: str


# ---------------------------- 响应包装 ----------------------------


def ok(data: Any = None) -> dict:
    """统一成功响应。

    ``data`` 允许为 ``None`` 时回空 dict，避免前端 ``res.data.xxx`` 的 undefined 爆栈。
    """
    return {"code": 0, "data": data if data is not None else {}}


def fail(code: int, message: str, detail: Any = None) -> dict:
    """统一失败响应。

    仅在全局异常 handler 内使用；router 正常路径应抛 ``HTTPException`` 让 handler 接管，
    保证 ``code`` 与 HTTP status 一致，且 message 永远来自单一出口。
    """
    return {"code": code, "message": message, "detail": detail}
