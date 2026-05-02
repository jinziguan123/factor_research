"""Amihud 非流动性因子。

公式：``factor_t = -1 * rolling_mean(|return_t| / dollar_volume_t, window)``。

直觉：Amihud (JFM 2002) 提出非流动性溢价——流动性越差的股票，预期收益越高。
A 股大量研究（如 Liu-Stambaugh-Yuan 2019）验证了非流动性的定价能力。
取负后高流动性 → 高分 → 长仓（与低风险/质量因子方向对齐）。

dollar_volume 用 ``close * volume / 1e6``（百万元）做代理，量纲使 Amihud
值在 1e-5 ~ 1e-3 范围内。

预热 = ``int(window * 1.5) + 10`` 自然日。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class AmihudIlliquidity(BaseFactor):
    factor_id = "amihud_illiquidity"
    display_name = "Amihud 非流动性（-|ret|/dollar_vol_mean）"
    category = "volume"
    description = "Amihud (2002) 非流动性度量取负——高流动性看多，方向与质量/低风险因子对齐。"
    hypothesis = "流动性越差预期收益越高（非流动性溢价）——Amihud 2002 / Liu-Stambaugh-Yuan 2019。"
    params_schema: dict = {
        "window": {"type": "int", "default": 20, "min": 5, "max": 252,
                   "desc": "滚动均值窗口（交易日）"},
    }
    default_params: dict = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        volume = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="volume", adjust="none",
        )
        if close is None or close.empty or volume.empty:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None).abs()
        dollar_vol = close * volume / 1e6
        dollar_vol = dollar_vol.where(dollar_vol > 0)
        daily_illiq = ret / dollar_vol
        factor = -daily_illiq.rolling(window).mean()
        return factor.loc[ctx.start_date:]
