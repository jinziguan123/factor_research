"""Reversal-N（N 日反转）因子。

定义：``factor_t = -(close_t / close_{t-N} - 1)``。

直觉：过去 N 日**跌得多**的股票更有回升空间，取负号后因子值越高越看多。
预热期 = ``N + 5`` 自然日（+5 为周末 / 假期 buffer，确保至少拿到 N 个交易日）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class ReversalN(BaseFactor):
    factor_id = "reversal_n"
    display_name = "N 日反转"
    category = "reversal"
    description = "过去 N 日累计收益的相反数，反映短期过跌反弹预期。"
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 2,
            "max": 252,
            "desc": "回看窗口（交易日）",
        }
    }
    default_params = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        # +5 自然日 buffer：抵消 pct_change 需要 window 个"前值"，
        # 再加周末 / 节假日导致 window 交易日 > window 自然日的空窗。
        return window + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        # 向左多取 warmup 天保证 pct_change 在 start_date 当天就有值。
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
        factor = -close.pct_change(window)
        # 切回 [start_date, end_date]；若用户 start_date 早于实际数据首日，
        # .loc 会只返回数据覆盖的日期，行为仍然确定。
        return factor.loc[ctx.start_date :]
