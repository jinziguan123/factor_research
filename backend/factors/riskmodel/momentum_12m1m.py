from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Momentum12m1m(BaseFactor):
    factor_id = "momentum_12m1m"
    display_name = "动量因子"
    category = "riskmodel"
    description = "Barra-style Momentum: cumulative return over past 12 months, skipping the most recent month"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return self._calc_warmup(250 + 21)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        data_end = ctx.end_date.date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, data_end, freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        close = close.astype(float).sort_index()
        # 12-month return skipping 1 month: p(t-21) / p(t-252) - 1
        result = close.shift(21) / close.shift(252) - 1.0
        return result.loc[ctx.start_date:]
