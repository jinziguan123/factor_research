"""Earnings Yield (EP)：盈利收益率，市盈率倒数。

公式：``factor_t = eps_ttm_t (PIT, ffill) / close_t (qfq)``。

直觉：Fama-French 价值因子核心。值越大 → 估值越便宜 → 长仓信号。
A 股大盘股稳定有效；小盘可能反向，下游 LightGBM 学非线性可处理。

预热 = 0（PIT 数据自带左 seed）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class EarningsYield(BaseFactor):
    factor_id = "earnings_yield"
    display_name = "盈利收益率 EP（eps_ttm/close）"
    category = "fundamental"
    description = "eps_ttm（PIT, ffill 到日频） / close（qfq），即 1/PE。"
    hypothesis = "估值越便宜（EP 越高）长期超额收益越高（价值溢价）。"
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        s, e = ctx.start_date.date(), ctx.end_date.date()
        eps = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="eps_ttm")
        close = ctx.data.load_panel(ctx.symbols, s, e, field="close", adjust="qfq")
        if eps.empty or close.empty:
            return pd.DataFrame()
        eps, close = eps.align(close, join="inner")
        return (eps / close).loc[ctx.start_date :]
