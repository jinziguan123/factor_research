from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Liquidity20d(BaseFactor):
    factor_id = "liquidity_20d"
    display_name = "流动性因子"
    category = "riskmodel"
    description = "Barra-style Liquidity: average daily turnover over trailing 20 days"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return self._calc_warmup(20 + 1)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        data_end = ctx.end_date.date()

        volume = ctx.data.load_panel(
            ctx.symbols, data_start, data_end, freq="1d", field="volume", adjust="none",
        )
        mktcap = ctx.data.load_market_cap(ctx.symbols, data_start, data_end)
        if volume.empty or mktcap.empty:
            return pd.DataFrame()

        volume = volume.astype(float).sort_index()
        mktcap = mktcap.reindex(index=volume.index, columns=volume.columns)
        turnover = volume / mktcap.replace(0.0, np.nan)
        result = turnover.rolling(window=20, min_periods=10).mean()
        return result.loc[ctx.start_date:]
