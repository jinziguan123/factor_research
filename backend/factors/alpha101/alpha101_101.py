"""WorldQuant Alpha 101 #101（K 线归一化涨幅）。

公式：``(close - open) / (high - low + epsilon)``，epsilon=1e-3 防分母 0。

直觉：日内"实体相对幅度"。≈ 1 表示当日强势收高，≈ -1 表示弱势收低；
归一化后跨股票可比。最简单的瞬时形态因子，无 rolling / lag。

预热 = 0（无 lag）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_101(BaseFactor):
    factor_id = "alpha101_101"
    display_name = "Alpha101 #101（K 线归一化涨幅）"
    category = "alpha101"
    description = "(close-open) / (high-low+epsilon)；日内归一化涨幅。"
    hypothesis = "当日实体相对全幅度的占比反映多空力道；高分 → 强势收高。"
    params_schema: dict = {
        "epsilon": {"type": "float", "default": 1e-3, "min": 1e-6, "max": 1.0,
                    "desc": "防分母 0 的常数项"},
    }
    default_params: dict = {"epsilon": 1e-3}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        eps = float(params.get("epsilon", self.default_params["epsilon"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        open_ = ctx.data.load_panel(ctx.symbols, s, e, field="open", adjust="qfq")
        close = ctx.data.load_panel(ctx.symbols, s, e, field="close", adjust="qfq")
        high = ctx.data.load_panel(ctx.symbols, s, e, field="high", adjust="qfq")
        low = ctx.data.load_panel(ctx.symbols, s, e, field="low", adjust="qfq")
        if any(p.empty for p in [open_, close, high, low]):
            return pd.DataFrame()
        # 4 路 outer align（防 column 漂移）
        open_, close = open_.align(close, join="outer")
        high, low = high.align(low, join="outer")
        open_, high = open_.align(high, join="outer")
        close, low = close.align(low, join="outer")
        factor = (close - open_) / (high - low + eps)
        return factor.loc[ctx.start_date :]
