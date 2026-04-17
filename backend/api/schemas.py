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

from pydantic import BaseModel, Field, model_validator


# ---------------------------- 请求体 DTO ----------------------------


class CreateEvalIn(BaseModel):
    """``POST /api/evals`` 请求体。

    - ``params`` 缺省 → 用因子 ``default_params``（由 router 在调用 worker 前补齐）。
    - ``forward_periods`` 代表 IC 曲线关心的前瞻天数集合；``n_groups`` 约束在 [2, 20]，
      避免前端误传 0 / 1 / 超大值（eval_service._build_weights / qcut 也会挡住但前置更快）。
    - ``split_date`` 可选：若提供，会把评估窗口切成 [start, split) 和 [split, end] 两段，
      各自计算 IC / Rank IC 汇总并放到 payload 的 ic_summary_train / ic_summary_test 里，
      用于样本内 / 样本外一致性检查。必须严格位于 (start_date, end_date) 之间，
      否则校验会在 router 层拦下（空的训练段或测试段没有统计意义）。
    """

    factor_id: str
    params: dict | None = None
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    forward_periods: list[int] = [1, 5, 10]
    n_groups: int = Field(default=5, ge=2, le=20)
    split_date: date | None = None

    @model_validator(mode="after")
    def _check_split_date(self) -> "CreateEvalIn":
        # 严格不等号：split_date 等于 start_date → 训练段为空；等于 end_date → 测试段空。
        # 放行"等号"会让样本段没有任何天可算，汇总统计全 NaN，指标展示很迷惑；直接拒绝。
        if self.split_date is None:
            return self
        if not (self.start_date < self.split_date < self.end_date):
            raise ValueError(
                f"split_date={self.split_date} 必须严格位于 "
                f"({self.start_date}, {self.end_date}) 之间。"
            )
        return self


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
    """``POST /api/pools`` / ``PUT /api/pools/{pid}`` 请求体。

    ``symbols`` 语义（``None`` vs ``[]`` 必须区分）：
    - ``None`` / 未传：``PUT`` 保留现有成员不动（只改 name / description）；
      ``POST`` 建空池。
    - ``[]``：显式清空成员。
    历史坑：默认值设成 ``[]`` 时，前端只想改池名也会把成员一并清掉，修名=失池。
    """

    name: str
    description: str | None = None
    symbols: list[str] | None = None


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
