"""Net Profit Margin（净利润率）PIT 因子。

公式：``factor_t = np_margin_t``（PIT 季报 ffill 到日频）。

直觉：净利润率反映企业将收入转化为利润的效率。高利润率意味着竞争优势
（护城河），长期看多。AQR Quality 因子"Profitability"维度的基础构件。

预热 = 0（PIT 数据自带左 seed）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class NetProfitMargin(BaseFactor):
    factor_id = "net_profit_margin"
    display_name = "净利润率（np_margin, PIT）"
    category = "fundamental"
    description = "PIT np_margin（净利润/收入），ffill 到日频。"
    hypothesis = "高净利润率反映竞争优势与定价权——AQR Quality 中 Profitability 维度。"
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        s, e = ctx.start_date.date(), ctx.end_date.date()
        panel = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="np_margin")
        if panel.empty:
            return pd.DataFrame()
        return panel.loc[ctx.start_date:]
