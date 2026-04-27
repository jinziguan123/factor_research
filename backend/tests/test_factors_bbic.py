"""BBIC 因子的纯计算单测。

公式：BBIC = (MA3 + MA6 + MA12 + MA24) / 4 / close

通过 FakeDataService 喂构造的 close panel，验证：
- warmup 常量；
- 单调上涨：BBI 滞后 → BBIC < 1；
- 单调下跌：BBI 滞后 → BBIC > 1；
- 平稳序列：BBIC ≈ 1；
- 手算定式与因子输出严格一致；
- 切片正确。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.factors.momentum.bbic import BBIC


@dataclass
class FakeDataService:
    """只实现 load_panel 的最小替身。"""
    panels: dict[str, pd.DataFrame]

    def load_panel(
        self,
        symbols,
        start,
        end,
        freq: str = "1d",
        field: str = "close",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def _make_ctx(panels: dict, *, start_offset: int) -> FactorContext:
    idx = panels["close"].index
    return FactorContext(
        data=FakeDataService(panels=panels),
        symbols=list(panels["close"].columns),
        start_date=idx[start_offset],
        end_date=idx[-1],
        warmup_days=start_offset,
    )


def test_required_warmup_constant() -> None:
    """无超参，warmup = int(24*1.5)+10 = 46。"""
    assert BBIC().required_warmup({}) == 46


def test_monotonic_up_gives_factor_below_one() -> None:
    """单调上涨：均线滞后于价格 → BBI < close → BBIC < 1。"""
    n = 60
    idx = _biz_index(n)
    close = pd.DataFrame({"A": np.linspace(10.0, 30.0, num=n)}, index=idx)
    panels = {"close": close}
    ctx = _make_ctx(panels, start_offset=30)

    factor = BBIC().compute(ctx, {})
    tail = factor["A"].dropna()
    assert not tail.empty
    # 上涨趋势中 BBIC 应稳定 < 1
    assert (tail.values < 1.0).all()


def test_monotonic_down_gives_factor_above_one() -> None:
    """单调下跌：均线滞后于价格 → BBI > close → BBIC > 1。"""
    n = 60
    idx = _biz_index(n)
    close = pd.DataFrame({"A": np.linspace(30.0, 10.0, num=n)}, index=idx)
    panels = {"close": close}
    ctx = _make_ctx(panels, start_offset=30)

    factor = BBIC().compute(ctx, {})
    tail = factor["A"].dropna()
    assert not tail.empty
    # 下跌趋势中 BBIC 应稳定 > 1
    assert (tail.values > 1.0).all()


def test_flat_series_gives_factor_equal_one() -> None:
    """平稳序列：所有均线 = close → BBI = close → BBIC = 1。"""
    n = 50
    idx = _biz_index(n)
    close = pd.DataFrame({"A": np.full(n, 15.0)}, index=idx)
    panels = {"close": close}
    ctx = _make_ctx(panels, start_offset=30)

    factor = BBIC().compute(ctx, {})
    tail = factor["A"].dropna()
    assert not tail.empty
    assert np.allclose(tail.values, 1.0, atol=1e-12)


def test_factor_matches_manual_compute() -> None:
    """手算 BBIC 与因子输出严格一致（按位精确对齐）。"""
    rng = np.random.default_rng(42)
    n = 80
    idx = _biz_index(n)
    symbols = ["A", "B", "C"]
    close_data = {
        s: 10 + rng.normal(0, 0.5, n).cumsum() for s in symbols
    }
    close = pd.DataFrame(close_data, index=idx)

    bbi_manual = (
        close.rolling(3).mean()
        + close.rolling(6).mean()
        + close.rolling(12).mean()
        + close.rolling(24).mean()
    ) / 4
    expected = bbi_manual / close

    panels = {"close": close}
    ctx = _make_ctx(panels, start_offset=40)
    factor = BBIC().compute(ctx, {})

    pd.testing.assert_frame_equal(
        factor.sort_index(axis=1),
        expected.loc[ctx.start_date :].sort_index(axis=1),
        check_freq=False,
    )


def test_returns_empty_on_missing_close() -> None:
    """close panel 为空时返回空 DataFrame。"""
    n = 30
    idx = _biz_index(n)
    panels = {"close": pd.DataFrame()}
    # 让 idx 走通 _make_ctx 的索引访问需要给一个非空 close；这里直接构造空场景
    ctx = FactorContext(
        data=FakeDataService(panels={"close": pd.DataFrame()}),
        symbols=["A"],
        start_date=idx[10],
        end_date=idx[-1],
        warmup_days=10,
    )
    factor = BBIC().compute(ctx, {})
    assert factor.empty


def test_factor_index_starts_at_ctx_start_date() -> None:
    """切片正确：返回的 DataFrame 第一行就是 ctx.start_date。"""
    n = 80
    idx = _biz_index(n)
    rng = np.random.default_rng(0)
    close = pd.DataFrame(
        {"A": 10 + rng.normal(0, 0.3, n).cumsum()},
        index=idx,
    )
    panels = {"close": close}
    ctx = _make_ctx(panels, start_offset=40)

    factor = BBIC().compute(ctx, {})
    assert factor.index[0] == ctx.start_date
    assert factor.index[-1] == ctx.end_date
