"""WorldQuant Alpha 101 #6（价量负相关，反转信号）。

公式：``-1 * correlation(open, volume, window)``，window 默认 10。

直觉：开盘价与成交量近 N 日 rolling 相关——负相关越深表示"放量低开/缩量
高开"，业界视作反转信号；取负后高分股 → 弱反转预期。

预热 = ``int(window * 1.5) + 5`` 自然日。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_6(BaseFactor):
    factor_id = "alpha101_6"
    display_name = "Alpha101 #6（价量负相关）"
    category = "alpha101"
    description = "-1 * correlation(open, volume, 10)；价量负相关反转信号。"
    hypothesis = "近 10 日 open 与 volume 负相关越深 → 反转预期越强；取负使高分股偏多头。"
    params_schema: dict = {
        "window": {"type": "int", "default": 10, "min": 3, "max": 60,
                   "desc": "rolling correlation 窗口"},
    }
    default_params: dict = {"window": 10}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window, buffer_days=5)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        open_ = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="open", adjust="qfq",
        )
        volume = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="volume", adjust="none",
        )
        if open_.empty or volume.empty:
            return pd.DataFrame()
        # outer align 防 column 漂移
        open_, volume = open_.align(volume, join="outer")
        # rolling.corr 是 element-wise；axis 默认 row-wise NaN-aware
        corr = open_.rolling(window).corr(volume)
        return (-corr).loc[ctx.start_date :]
