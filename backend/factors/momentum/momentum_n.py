"""Momentum-N（动量-N，跳过近 skip 天）因子。

定义：``factor_t = close_{t-skip} / close_{t-skip-window} - 1``。

直觉：观察"更早的 window 天"涨幅，跳过最近 ``skip`` 天以避开短期反转噪声。
典型参数是 12-1：window=252、skip=20（月），此处 MVP 默认 120-5。
预热期 = ``window + skip + 5`` 自然日。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class MomentumN(BaseFactor):
    factor_id = "momentum_n"
    display_name = "N 日动量（跳过近 skip 日）"
    category = "momentum"
    description = "跳过近 skip 日，计算再往前 window 日的涨幅。"
    params_schema = {
        "window": {
            "type": "int",
            "default": 120,
            "min": 5,
            "max": 504,
            "desc": "动量计算窗口（交易日）",
        },
        "skip": {
            "type": "int",
            "default": 5,
            "min": 0,
            "max": 60,
            "desc": "跳过最近 N 日（避开短期反转噪声）",
        },
    }
    default_params = {"window": 120, "skip": 5}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        skip = int(params.get("skip", self.default_params["skip"]))
        return window + skip + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        skip = int(params.get("skip", self.default_params["skip"]))
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
        # 基准价：skip 天前；参照价：skip + window 天前。
        factor = close.shift(skip) / close.shift(skip + window) - 1
        return factor.loc[ctx.start_date :]
