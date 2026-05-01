"""Alpha101 #6 因子单测（-corr(open, volume, 10)）。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_6 import Alpha101_6


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


def _make_panels(n: int, symbols: list[str], seed: int) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = _biz_index(n)
    open_data = {s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols}
    vol_data = {s: rng.uniform(1e6, 1e7, n) for s in symbols}
    return {
        "open": pd.DataFrame(open_data, index=idx),
        "volume": pd.DataFrame(vol_data, index=idx),
    }


def test_alpha101_6_happy_path():
    """30 天 × 5 票，因子 = -rolling(10).corr(open, volume) 与手算对齐。"""
    n = 30
    symbols = ["A", "B", "C", "D", "E"]
    panels = _make_panels(n, symbols, seed=0)
    ctx = FactorContext(
        data=FakeDataService(panels=panels),
        symbols=symbols,
        start_date=panels["open"].index[15],
        end_date=panels["open"].index[-1],
        warmup_days=15,
    )
    factor = Alpha101_6().compute(ctx, {})

    # 手算同口径
    open_df = panels["open"]
    vol_df = panels["volume"]
    expected = -open_df.rolling(10).corr(vol_df)
    # 全表对比 catch 切片 off-by-one + 列对齐错位（比单行断言覆盖面更大）
    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_names=False,
    )


def test_alpha101_6_nan_robust():
    """某只票某段 NaN（模拟停牌），因子不崩 + 该段输出 NaN。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=1)
    panels["open"].iloc[10:15, panels["open"].columns.get_loc("A")] = np.nan
    panels["volume"].iloc[10:15, panels["volume"].columns.get_loc("A")] = np.nan
    ctx = FactorContext(
        data=FakeDataService(panels=panels),
        symbols=symbols,
        start_date=panels["open"].index[15],
        end_date=panels["open"].index[-1],
        warmup_days=15,
    )
    factor = Alpha101_6().compute(ctx, {})
    # 不抛异常 + B/C 列在末段非 NaN
    assert not factor.empty
    assert factor[["B", "C"]].iloc[-5:].notna().any().all()


def test_alpha101_6_col_order_invariance():
    """打乱 columns 顺序，因子值在对应 column 上一致。"""
    n = 30
    symbols = ["A", "B", "C"]
    panels = _make_panels(n, symbols, seed=2)
    ctx_a = FactorContext(
        data=FakeDataService(panels=panels),
        symbols=symbols,
        start_date=panels["open"].index[15],
        end_date=panels["open"].index[-1],
        warmup_days=15,
    )
    factor_a = Alpha101_6().compute(ctx_a, {})

    # 打乱
    shuffled = ["C", "A", "B"]
    panels_s = {k: v[shuffled] for k, v in panels.items()}
    ctx_s = FactorContext(
        data=FakeDataService(panels=panels_s),
        symbols=shuffled,
        start_date=panels_s["open"].index[15],
        end_date=panels_s["open"].index[-1],
        warmup_days=15,
    )
    factor_s = Alpha101_6().compute(ctx_s, {})

    # 同 column 上 NaN-aware 等值
    target = factor_a.index[5]
    for c in symbols:
        a_v = factor_a.loc[target, c]
        s_v = factor_s.loc[target, c]
        if pd.isna(a_v):
            assert pd.isna(s_v)
        else:
            assert abs(a_v - s_v) < 1e-12
