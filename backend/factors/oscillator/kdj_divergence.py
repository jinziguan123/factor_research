"""KDJ 价-J 底背离强度因子。

定义：
    j_rebound = J - rolling_min(J, lookback)           # J 已从近期最低反弹的距离
    p_rebound = close - rolling_min(close, lookback)   # 价格已从近期最低反弹的距离
    scale     = rolling_std(J, lookback) /             # 两边量级归一
                rolling_std(close, lookback)
    factor    = j_rebound - scale * p_rebound

直觉：
- 底背离 = "J 先反弹 + 价格滞后"，此时 j_rebound 大而 p_rebound 小 → factor > 0；
- 顶背离 = "J 先下跌 + 价格惯性新高"，反过来 → factor < 0；
- 如果两者同步（无背离），factor ≈ 0。

为什么用 rolling_min 近似而不找 local extrema：
- 真找局部极值要判 window 内的"反转点"，实现又脏又慢，收敛也敏感；
- rolling_min 虽然在"一路下跌（min 永远是最新那根）"时给出 0、不算强信号，
  但在 V 型底 / W 型底时能正确捕捉——这正是我们关心的场景。

为什么要 scale：
- close 的量级是价格（几元~几百元）、J 的量级是 0-100，直接相减会被 p_rebound
  主导；用两边 rolling_std 的比值做横截面归一，让两者在同一量纲下可比。
- 当 rolling_std(close) 极小（新股 / 停牌后），用 scale=1 兜底避免除零爆 inf。

预期方向：反转（底背离看多）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj, load_hlc


class KdjDivergence(BaseFactor):
    factor_id = "kdj_divergence"
    display_name = "价-J 底背离强度"
    category = "oscillator"
    description = (
        "(J - rolling_min(J, lb)) - scale * (close - rolling_min(close, lb))；"
        "J 已反弹距离 - 价格已反弹距离（归一化后），正值=底背离看多。"
    )
    params_schema = {
        "n": {
            "type": "int", "default": 9, "min": 3, "max": 60,
            "desc": "RSV 窗口（交易日）",
        },
        "lookback": {
            "type": "int", "default": 20, "min": 10, "max": 60,
            "desc": "背离回看窗口（交易日）",
        },
    }
    default_params = {"n": 9, "lookback": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        return int((n * 3 + lookback) * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        lookback = int(params.get("lookback", self.default_params["lookback"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels
        _, _, j = compute_kdj(high, low, close, n=n)

        j_rebound = j - j.rolling(lookback, min_periods=lookback).min()
        p_rebound = close - close.rolling(lookback, min_periods=lookback).min()
        j_std = j.rolling(lookback, min_periods=lookback).std()
        p_std = close.rolling(lookback, min_periods=lookback).std()
        # p_std 极小时（< 1e-9 ≈ 完全横盘）用 scale=1 兜底，避免 inf。
        scale = (j_std / p_std.where(p_std > 1e-9)).fillna(1.0)
        factor = j_rebound - scale * p_rebound
        return factor.loc[ctx.start_date:]
