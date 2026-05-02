"""KDJ K 自身分位反转因子。

定义：``factor = -rolling_pct_rank(K, lookback)``；K 值在过去 lookback 日内的
百分位（0-1），取负号后分位越低因子越大（越看多）。

设计动机：直接用 K 做横截面因子有一个根本问题——A 股的 K=30 可能是超卖反弹
机会，B 股的 K=30 可能只是下跌刚开始。K 的绝对值跨股票不可比。改成"K 在
自身过去 lookback 日的分位"后，每只股票的分位值都被归一化到 [0,1]，横截面
比较才站得住脚。

为什么用 pct_rank 不用 z-score：K 是 bounded 量（0-100 附近），分布偏态且尾部
被 clip，z-score 对这种分布敏感；pct_rank 只看排序，对分布形状不敏感。

预期方向：反转（分位低越看多）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj, load_hlc


def _pct_rank_last(window: np.ndarray) -> float:
    """窗口内"当前值在所有有效样本中的百分位"（0-1）。

    rolling.apply(raw=True) 会传入 1-D ndarray，这里用 numpy 向量化：
    - 窗口最后一个值就是"当前值"；
    - 严格小于的数 + 等于的数 / 2（average rank 近似，避免并列导致尖锐跳变）
      然后除以有效样本数。
    比 pd.Series.rank(method='average', pct=True).iloc[-1] 快约一个数量级。
    """
    if np.isnan(window[-1]):
        return np.nan
    valid = window[~np.isnan(window)]
    if valid.size < 2:
        return np.nan
    last = window[-1]
    gt = (valid < last).sum()
    eq = (valid == last).sum()
    return (gt + 0.5 * eq) / valid.size


class KdjKPctRev(BaseFactor):
    factor_id = "kdj_k_pct_rev"
    display_name = "K 自身分位反转"
    category = "oscillator"
    description = (
        "factor = -rolling_pct_rank(K, lookback)；K 在自身过去 lookback 日分位的"
        "相反数，分位越低越看多，消除 K 绝对值跨股不可比。"
    )
    hypothesis = "K 值在自身历史分位极低时超卖——用分位消除 K 绝对值跨股不可比问题，反转信号。"
    params_schema = {
        "n": {
            "type": "int", "default": 9, "min": 3, "max": 60,
            "desc": "RSV 窗口（交易日）",
        },
        "lookback": {
            "type": "int", "default": 60, "min": 10, "max": 252,
            "desc": "K 自身分位回看窗口（交易日）",
        },
    }
    default_params = {"n": 9, "lookback": 60}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        return self._calc_warmup(n * 3 + lookback)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels
        k, _, _ = compute_kdj(high, low, close, n=n)
        pct_rank = k.rolling(lookback, min_periods=lookback).apply(
            _pct_rank_last, raw=True
        )
        return (-pct_rank).loc[ctx.start_date:]
