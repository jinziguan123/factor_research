"""Reversal-N（N 日反转）因子。

定义：``factor_t = -(close_t / close_{t-N} - 1)``。

直觉：过去 N 日**跌得多**的股票更有回升空间，取负号后因子值越高越看多。
预热期 = ``int(N * 1.5) + 10`` 自然日：
- 周末 / 节假日会让 N 个交易日对应更长的自然日（约 1.4 倍）；
- 再加 10 天兜住春节 / 国庆等长假；
- 相比旧公式 ``N + 5``，新公式在 N≥20 时都能稳定覆盖。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class ReversalN(BaseFactor):
    factor_id = "reversal_n"
    display_name = "N 日反转"
    category = "reversal"
    description = "过去 N 日累计收益的相反数，反映短期过跌反弹预期。"
    hypothesis = "短期过度反应与流动性冲击驱动价格偏离，未来向均值回归——Jegadeesh 1990, Lehmann 1990。"
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
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        factor = -close.pct_change(window, fill_method=None)
        return factor.loc[ctx.start_date :]
