"""Momentum-N（动量-N，跳过近 skip 天）因子。

定义：``factor_t = close_{t-skip} / close_{t-skip-window} - 1``。

直觉：观察"更早的 window 天"涨幅，跳过最近 ``skip`` 天以避开短期反转噪声。
典型参数是 12-1：window=252、skip=20（月），此处 MVP 默认 120-5。
预热期 = ``int((window + skip) * 1.5) + 10`` 自然日（交易日 → 自然日折算 +
长假 buffer）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class MomentumN(BaseFactor):
    factor_id = "momentum_n"
    display_name = "N 日动量（跳过近 skip 日）"
    category = "momentum"
    description = "跳过近 skip 日，计算再往前 window 日的涨幅。"
    hypothesis = "中期赢家续强、输家续弱——Jegadeesh-Titman 1993 动量效应，跳过近期反转噪声。"
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
        return self._calc_warmup(window + skip)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        skip = int(params.get("skip", self.default_params["skip"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        factor = close.shift(skip) / close.shift(skip + window) - 1
        return factor.loc[ctx.start_date :]
