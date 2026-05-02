"""已实现波动率（Realized Volatility）因子。

定义：``factor_t = std(pct_change, rolling=window) * sqrt(252)``，
即过去 window 日日收益率的标准差并年化。

直觉：高波动通常隐含高风险溢价（或高不确定性）；是低波动异象的输入。
预热期 = ``int(window * 1.5) + 10`` 自然日（交易日 → 自然日折算 + 长假 buffer）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RealizedVol(BaseFactor):
    factor_id = "realized_vol"
    display_name = "已实现波动率（年化）"
    category = "volatility"
    description = "过去 window 日日收益率标准差的年化值。"
    hypothesis = "高波动隐含高风险溢价或不确定性——低波动异象（Ang-Hodrick-Xing-Zhang 2006）。"
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 5,
            "max": 252,
            "desc": "滚动窗口（交易日）",
        }
    }
    default_params = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        factor = close.pct_change(fill_method=None).rolling(window).std() * np.sqrt(252)
        return factor.loc[ctx.start_date :]
