"""MAX 异象因子单测：-rolling_max(returns, 20)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.volatility.max_anomaly import MaxAnomaly


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None: return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_max_anomaly_happy_path():
    """因子 = -rolling_max(close.pct_change(), 20)，全表与手算对齐。"""
    n = 40
    idx = pd.bdate_range("2024-01-02", periods=n)
    symbols = ["A", "B", "C"]
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    factor = MaxAnomaly().compute(ctx, {})
    # 手算同口径
    ret = close.pct_change(fill_method=None)
    expected = -ret.rolling(20).max()
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_names=False,
    )


def test_max_anomaly_nan_robust():
    n = 40
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(1)
    symbols = ["A", "B"]
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    close.iloc[15:18, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    factor = MaxAnomaly().compute(ctx, {})
    assert not factor.empty
    # B 列末段非 NaN
    assert factor["B"].iloc[-5:].notna().all()
    # 验证 NaN 传染窗口 = 20（rolling window）：
    # close.iloc[15:18, 0] = NaN（3 天）→ pct_change 在 idx[15..18] NaN（4 天，因
    # idx[18] 的 ret 用 close[17]=NaN）→ rolling(20) 不含 NaN 要 t-19 > 18，即 t >= 38。
    # factor 从 ctx.start_date=idx[25] 开始（factor.iloc[0]）→ factor.iloc[13]=idx[38] 是第一恢复日。
    a_factor = factor["A"]
    assert a_factor.iloc[:13].isna().all(), (
        "rolling(20) 窗口含 NaN 期间 A 列应全 NaN（factor 起 13 日）"
    )
    assert a_factor.iloc[13:].notna().all(), (
        "rolling(20) 窗口离开 NaN 段后 A 列应恢复"
    )


def test_max_anomaly_col_order_invariance():
    n = 40
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(2)
    symbols = ["A", "B", "C"]
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx_a = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    fa = MaxAnomaly().compute(ctx_a, {})

    shuffled = ["C", "A", "B"]
    ctx_s = FactorContext(
        data=FakeDataService(panels={"close": close[shuffled]}), symbols=shuffled,
        start_date=idx[25], end_date=idx[-1], warmup_days=25,
    )
    fs = MaxAnomaly().compute(ctx_s, {})

    target = fa.index[5]
    for c in symbols:
        a, s = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a):
            assert pd.isna(s)
        else:
            assert abs(a - s) < 1e-12
