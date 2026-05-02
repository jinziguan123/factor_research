"""Revenue Growth（营收增长率）PIT 因子。

公式：``factor_t = (mb_revenue_t - mb_revenue_{t - lag}) / abs(mb_revenue_{t - lag})``，
lag 默认 252 交易日（约 1 年）。mb_revenue 是主营业务收入（PIT, ffill 到日频）。

直觉：营收持续增长的公司未来超额收益更高——成长因子（Growth）的核心构件。
与动量因子不同：营收增长是基于基本面的，而非量价。

预热 = 0（PIT 自带左 seed；shift 后前 lag 天 NaN）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RevenueGrowth(BaseFactor):
    factor_id = "revenue_growth"
    display_name = "营收增长率（mb_revenue YoY, PIT）"
    category = "fundamental"
    description = "(mb_revenue_t - mb_revenue_{t-lag}) / |mb_revenue_{t-lag}|，PIT ffill 到日频。"
    hypothesis = "营收持续增长反映企业扩张能力——Fama-French 成长因子的核心构件。"
    params_schema: dict = {
        "yoy_lag": {"type": "int", "default": 252, "min": 200, "max": 260,
                    "desc": "同比 lag（交易日，252 ≈ 1 年）"},
    }
    default_params: dict = {"yoy_lag": 252}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        lag = int(params.get("yoy_lag", self.default_params["yoy_lag"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        panel = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="mb_revenue")
        if panel.empty:
            return pd.DataFrame()
        shifted = panel.shift(lag)
        return ((panel - shifted) / shifted.abs().clip(lower=1e-9)).loc[ctx.start_date:]
