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

from datetime import date, datetime
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
    - ``cost_bps`` 默认 3bp = 万 3；``init_cash`` 默认 1000 万；
    - ``filter_price_limit`` 默认 False（保留与历史回测的可对比性）。
      开启后按 ``|pct_change| ≥ 0.097`` 的近似口径剔除当日触板票，详见
      ``backtest_service._compute_price_limit_mask`` docstring 的误差方向说明。
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
    filter_price_limit: bool = False


class CreateCostSensitivityIn(BaseModel):
    """``POST /api/cost-sensitivity`` 请求体。

    和 ``CreateBacktestIn`` 几乎完全一致，只是把 ``cost_bps: float`` 替换成
    ``cost_bps_list: list[float]``，让后端循环跑。

    校验策略：
    - ``cost_bps_list`` 至少 2 个点（否则跟 single backtest 没区别，应走 /backtests）；
      上限 20 个点，防止前端误传很长的 range 把后端拖满。
    - 单点 ∈ [0, 200]（2% = 200bp 已是非常离谱的估算，留余量）。
    - 去重 + 升序由后端 service 统一处理，这里只做必要边界校验。
    """

    factor_id: str
    params: dict | None = None
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    n_groups: int = Field(default=5, ge=2, le=20)
    rebalance_period: int = Field(default=1, ge=1)
    position: str = "top"
    init_cash: float = 1e7
    cost_bps_list: list[float] = Field(..., min_length=2, max_length=20)
    filter_price_limit: bool = False

    @model_validator(mode="after")
    def _check_cost_bps_list(self) -> "CreateCostSensitivityIn":
        # 值域校验：负费率没有物理意义（返佣不在我们建模范围内）；过大（>200bp）
        # 通常是前端单位输错（比如把 0.03 当成 3 传成 300）。
        for v in self.cost_bps_list:
            if v < 0 or v > 200:
                raise ValueError(
                    f"cost_bps={v} 必须在 [0, 200] 基点区间内（过大可能是单位错误）"
                )
        return self


class CreateParamSensitivityIn(BaseModel):
    """``POST /api/param-sensitivity`` 请求体。

    扫同一因子的一个超参数（param_name）在 values 中取各值时的评估指标；
    任务异步执行，结果入 fr_param_sensitivity_runs，状态机同 cost_sensitivity。

    校验策略：
    - param_name 必须是 factor.params_schema 的 key（service 层也会校验一次）；
    - values 至少 2 个点（单点没有"邻域"概念）；上限 15，防止扫到天黑——
      单点 20-60s，15 点是现实下容忍度的天花板。
    """

    factor_id: str
    param_name: str
    values: list[float] = Field(..., min_length=2, max_length=15)
    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    n_groups: int = Field(default=5, ge=2, le=20)
    forward_periods: list[int] = Field(default_factory=lambda: [1, 5, 10])
    base_params: dict | None = None

    @model_validator(mode="after")
    def _check_values(self) -> "CreateParamSensitivityIn":
        # 去重后仍需 >=2 个：前端允许用户手填重复，这里统一按唯一值数量卡下限。
        unique = {float(v) for v in self.values}
        if len(unique) < 2:
            raise ValueError("values 至少需要 2 个不同的扫描点")
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date={self.start_date} 必须早于 end_date={self.end_date}"
            )
        return self


class CompositionFactorItem(BaseModel):
    """合成请求里单个因子项：因子 id + 可选 params。

    ``params`` None → 使用该因子的 ``default_params``（由 composition_service
    在 _load_or_compute_factor 里补齐），与单因子评估完全对齐。
    """

    factor_id: str
    params: dict | None = None


class CreateCompositionIn(BaseModel):
    """``POST /api/compositions`` 请求体。

    - ``factor_items`` 至少 2 个，最多 8 个：
      < 2 退化为单因子评估，应走 /evals；> 8 相关性矩阵已经很难看清，
      且 orthogonal_equal 在高维下数值稳定性变差。
    - ``method`` 限制 4 种，避免拼写错误静默跑出奇怪结果。
    - ``ic_weight_period`` 只对 ic_weighted 有用，放这里是为了 schema 统一。
    """

    pool_id: int
    start_date: date
    end_date: date
    freq: str = "1d"
    factor_items: list[CompositionFactorItem] = Field(..., min_length=2, max_length=8)
    method: str = "equal"
    n_groups: int = Field(default=5, ge=2, le=20)
    forward_periods: list[int] = [1, 5, 10]
    ic_weight_period: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def _check_fields(self) -> "CreateCompositionIn":
        if self.method not in ("equal", "ic_weighted", "orthogonal_equal", "ml_lgb"):
            raise ValueError(
                f"method={self.method!r} 不支持，"
                "仅接受 equal/ic_weighted/orthogonal_equal/ml_lgb"
            )
        if self.start_date >= self.end_date:
            raise ValueError(
                f"start_date={self.start_date} 必须早于 end_date={self.end_date}"
            )
        # 因子 id 去重：同一因子即使 params 不同也不建议放同一次合成（语义混乱、
        # 相关性矩阵对角附近出现极高值）。
        ids = [it.factor_id for it in self.factor_items]
        if len(set(ids)) != len(ids):
            raise ValueError(f"factor_items 里存在重复的 factor_id: {ids}")
        return self


