"""评估引擎数学库单测（纯函数，不依赖数据库）。

覆盖点：
- Pearson IC 对完美正 / 负相关的正确性；
- Rank IC 对"所有因子值相同"这类退化输入不崩；
- 分组收益在因子 == 未来收益时的单调性；
- 换手率在排名不变时应为 0；
- IC 汇总统计（mean / std / ir / win_rate）基本边界；
- 值直方图；
- 多空收益汇总；
- eval_service 只做 import smoke（不触达 DB）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import metrics


def _mk_panel(n_dates: int = 60, n_syms: int = 20, seed: int = 0) -> pd.DataFrame:
    """构造一个形如 (n_dates, n_syms) 的宽表随机因子矩阵。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    cols = [f"S{i:02d}" for i in range(n_syms)]
    return pd.DataFrame(
        rng.standard_normal((n_dates, n_syms)), index=idx, columns=cols
    )


def test_ic_perfect_positive_relationship():
    """未来收益 = 因子 * 0.1 + 极小噪声 → IC 应 ≈ 1。"""
    f = _mk_panel()
    rng = np.random.default_rng(42)
    r = f * 0.1 + rng.standard_normal(f.shape) * 1e-8
    ic = metrics.cross_sectional_ic(f, r)
    assert ic.mean() > 0.99


def test_ic_perfect_negative_relationship():
    """未来收益 = -因子 * 0.1 + 极小噪声 → IC 应 ≈ -1。"""
    f = _mk_panel()
    rng = np.random.default_rng(42)
    r = -f * 0.1 + rng.standard_normal(f.shape) * 1e-8
    ic = metrics.cross_sectional_ic(f, r)
    assert ic.mean() < -0.99


def test_rank_ic_handles_ties():
    """所有因子值相同时 rank corr 无法定义，函数应跳过该日而不是抛或返回 inf。"""
    idx = pd.date_range("2024-01-01", periods=5, freq="B")
    f = pd.DataFrame(1.0, index=idx, columns=["A", "B", "C", "D"])
    r = _mk_panel(n_dates=5, n_syms=4)
    # columns 要对齐：用相同 symbols
    r.columns = ["A", "B", "C", "D"]
    r.index = idx
    rr = metrics.cross_sectional_rank_ic(f, r)
    # 要么 series 为空，要么所有值非 inf / 非 NaN
    if not rr.empty:
        assert not np.isinf(rr).any()
        assert not rr.isna().any()


def test_group_returns_monotonic_when_factor_predicts_return():
    """因子直接 = 未来收益时，分组后各组均收益应严格单调递增。"""
    f = _mk_panel(n_dates=80, n_syms=50)
    r = f.copy()
    g = metrics.group_returns(f, r, n_groups=5)
    assert not g.empty
    means = g.mean().values
    for i in range(len(means) - 1):
        assert means[i] <= means[i + 1], (
            f"分组收益非单调：means[{i}]={means[i]} > means[{i+1}]={means[i+1]}"
        )


def test_turnover_zero_when_factor_rank_constant():
    """每日因子排名一致 → top 组每日成员相同 → 换手率 = 0。"""
    idx = pd.date_range("2024-01-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(10)]
    vals = np.tile(np.arange(10), (10, 1))  # 每行都是 [0,1,...,9]
    f = pd.DataFrame(vals, index=idx, columns=cols)
    to = metrics.turnover_series(f, n_groups=5, which="top")
    assert not to.empty
    assert (to == 0).all()


def test_ic_summary_basic():
    """ic_summary 返回五项指标，ir=mean/std、win_rate 在 [0,1]。"""
    ic = pd.Series([0.1, 0.05, -0.02, 0.08, 0.06])
    s = metrics.ic_summary(ic)
    assert s["ic_mean"] > 0
    assert 0 <= s["ic_win_rate"] <= 1
    # ir 应等于 mean / std(ddof=1)
    assert s["ic_ir"] == pytest.approx(ic.mean() / ic.std(ddof=1))
    # t_stat 应等于 mean / (std/sqrt(n))
    expected_t = ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))
    assert s["ic_t_stat"] == pytest.approx(expected_t)


def test_ic_summary_empty_series():
    """空 IC Series 应返回全零 dict，不抛。"""
    s = metrics.ic_summary(pd.Series([], dtype=float))
    assert s["ic_mean"] == 0
    assert s["ic_std"] == 0
    assert s["ic_ir"] == 0
    assert s["ic_win_rate"] == 0
    assert s["ic_t_stat"] == 0


def test_value_histogram_basic():
    """bins=5 应得到 5 个 count，counts 加总 = 有效值数。"""
    f = pd.DataFrame(
        {"A": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
        index=pd.date_range("2024-01-01", periods=10, freq="B"),
    )
    hist = metrics.value_histogram(f, bins=5)
    assert len(hist["counts"]) == 5
    # numpy 约定 bins 是边界，len(edges) = n+1
    assert len(hist["bins"]) == 6
    assert sum(hist["counts"]) == 10


def test_value_histogram_all_nan():
    """全 NaN 不应抛，返回空 dict。"""
    f = pd.DataFrame(
        {"A": [np.nan] * 5},
        index=pd.date_range("2024-01-01", periods=5, freq="B"),
    )
    hist = metrics.value_histogram(f, bins=5)
    assert hist["bins"] == []
    assert hist["counts"] == []


def test_long_short_metrics_basic():
    """常数 5bp 日收益 → 年化 = 0.0005 * 252。"""
    idx = pd.date_range("2024-01-01", periods=252, freq="B")
    ls = pd.Series([0.0005] * len(idx), index=idx)
    m = metrics.long_short_metrics(ls)
    assert m["long_short_annret"] == pytest.approx(0.0005 * 252)
    # std=0 被兜底为 1e-12，sharpe 会非常大但是有限
    assert np.isfinite(m["long_short_sharpe"])


def test_long_short_metrics_empty():
    """空 Series 不崩，返回零 dict。"""
    m = metrics.long_short_metrics(pd.Series([], dtype=float))
    assert m["long_short_annret"] == 0
    assert m["long_short_sharpe"] == 0


def test_long_short_series_basic():
    """顶组 - 底组应得到 (top - bot)。"""
    idx = pd.date_range("2024-01-01", periods=3, freq="B")
    g = pd.DataFrame(
        {0: [0.01, 0.02, 0.03], 1: [0.02, 0.03, 0.04], 2: [0.03, 0.05, 0.07]},
        index=idx,
    )
    ls = metrics.long_short_series(g)
    expected = pd.Series([0.02, 0.03, 0.04], index=idx)
    pd.testing.assert_series_equal(
        ls.rename(None), expected.rename(None), check_names=False
    )


def test_params_hash_deterministic():
    """同一 dict 两次调用得相同 hash，key 顺序不影响。"""
    from backend.services.params_hash import params_hash

    h1 = params_hash({"a": 1, "b": 2})
    h2 = params_hash({"b": 2, "a": 1})
    assert h1 == h2
    assert len(h1) == 40
    assert all(c in "0123456789abcdef" for c in h1)


def test_params_hash_differs_on_different_params():
    """不同 params 应得到不同 hash。"""
    from backend.services.params_hash import params_hash

    assert params_hash({"a": 1}) != params_hash({"a": 2})


def test_eval_service_imports():
    """eval_service 模块可 import，run_eval 可调用签名存在。"""
    from backend.services.eval_service import run_eval

    # run_eval 是函数，不 crash 即可
    assert callable(run_eval)
