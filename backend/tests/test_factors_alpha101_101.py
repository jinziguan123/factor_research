"""Alpha101 #101 因子单测：(close - open) / (high - low + epsilon)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_101 import Alpha101_101


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def _biz_index(n, start="2024-01-02"):
    return pd.bdate_range(start=start, periods=n)


def _make_ohlc(n, symbols, seed):
    rng = np.random.default_rng(seed)
    idx = _biz_index(n)
    open_ = pd.DataFrame({s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx)
    close = open_ + rng.normal(0, 0.2, (n, len(symbols)))
    high = pd.concat([open_, close]).groupby(level=0).max() + rng.uniform(0.05, 0.2, (n, len(symbols)))
    low = pd.concat([open_, close]).groupby(level=0).min() - rng.uniform(0.05, 0.2, (n, len(symbols)))
    return {"open": open_, "close": close, "high": high, "low": low}


def test_alpha101_101_happy_path():
    """因子 = (close-open) / (high-low+ε)，与手算对齐（全表对比）。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_ohlc(n, symbols, seed=0)
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[0], end_date=panels["close"].index[-1],
        warmup_days=0,
    )
    factor = Alpha101_101().compute(ctx, {})
    eps = 1e-3
    expected = (panels["close"] - panels["open"]) / (panels["high"] - panels["low"] + eps)
    # 全表对比 catch 切片 off-by-one + 列对齐错位（比单行断言覆盖面更大）
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_names=False,
    )


def test_alpha101_101_nan_robust():
    n = 30
    symbols = ["A", "B"]
    panels = _make_ohlc(n, symbols, seed=1)
    for k in panels:
        panels[k].iloc[5:8, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[0], end_date=panels["close"].index[-1],
        warmup_days=0,
    )
    factor = Alpha101_101().compute(ctx, {})
    assert not factor.empty
    assert factor["A"].iloc[5:8].isna().all()
    assert factor["B"].notna().all()


def test_alpha101_101_col_order_invariance():
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_ohlc(n, symbols, seed=2)
    ctx_a = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[0], end_date=panels["close"].index[-1],
        warmup_days=0,
    )
    fa = Alpha101_101().compute(ctx_a, {})

    shuffled = ["B", "C", "A"]
    panels_s = {k: v[shuffled] for k, v in panels.items()}
    ctx_s = FactorContext(
        data=FakeDataService(panels=panels_s), symbols=shuffled,
        start_date=panels_s["close"].index[0], end_date=panels_s["close"].index[-1],
        warmup_days=0,
    )
    fs = Alpha101_101().compute(ctx_s, {})

    target = fa.index[10]
    for c in symbols:
        a_v, s_v = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a_v): assert pd.isna(s_v)
        else: assert abs(a_v - s_v) < 1e-12
