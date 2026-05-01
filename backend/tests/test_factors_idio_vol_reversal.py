"""特质波动率反转单测：-std(returns - cs_mean(returns), 60)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.volatility.idio_vol_reversal import IdioVolReversal


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None: return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_idio_vol_reversal_happy_path():
    """构造 5 票 80 天 close → 算特质 std → 全表与手算对齐。"""
    n = 80
    symbols = ["A", "B", "C", "D", "E"]
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    factor = IdioVolReversal().compute(ctx, {})

    # 手算同口径
    ret = close.pct_change(fill_method=None)
    mkt = ret.mean(axis=1)
    residual = ret.sub(mkt, axis=0)
    expected = -residual.rolling(60).std()

    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_names=False,
    )


def test_idio_vol_reversal_nan_robust():
    n = 80
    symbols = ["A", "B", "C"]
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(1)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    close.iloc[20:30, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    factor = IdioVolReversal().compute(ctx, {})
    assert not factor.empty
    # B/C 末段非 NaN
    assert factor[["B", "C"]].iloc[-3:].notna().all().all()


def test_idio_vol_reversal_col_order_invariance():
    n = 80
    symbols = ["A", "B", "C"]
    idx = pd.bdate_range("2024-01-02", periods=n)
    rng = np.random.default_rng(2)
    close = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}, index=idx,
    )
    ctx_a = FactorContext(
        data=FakeDataService(panels={"close": close}), symbols=symbols,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    fa = IdioVolReversal().compute(ctx_a, {})

    shuffled = ["C", "A", "B"]
    ctx_s = FactorContext(
        data=FakeDataService(panels={"close": close[shuffled]}), symbols=shuffled,
        start_date=idx[65], end_date=idx[-1], warmup_days=65,
    )
    fs = IdioVolReversal().compute(ctx_s, {})

    target = fa.index[5]
    for c in symbols:
        a, s = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a):
            assert pd.isna(s)
        else:
            assert abs(a - s) < 1e-12
