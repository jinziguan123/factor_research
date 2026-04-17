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
        # 1.5× 折算交易日到自然日 + 10 天长假 buffer。
        return int(window * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols,
            data_start,
            ctx.end_date.date(),
            freq="1d",
            field="close",
            adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        # sqrt(252) 是年化系数（A 股约 252 个交易日 / 年）。
        # fill_method=None：停牌日 NaN 不做 ffill，避免 pad=0 把 rolling std
        # 拉低成伪"低波动"。pandas 2.x 默认即 None，这里显式以消除 1.x warning。
        factor = close.pct_change(fill_method=None).rolling(window).std() * np.sqrt(252)
        return factor.loc[ctx.start_date :]
