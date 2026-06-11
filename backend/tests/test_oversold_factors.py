"""移植自通达信的三个超卖摆动因子单测（风景线 / 天地绝杀 / 强承接吸筹）。

用 conftest 的 factor_context（FakeDataService）喂 high/low/close 宽表，验证：
- compute 返回非空宽表、index 从 start_date 起、列=标的；
- 输出无 inf、warmup 之后有有限值；
- 方向：构造一段"先跌到底再反弹"的序列，底部那天因子值应明显高于高位那天
  （factor=-摆动值，越超卖越大）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.oscillator.scenery_risk_oversold import SceneryRiskOversold
from backend.factors.oscillator.tiandi_trend_oversold import TiandiTrendOversold
from backend.factors.oscillator.accumulation_vr3_oversold import AccumulationVr3Oversold

_FACTORS = [SceneryRiskOversold, TiandiTrendOversold, AccumulationVr3Oversold]


def _panels():
    """输出窗口(2024-01-10起)内构造一个清晰的 V：先跌到底(01-31)再反弹。

    warmup 段保持高位平台，让 V 的左肩处于区间高位、谷底处于区间低位，
    方便校验"因子在谷底最高"。high/low 包住 close。
    """
    dates = pd.date_range("2023-06-01", "2024-02-28", freq="B")
    n = len(dates)
    close = pd.Series(60.0, index=dates)
    dec = (dates >= "2024-01-10") & (dates <= "2024-01-31")
    rec = dates > "2024-01-31"
    close[dec] = np.linspace(60, 40, int(dec.sum()))   # 跌到谷底 ~40
    close[rec] = np.linspace(40, 55, int(rec.sum()))   # 反弹到 ~55
    close = close + np.random.RandomState(0).normal(0, 0.2, n)  # 加噪避免平台 range=0
    close = pd.DataFrame({"AAA.SZ": close, "BBB.SZ": close * 1.1 + 3})
    high = close * 1.015
    low = close * 0.985
    return {"close": close, "high": high, "low": low}


def test_factors_compute_finite_wide_table(factor_context):
    p = _panels()
    ctx = factor_context(close=p["close"], high=p["high"], low=p["low"])
    for F in _FACTORS:
        out = F().compute(ctx, {})
        assert not out.empty, f"{F.factor_id} 返回空"
        assert list(out.columns) == ["AAA.SZ", "BBB.SZ"], f"{F.factor_id} 列不对"
        assert out.index.min() >= ctx.start_date, f"{F.factor_id} 起点早于 start_date"
        # 无 inf；且至少有有限值
        assert not np.isinf(out.to_numpy(dtype=float)).any(), f"{F.factor_id} 出现 inf"
        assert np.isfinite(out.to_numpy(dtype=float)).any(), f"{F.factor_id} 全是 NaN"


def test_factor_higher_when_more_oversold(factor_context):
    # factor = -摆动值：价格在区间低位时摆动值低 → 因子高。
    p = _panels()
    ctx = factor_context(close=p["close"], high=p["high"], low=p["low"])
    for F in _FACTORS:
        out = F().compute(ctx, {})["AAA.SZ"].dropna()
        assert len(out) > 5
        # 因子最大值出现在序列里价格相对低位的时段（粗校验方向不反）
        close_in_window = p["close"]["AAA.SZ"].loc[out.index]
        top_factor_day = out.idxmax()
        # 因子最高那天的收盘，应低于窗口内中位收盘（即处于相对低位）
        assert close_in_window.loc[top_factor_day] <= close_in_window.median()


def test_required_warmup_positive():
    for F in _FACTORS:
        assert F().required_warmup({}) > 0
