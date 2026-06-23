"""样本外验证纯函数单测：窗口切分 + OOS IC 报告。

    uv run pytest backend/tests/test_validation.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import validation as val


# ---------------------------- walk_forward_windows ----------------------------


def test_wf_non_overlapping_default_step():
    wins = val.walk_forward_windows(n=10, train_size=4, test_size=2)
    # start=0: tr[0,4) te[4,6); start=2: tr[2,6) te[6,8); start=4: tr[4,8) te[8,10)
    assert wins == [((0, 4), (4, 6)), ((2, 6), (6, 8)), ((4, 8), (8, 10))]


def test_wf_anchored_train_grows_from_zero():
    wins = val.walk_forward_windows(n=10, train_size=4, test_size=2, anchored=True)
    assert all(tr[0] == 0 for tr, _ in wins)
    assert wins[-1][0] == (0, 8)


def test_wf_custom_step():
    wins = val.walk_forward_windows(n=12, train_size=4, test_size=2, step=4)
    assert wins == [((0, 4), (4, 6)), ((4, 8), (8, 10))]


def test_wf_too_short_returns_empty():
    assert val.walk_forward_windows(n=3, train_size=4, test_size=2) == []


def test_wf_rejects_bad_args():
    with pytest.raises(ValueError):
        val.walk_forward_windows(n=10, train_size=0, test_size=2)


# ---------------------------- purged_kfold_windows ----------------------------


def test_kfold_covers_all_as_test_once():
    folds = val.purged_kfold_windows(n=10, n_splits=5, embargo=0)
    all_test = np.concatenate([te for _, te in folds])
    assert sorted(all_test.tolist()) == list(range(10))  # 每个点恰好做一次测试


def test_kfold_train_test_disjoint():
    folds = val.purged_kfold_windows(n=20, n_splits=4, embargo=0)
    for tr, te in folds:
        assert set(tr.tolist()).isdisjoint(set(te.tolist()))


def test_kfold_embargo_purges_neighbors():
    folds = val.purged_kfold_windows(n=20, n_splits=4, embargo=2)
    # 取中间一折，验证 test 前后 embargo 期不在训练集中
    tr, te = folds[1]
    te_lo, te_hi = int(te[0]), int(te[-1]) + 1
    purged = set(range(max(0, te_lo - 2), min(20, te_hi + 2)))
    assert purged.isdisjoint(set(tr.tolist()))


def test_kfold_rejects_too_few_splits():
    with pytest.raises(ValueError):
        val.purged_kfold_windows(n=10, n_splits=1)


# ---------------------------- oos_validation_report ----------------------------


def _panels(n=60, m=20, factor="perfect"):
    """构造合成 (F, close)：close 由确定性 returns 累乘得到，fwd[t]=R[t+1]。

    factor='perfect' → F=fwd（完美预测，IC≈1）；'inverse' → F=-fwd（IC≈-1）；
    'noise' → F 与 fwd 无关（IC≈0）。
    """
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    syms = [f"s{i:02d}" for i in range(m)]
    # 确定性 returns：用 sin 网格制造横截面差异，避免随机不可复现。
    grid = np.array([[np.sin(0.3 * t + 0.7 * j) for j in range(m)] for t in range(n)])
    R = 0.02 * grid
    close = pd.DataFrame((1.0 + R).cumprod(axis=0), index=dates, columns=syms)
    fwd = close.shift(-1) / close - 1
    if factor == "perfect":
        F = fwd.copy()
    elif factor == "inverse":
        F = -fwd
    else:  # noise：用另一套不相关网格
        F = pd.DataFrame(
            [[np.cos(1.1 * t - 0.4 * j) for j in range(m)] for t in range(n)],
            index=dates, columns=syms,
        )
    return F, close


def test_oos_walk_forward_perfect_factor_high_ic():
    F, close = _panels(factor="perfect")
    rep = val.oos_validation_report(
        F, close, forward_periods=[1], scheme="walk_forward",
        train_size=20, test_size=10,
    )
    assert rep["n_windows"] >= 2
    # 完美因子：样本外 IC 接近 1
    assert rep["summary"]["oos_ic_mean"] > 0.9
    # 衰减比接近 1（IS 与 OOS 都≈1）
    assert rep["summary"]["ic_decay_ratio"] > 0.9


def test_oos_purged_kfold_inverse_factor_negative_ic():
    F, close = _panels(factor="inverse")
    rep = val.oos_validation_report(
        F, close, forward_periods=[1], scheme="purged_kfold",
        n_splits=5, embargo=2,
    )
    assert rep["n_windows"] == 5
    assert rep["summary"]["oos_ic_mean"] < -0.9


def test_oos_noise_factor_near_zero_ic():
    F, close = _panels(factor="noise")
    rep = val.oos_validation_report(
        F, close, forward_periods=[1], scheme="walk_forward",
        train_size=20, test_size=10,
    )
    assert abs(rep["summary"]["oos_ic_mean"]) < 0.3  # 无关因子 OOS IC 接近 0


def test_oos_rejects_bad_scheme():
    F, close = _panels()
    with pytest.raises(ValueError):
        val.oos_validation_report(
            F, close, forward_periods=[1], scheme="bogus",
        )
