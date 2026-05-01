"""Earnings Yield (EP) 因子单测：eps_ttm / close。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
import pytest
from backend.engine.base_factor import FactorContext
from backend.factors.fundamental.earnings_yield import EarningsYield


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]
    fund_panel: pd.DataFrame

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None: return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_earnings_yield_happy_path():
    """因子 = eps_ttm / close，逐元素对齐。"""
    n = 30
    idx = pd.bdate_range("2024-01-02", periods=n)
    symbols = ["A", "B"]
    close = pd.DataFrame({"A": np.linspace(10, 20, n), "B": np.linspace(50, 100, n)}, index=idx)
    eps = pd.DataFrame({"A": [0.5] * n, "B": [2.0] * n}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}, fund_panel=eps),
        symbols=symbols, start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = EarningsYield().compute(ctx, {})
    expected = eps / close
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1), expected.sort_index(axis=1), check_names=False,
    )


def test_earnings_yield_nan_robust():
    """eps_ttm 在某段为 NaN（披露前），因子也是 NaN，不崩。"""
    n = 30
    idx = pd.bdate_range("2024-01-02", periods=n)
    close = pd.DataFrame({"A": np.linspace(10, 20, n)}, index=idx)
    eps = pd.DataFrame({"A": [np.nan]*10 + [0.5]*20}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}, fund_panel=eps),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = EarningsYield().compute(ctx, {})
    assert factor["A"].iloc[:10].isna().all()
    assert factor["A"].iloc[10:].notna().all()


def test_earnings_yield_col_order_invariance():
    n = 20
    idx = pd.bdate_range("2024-01-02", periods=n)
    symbols = ["A", "B", "C"]
    close = pd.DataFrame({s: np.linspace(10, 20, n) + i for i, s in enumerate(symbols)}, index=idx)
    eps = pd.DataFrame({s: [0.5 + i*0.1]*n for i, s in enumerate(symbols)}, index=idx)

    ctx_a = FactorContext(
        data=FakeDataService(panels={"close": close}, fund_panel=eps),
        symbols=symbols, start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fa = EarningsYield().compute(ctx_a, {})

    shuffled = ["B", "C", "A"]
    close_s = close[shuffled]
    eps_s = eps[shuffled]
    ctx_s = FactorContext(
        data=FakeDataService(panels={"close": close_s}, fund_panel=eps_s),
        symbols=shuffled, start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fs = EarningsYield().compute(ctx_s, {})

    for c in symbols:
        assert (fa[c] - fs[c]).abs().max() < 1e-12
