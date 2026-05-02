"""KDJ 金叉强度因子。

定义：``factor = K - D``。

直觉：
- K 是 RSV 的 1 阶 EMA（alpha=1/3），D 是 K 的 2 阶 EMA——D 比 K 滞后；
- 价格触底反弹时 RSV 先抬升 → K 先涨 → K-D 转正（金叉强度）；
- 价格见顶回落时 K 先跌 → K-D 转负（死叉）。

预期方向：趋势（与 kdj_j_oversold 反向——这是故意的，用来横比 KDJ 在目标
universe 上到底是反转信号有效还是趋势跟随有效）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj, load_hlc


class KdjCross(BaseFactor):
    factor_id = "kdj_cross"
    display_name = "KDJ 金叉强度"
    category = "oscillator"
    description = "factor = K - D；金叉越强（K 高于 D）看多，趋势跟随信号。"
    hypothesis = "K 上穿 D 线（金叉）指示短期动能转强，趋势跟随信号。"
    params_schema = {
        "n": {
            "type": "int",
            "default": 9,
            "min": 3,
            "max": 60,
            "desc": "RSV 窗口（交易日）",
        }
    }
    default_params = {"n": 9}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        return self._calc_warmup(n * 3)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels
        k, d, _ = compute_kdj(high, low, close, n=n)
        return (k - d).loc[ctx.start_date:]
