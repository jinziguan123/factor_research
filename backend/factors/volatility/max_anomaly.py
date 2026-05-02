"""MaxAnomaly：MAX 异象（彩票股反转）。

公式：``factor_t = -1 * rolling_max(close.pct_change(), window)``。

直觉：Bali-Cakici-Whitelaw (RFS 2011) "Maxing Out: Stocks as Lotteries" 提出
MAX 异象——过去 N 日单日最高收益（"彩票特征"）越大的股票未来表现越差。
A 股 Han-Hu-Yang (PBFJ 2018) 等多篇论文确认有效。Negate 后大值 → 低 MAX → 长仓信号。

与 IVOL 的区别（同样基于 returns）：IVOL 是 60 日**残差波动**度量"持续紊乱程度"，
MAX 是 20 日**单日最大**度量"瞬时极端程度"，两者在因子空间正交。

预热 = ``int(window * 1.5) + 10`` 自然日（pct_change 1 + rolling window-1 + 节假 buffer）。

NaN 行为：``rolling(window).max()`` 默认 ``min_periods=window``，窗口内出现任意
NaN（停牌 / 上市未满 window 日）→ 该日因子 NaN；**NaN 影响范围 = window 个交易日**。
``pct_change(fill_method=None)`` 仅保证当日 ret 不被 ffill 伪造，不消除 rolling
窗口的 NaN 传染性。下游 evaluate 自带 cross-section valid mask，会自动剔除 NaN
位置，不污染信号。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class MaxAnomaly(BaseFactor):
    factor_id = "max_anomaly"
    display_name = "MAX 异象（-rolling_max(returns, 20)）"
    category = "volatility"
    description = "过去 20 日单日最高收益取负——高 MAX 股票（彩票特征）未来收益更低。"
    hypothesis = "Bali-Cakici-Whitelaw 2011 / Han-Hu-Yang 2018：高 MAX 股未来跑输；取负使高分→长仓。"
    params_schema: dict = {
        "window": {"type": "int", "default": 20, "min": 5, "max": 60,
                   "desc": "rolling max 窗口（交易日，20 ≈ 1 月）"},
    }
    default_params: dict = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        factor = -ret.rolling(window).max()
        return factor.loc[ctx.start_date :]