class CreateSignalIn(BaseModel):
    """``POST /api/signals`` 请求体。

    实盘信号触发：用一组因子（单或多）按当下市场快照算选股排名。
    与 composition 的差异：
    - 没有 start_date / end_date：信号只看"当下"，历史窗口由 service 内部决定；
    - 没有 forward_periods：不评估 IC，只看末行 qcut；
    - 多了 ``as_of_time``（默认 NOW()）/ ``use_realtime`` / ``filter_price_limit``；
    - ``method`` 多一个 ``"single"`` 表示单因子（factor_items 必须正好 1 个）。
    """

    factor_items: list[CompositionFactorItem] = Field(..., min_length=1, max_length=8)
    method: str = "equal"
    pool_id: int
    n_groups: int = Field(default=5, ge=2, le=20)
    ic_lookback_days: int = Field(default=60, ge=10, le=500)
    as_of_time: datetime | None = None  # None → service 内部用 NOW()
    use_realtime: bool = True
    filter_price_limit: bool = True
    # top_n: 可选 top K 限制；None → qcut 顶组全部（兼容旧行为）
    top_n: int | None = Field(default=None, ge=1, le=200)

    @model_validator(mode="after")
    def _check_fields(self) -> "CreateSignalIn":
        if self.method not in ("equal", "ic_weighted", "orthogonal_equal", "single"):
            raise ValueError(
                f"method={self.method!r} 不支持，"
                "仅接受 equal/ic_weighted/orthogonal_equal/single"
            )
        if self.method == "single" and len(self.factor_items) != 1:
            raise ValueError("method='single' 时 factor_items 必须正好 1 个")
        if self.method != "single":
            ids = [it.factor_id for it in self.factor_items]
            if len(set(ids)) != len(ids):
                raise ValueError(f"factor_items 里存在重复 factor_id: {ids}")
        return self


class CreateSubscriptionIn(BaseModel):
    """``POST /api/signal-subscriptions`` 请求体。

    实盘监控订阅：worker 按 ``refresh_interval_sec`` 周期重算这个因子组合，
    每次产出一条新的 signal_run（关联 subscription_id），保留刷新历史。

    支持两种创建方式：
    1. 完整 body（与 CreateSignalIn 类似，但去掉 use_realtime / as_of_time）；
    2. 从已有 signal_run 派生：路由会从 fr_signal_runs 读出 config 拼成 body。
    """

    factor_items: list[CompositionFactorItem] = Field(..., min_length=1, max_length=8)
    method: str = "equal"
    pool_id: int
    n_groups: int = Field(default=5, ge=2, le=20)
    ic_lookback_days: int = Field(default=60, ge=10, le=500)
    filter_price_limit: bool = True
    top_n: int | None = Field(default=None, ge=1, le=200)
    # 调度：默认 5min；30s floor 由 service 层保护
    refresh_interval_sec: int = Field(default=300, ge=30, le=3600)

    @model_validator(mode="after")
    def _check_method(self) -> "CreateSubscriptionIn":
        if self.method not in ("equal", "ic_weighted", "orthogonal_equal", "single"):
            raise ValueError(
                f"method={self.method!r} 不支持，"
                "仅接受 equal/ic_weighted/orthogonal_equal/single"
            )
        if self.method == "single" and len(self.factor_items) != 1:
            raise ValueError("method='single' 时 factor_items 必须正好 1 个")
        return self


class UpdateSubscriptionIn(BaseModel):
    """``PUT /api/signal-subscriptions/{id}`` 请求体（部分字段更新）。

    主要用于 toggle is_active 或调整 refresh_interval_sec。
    factor_items / method / pool_id 等"配置层"字段不可修改——若要改，
    DELETE + 重建一条新订阅，避免破坏 last_run_id 链的语义连续性。
    """

    is_active: bool | None = None
    refresh_interval_sec: int | None = Field(default=None, ge=30, le=3600)


class BatchDeleteIn(BaseModel):
    """批量删除请求体。``run_ids`` 最多 100 条，防止单次请求删太多。"""

    run_ids: list[str] = Field(..., min_length=1, max_length=100)


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


# ---------------------------- 分页 ----------------------------


class PageResponse(BaseModel):
    """分页包装：list 端点返回 ``{items, next_cursor, has_more, total}``。

    - ``next_cursor``：下一页的游标，客户端下次请求传 ``?cursor=xxx``；
      为空表示已到末页。
    - ``total``：可选总数（某些端点查询成本高时不返回，为 -1）。
    """

    items: list[Any]
    next_cursor: str | None = None
    has_more: bool = False
    total: int = -1


def paginate(items: list[Any], cursor_field: str = "id", limit: int = 50) -> dict:
    """对已排序列表做游标分页，返回 ``PageResponse`` 结构的 dict。

    调用方应保证 ``items`` 已按 ``cursor_field`` 排序；本函数只做切片。
    ``cursor`` 取最后一项的 ``cursor_field`` 值；实际游标由路由层解析。
    """
    page = items[:limit]
    has_more = len(items) > limit
    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        if isinstance(last, dict):
            next_cursor = str(last.get(cursor_field, ""))
        elif hasattr(last, cursor_field):
            next_cursor = str(getattr(last, cursor_field, ""))
    return {
        "items": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total": len(items),
    }
