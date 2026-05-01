"""Alpha101 #12 因子单测：sign(Δvol) * (-Δclose)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_12 import Alpha101_12


@dataclass
class FakeDataService:
    panels: dict[str, pd.DataFrame]

    def load_panel(self, symbols, start, end, freq="1d", field="close", adjust="qfq"):
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def _make_panels(n, symbols, seed):
    rng = np.random.default_rng(seed)
    idx = _biz_index(n)
    return {
        "close": pd.DataFrame(
            {s: 10 + rng.normal(0, 0.3, n).cumsum() for s in symbols}, index=idx,
        ),
        "volume": pd.DataFrame(
            {s: rng.uniform(1e6, 1e7, n) for s in symbols}, index=idx,
        ),
    }


def test_alpha101_12_happy_path():
    """因子 = sign(Δvol) * (-Δclose)，与手算对齐（全表对比）。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=0)
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    factor = Alpha101_12().compute(ctx, {})
    # 手算同口径（含 Δvol==0 → NaN mask，避免停牌污染 cross-section rank）
    dvol = panels["volume"].diff(1)
    dvol = dvol.where(dvol != 0)
    expected = np.sign(dvol) * (-panels["close"].diff(1))
    # 全表对比 catch 切片 off-by-one + 列对齐错位（比单行断言覆盖面更大）
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_names=False,
    )


def test_alpha101_12_nan_robust():
    n = 30
    symbols = ["A", "B"]
    panels = _make_panels(n, symbols, seed=1)
    panels["close"].iloc[10:13, 0] = np.nan
    panels["volume"].iloc[10:13, 0] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    factor = Alpha101_12().compute(ctx, {})
    assert not factor.empty
    assert factor["B"].iloc[-5:].notna().all()


def test_alpha101_12_col_order_invariance():
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=2)
    ctx_a = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    fa = Alpha101_12().compute(ctx_a, {})

    shuffled = ["C", "A", "B"]
    panels_s = {k: v[shuffled] for k, v in panels.items()}
    ctx_s = FactorContext(
        data=FakeDataService(panels=panels_s), symbols=shuffled,
        start_date=panels_s["close"].index[3], end_date=panels_s["close"].index[-1],
        warmup_days=3,
    )
    fs = Alpha101_12().compute(ctx_s, {})

    target = fa.index[5]
    for c in symbols:
        a_v, s_v = fa.loc[target, c], fs.loc[target, c]
        if pd.isna(a_v): assert pd.isna(s_v)
        else: assert abs(a_v - s_v) < 1e-12


def test_alpha101_12_zero_volume_diff_is_nan():
    """连续两日 volume 相同（含停牌 0=0）→ Δvol=0 → 因子应是 NaN，
    不能输出 0 污染 cross-section rank。"""
    n = 30
    symbols = ["A", "B"]
    panels = _make_panels(n, symbols, seed=0)
    # 让 A 在 idx[15..16] 连续两日 volume 相同（模拟停牌）
    panels["volume"].iloc[15, 0] = panels["volume"].iloc[14, 0]
    panels["volume"].iloc[16, 0] = panels["volume"].iloc[15, 0]
    ctx = FactorContext(
        data=FakeDataService(panels=panels), symbols=symbols,
        start_date=panels["close"].index[3], end_date=panels["close"].index[-1],
        warmup_days=3,
    )
    factor = Alpha101_12().compute(ctx, {})
    # idx[15] 和 idx[16] 的 A 列应该 NaN（Δvol=0）
    assert pd.isna(factor["A"].loc[panels["close"].index[15]])
    assert pd.isna(factor["A"].loc[panels["close"].index[16]])
    # 同期 B 因子非 NaN
    assert not pd.isna(factor["B"].loc[panels["close"].index[15]])
