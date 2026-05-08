from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class SizeFactor(BaseFactor):
    factor_id = "size_mv"
    display_name = "规模因子"
    category = "riskmodel"
    description = "Barra-style Size: log(total market cap)"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 1

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        data_start = ctx.start_date.date()
        data_end = ctx.end_date.date()
        mktcap = ctx.data.load_market_cap(ctx.symbols, data_start, data_end)
        if mktcap.empty:
            return pd.DataFrame()
        mktcap = mktcap.astype(float).reindex(index=pd.DatetimeIndex(sorted(mktcap.index)))
        result = np.log(mktcap.replace(0.0, np.nan))
        return result.loc[ctx.start_date:]
