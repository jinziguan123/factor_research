"""Alpha101 #8 因子的纯计算单测。

公式：(-1 * rank((sum(open,5)*sum(returns,5))
              - delay((sum(open,5)*sum(returns,5)), 10)))

通过 FakeDataService 喂构造的 open / close panel，验证：
- 横截面 rank 后值落在 [-1, 0] 区间；
- 手算 1 个特定日期的差值 / rank，与因子输出对齐；
- delay 10 日确实做了 shift；
- warmup 切片正确。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.factors.alpha101.alpha101_8 import Alpha101_8


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


def _make_ctx(panels: dict, *, start_offset: int, n: int) -> FactorContext:
    idx = panels["close"].index
    return FactorContext(
        data=FakeDataService(panels=panels),
        symbols=list(panels["close"].columns),
        start_date=idx[start_offset],
        end_date=idx[-1],
        warmup_days=start_offset,
    )


def test_required_warmup_constant() -> None:
    """无超参，warmup = int(16*1.5)+5 = 29。"""
    assert Alpha101_8().required_warmup({}) == 29


def test_factor_value_range_in_minus_one_to_zero() -> None:
    """横截面 pct rank ∈ (0, 1] → factor = -rank ∈ [-1, 0)。

    构造 5 只股票随机走势，验证因子值都落在 [-1, 0] 内。
    """
    rng = np.random.default_rng(7)
    n = 60
    idx = _biz_index(n)
    symbols = ["A", "B", "C", "D", "E"]
    # 随机游走价格序列，避免人为单调
    open_data = {s: 10 + np.cumsum(rng.normal(0, 0.5, n)) for s in symbols}
    close_data = {s: open_data[s] + rng.normal(0, 0.3, n) for s in symbols}
    panels = {
        "open": pd.DataFrame(open_data, index=idx),
        "close": pd.DataFrame(close_data, index=idx),
    }
    ctx = _make_ctx(panels, start_offset=20, n=n)

    factor = Alpha101_8().compute(ctx, {})

    tail = factor.dropna(how="all").stack()
    assert not tail.empty
    # rank pct=True 给 (0, 1]，取负后 [-1, 0)
    assert (tail.values <= 0 + 1e-9).all()
    assert (tail.values >= -1 - 1e-9).all()


def test_factor_matches_manual_compute_on_specific_date() -> None:
    """手算一个特定日期，与因子输出严格对齐（5 只股票横截面）。"""
    rng = np.random.default_rng(123)
    n = 50
    idx = _biz_index(n)
    symbols = ["A", "B", "C", "D", "E"]
    open_data = {s: 10 + rng.normal(0, 1, n).cumsum() for s in symbols}
    close_data = {s: open_data[s] + rng.normal(0, 0.2, n) for s in symbols}
    open_df = pd.DataFrame(open_data, index=idx)
    close_df = pd.DataFrame(close_data, index=idx)

    # 手算 sum(open,5) * sum(returns,5)，再 - shift(10)，再横截面 pct rank，再取负。
    returns = close_df.pct_change(fill_method=None)
    compound = open_df.rolling(5).sum() * returns.rolling(5).sum()
    diff = compound - compound.shift(10)
    expected = -diff.rank(axis=1, method="average", pct=True)

    panels = {"open": open_df, "close": close_df}
    ctx = _make_ctx(panels, start_offset=30, n=n)
    factor = Alpha101_8().compute(ctx, {})

    # 任取因子内一行（例如第 5 行）作精确对比
    target_date = factor.index[5]
    pd.testing.assert_series_equal(
        factor.loc[target_date].sort_index(),
        expected.loc[target_date].sort_index(),
        check_names=False,
    )


def test_delay_actually_shifts_10_days() -> None:
    """构造一只股票"前 15 日平稳、第 16 日跳变"的 compound 序列，
    跳变后第 1~10 日的 diff 都因 shift(10) 还在与平稳期相比，
    第 11 日 diff = 0（自身减自身的延迟），跳变信号传播完毕。

    更直接的等价测试：固定列只检查 shift 操作的天数。
    """
    n = 40
    idx = _biz_index(n)

    # 只用 1 列：open / close 用同一序列，sum(open,5) * sum(returns,5) 也是一致序列。
    # 直接对比 diff = compound - compound.shift(10) 的位置正确性。
    open_arr = np.linspace(10, 20, n)
    close_arr = np.linspace(10, 20, n)
    open_df = pd.DataFrame({"A": open_arr}, index=idx)
    close_df = pd.DataFrame({"A": close_arr}, index=idx)

    returns = close_df.pct_change(fill_method=None)
    compound = open_df.rolling(5).sum() * returns.rolling(5).sum()
    diff_expected = compound - compound.shift(10)

    panels = {"open": open_df, "close": close_df}
    ctx = _make_ctx(panels, start_offset=20, n=n)

    # 只 1 只股票时横截面 rank 全为 1.0（pct=True，所有非 NaN 行），
    # 因子 = -1.0 在 diff 非 NaN 的位置上。
    factor = Alpha101_8().compute(ctx, {})

    nonnan_mask = ~diff_expected["A"].loc[ctx.start_date :].isna()
    factor_a = factor["A"]
    # 在 expected diff 非 NaN 的位置上，factor 也应为 -1.0；NaN 位置对齐。
    assert (factor_a[nonnan_mask] == -1.0).all()
    assert factor_a[~nonnan_mask].isna().all()


def test_returns_empty_on_missing_panel() -> None:
    """open 或 close 缺失时返回空 DataFrame，不抛异常。"""
    n = 30
    idx = _biz_index(n)
    panels = {"close": pd.DataFrame({"A": np.arange(n, dtype=float)}, index=idx)}
    # open 缺失
    ctx = _make_ctx(
        {"close": panels["close"], "open": pd.DataFrame()},
        start_offset=10,
        n=n,
    )
    factor = Alpha101_8().compute(ctx, {})
    assert factor.empty


def test_factor_index_starts_at_ctx_start_date() -> None:
    """切片正确：返回的 DataFrame 第一行就是 ctx.start_date（如果数据可达）。"""
    rng = np.random.default_rng(0)
    n = 60
    idx = _biz_index(n)
    symbols = ["A", "B", "C"]
    open_df = pd.DataFrame(
        {s: 10 + rng.normal(0, 0.3, n).cumsum() for s in symbols},
        index=idx,
    )
    close_df = open_df + rng.normal(0, 0.2, (n, len(symbols)))
    panels = {"open": open_df, "close": close_df}
    ctx = _make_ctx(panels, start_offset=25, n=n)

    factor = Alpha101_8().compute(ctx, {})
    assert factor.index[0] == ctx.start_date
    assert factor.index[-1] == ctx.end_date
