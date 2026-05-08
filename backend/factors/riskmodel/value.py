from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class ValueFactor(BaseFactor):
    factor_id = "value_ep"
    display_name = "价值因子"
    category = "riskmodel"
    description = "Barra-style Value: 1 / PB"
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 1

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        data_start = ctx.start_date.date()
        data_end = ctx.end_date.date()
        pb = ctx.data.load_pb(ctx.symbols, data_start, data_end)
        if pb.empty:
            return pd.DataFrame()
        pb = pb.astype(float).reindex(index=pd.DatetimeIndex(sorted(pb.index)))
        result = 1.0 / pb.replace(0.0, np.nan)
        return result.loc[ctx.start_date:]
