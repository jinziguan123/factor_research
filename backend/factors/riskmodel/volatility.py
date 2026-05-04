from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Volatility60d(BaseFactor):
    factor_id = "volatility_60d"
    display_name = "波动因子"
    category = "riskmodel"
    description = "Barra-style Volatility: std of daily returns over trailing 60 days"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return self._calc_warmup(60 + 1)

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
        ret = close.pct_change(fill_method=None)
        result = ret.rolling(window=60, min_periods=20).std()
        return result.loc[ctx.start_date:]
