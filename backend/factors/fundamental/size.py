"""Size（规模）因子：市值代理。

公式：``factor_t = -1 * log(close_t)``。

直觉：Fama-French 三因子模型的 SMB（Small Minus Big）——
小市值股票长期来看风险调整后收益高于大市值（规模溢价）。
取负后小市值 → 高分 → 长仓信号。

注意：此处用 close 价格的对数作为市值的**粗糙代理**。A 股市场上低价股
与小市值高度相关（秩相关系数通常在 0.6-0.8）。未来接入真正的总市值数据后
可替换为 ``log(market_cap)``，方向不变。

预热 = 0（瞬时因子，无滚动窗口）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Size(BaseFactor):
    factor_id = "size"
    display_name = "规模因子（-log(close) 市值代理）"
    category = "fundamental"
    description = "-log(close)，小市值 → 高分 → 长仓。Fama-French SMB 的代理。"
    hypothesis = "小市值股票长期超额收益更高——规模溢价（Banz 1981, Fama-French 1993）。"
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        s, e = ctx.start_date.date(), ctx.end_date.date()
        close = ctx.data.load_panel(ctx.symbols, s, e, field="close", adjust="qfq")
        if close.empty:
            return pd.DataFrame()
        return (-np.log(close.clip(lower=1e-6))).loc[ctx.start_date:]
