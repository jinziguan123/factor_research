"""KDJ 超卖阈值 hinge 因子。

定义：``factor = max(0, threshold - K)``；K < threshold（典型 20）时给正值，
否则 0。和 kdj_j_oversold 的连续版本相比，hinge 让"不在超卖区的股票"一视同仁（都
给 0），只在超卖段激活——更接近技术分析手册的离散规则，用来测"非连续信号"比
"连续水平信号"在横截面上是否更有效。

预期方向：反转（只在 K < threshold 时激活）。

结构特点：分五组回测时可能有大量 0 挤在低分组，qcut 可能报"bins 不足"警告，
属正常现象（因子本身就是稀疏的）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj, load_hlc


class KdjOversoldHinge(BaseFactor):
    factor_id = "kdj_oversold_hinge"
    display_name = "KDJ 超卖 hinge"
    category = "oscillator"
    description = "factor = max(0, threshold - K)；仅在 K 低于阈值（超卖区）给正分。"
    hypothesis = "K 低于阈值（如 20）时激活超卖反弹信号——经典技术分析离散规则，反转信号。"
    params_schema = {
        "n": {
            "type": "int", "default": 9, "min": 3, "max": 60,
            "desc": "RSV 窗口（交易日）",
        },
        "threshold": {
            "type": "int", "default": 20, "min": 5, "max": 40,
            "desc": "K 超卖阈值（低于此值才激活）",
        },
    }
    default_params = {"n": 9, "threshold": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        return self._calc_warmup(n * 3)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        threshold = float(params.get("threshold", self.default_params["threshold"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels
        k, _, _ = compute_kdj(high, low, close, n=n)
        # np.maximum 会把 NaN 吃掉变 0；先算 hinge 再用 k 的 NaN mask 恢复，
        # 保持与平台其它因子"停牌段输出 NaN"的一致行为。
        hinge = pd.DataFrame(
            np.maximum(0.0, threshold - k.values),
            index=k.index, columns=k.columns,
        )
        return hinge.where(~k.isna()).loc[ctx.start_date:]
