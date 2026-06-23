"""组合优化器纯函数单测。

    uv run pytest backend/tests/test_optimizer.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import optimizer as opt


def test_equal_weights():
    w = opt.equal_weights(4)
    assert np.allclose(w, 0.25) and w.sum() == pytest.approx(1.0)


def test_inverse_vol_lower_weight_for_higher_vol():
    cov = np.diag([0.01, 0.04])  # σ = 0.1, 0.2
    w = opt.inverse_vol_weights(cov)
    assert w.sum() == pytest.approx(1.0)
    # w ∝ 1/σ = [10, 5] → [2/3, 1/3]
    assert w[0] == pytest.approx(2 / 3, abs=1e-6)
    assert w[1] == pytest.approx(1 / 3, abs=1e-6)


def test_risk_parity_diagonal_matches_inverse_vol():
    cov = np.diag([0.01, 0.04])
    w = opt.risk_parity_weights(cov)
    assert w.sum() == pytest.approx(1.0)
    assert w[0] == pytest.approx(2 / 3, abs=1e-3)  # 对角输入 → 逆波动率
    assert w[0] > w[1]


def test_risk_parity_equalizes_risk_contribution():
    cov = np.array([[0.04, 0.01], [0.01, 0.09]])
    w = opt.risk_parity_weights(cov)
    mrc = cov @ w
    rc = w * mrc
    # 两资产风险贡献应近似相等
    assert rc[0] == pytest.approx(rc[1], rel=1e-2)


def test_mean_variance_prefers_high_return_equal_risk():
    mu = np.array([0.10, 0.05])
    cov = np.diag([0.04, 0.04])  # 等风险
    w = opt.mean_variance_weights(mu, cov, risk_aversion=1.0, long_only=True)
    assert w.sum() == pytest.approx(1.0)
    assert (w >= -1e-9).all()  # long-only
    assert w[0] > w[1]  # 高收益资产权重更大


def test_mean_variance_prefers_low_risk_equal_return():
    mu = np.array([0.05, 0.05])
    cov = np.diag([0.01, 0.09])  # 资产0风险低
    w = opt.mean_variance_weights(mu, cov, risk_aversion=5.0, long_only=True)
    assert w[0] > w[1]  # 等收益下偏好低风险


def test_mean_variance_singular_cov_falls_back():
    mu = np.array([0.1, 0.1])
    cov = np.zeros((2, 2))  # 奇异
    w = opt.mean_variance_weights(mu, cov, long_only=True)
    assert w.sum() == pytest.approx(1.0)
    assert np.all(np.isfinite(w))


def test_ic_weighted_combine_equal_weights():
    idx = pd.date_range("2024-01-01", periods=3)
    cols = ["a", "b", "c"]
    f1 = pd.DataFrame([[1.0, 2, 3]] * 3, index=idx, columns=cols)
    f2 = pd.DataFrame([[3.0, 2, 1]] * 3, index=idx, columns=cols)
    out = opt.ic_weighted_combine({"f1": f1, "f2": f2}, {"f1": 0.5, "f2": 0.5})
    # f1 与 f2 是相反排序，等权 z-score 合成后该行应近似全 0
    assert np.allclose(out.iloc[0].to_numpy(), 0.0, atol=1e-9)


def test_ic_weighted_combine_ignores_zero_weight():
    idx = pd.date_range("2024-01-01", periods=2)
    cols = ["a", "b", "c"]
    f1 = pd.DataFrame([[1.0, 2, 3]] * 2, index=idx, columns=cols)
    f2 = pd.DataFrame([[9.0, 9, 9]] * 2, index=idx, columns=cols)
    out = opt.ic_weighted_combine({"f1": f1, "f2": f2}, {"f1": 1.0, "f2": 0.0})
    # f2 权重 0 被忽略；结果 = f1 的 z-score（归一化系数 = |1.0| = 1）
    mean = f1.mean(axis=1)
    std = f1.std(axis=1)
    expect = f1.sub(mean, axis=0).div(std, axis=0)
    assert np.allclose(out.to_numpy(), expect.to_numpy(), equal_nan=True)


def test_ic_weighted_combine_all_zero_raises():
    idx = pd.date_range("2024-01-01", periods=1)
    f1 = pd.DataFrame([[1.0, 2]], index=idx, columns=["a", "b"])
    with pytest.raises(ValueError):
        opt.ic_weighted_combine({"f1": f1}, {"f1": 0.0})


def test_turnover_budget_shrinks_to_limit():
    target = np.array([0.5, 0.5, 0.0])
    prev = np.array([0.0, 0.0, 1.0])
    out = opt.apply_turnover_budget(target, prev, max_turnover=1.0)
    # 原换手 = 2.0 > 1.0，收缩 α=0.5 → [0.25,0.25,0.5]，换手=1.0
    assert np.allclose(out, [0.25, 0.25, 0.5])
    assert np.abs(out - prev).sum() == pytest.approx(1.0)


def test_turnover_budget_no_shrink_within_limit():
    target = np.array([0.4, 0.6])
    prev = np.array([0.5, 0.5])
    out = opt.apply_turnover_budget(target, prev, max_turnover=1.0)
    assert np.allclose(out, target)  # 换手 0.2 < 1.0 不收缩


def test_turnover_budget_disabled():
    target = np.array([1.0, 0.0])
    prev = np.array([0.0, 1.0])
    out = opt.apply_turnover_budget(target, prev, max_turnover=0.0)
    assert np.allclose(out, target)  # <=0 不约束


def test_reweight_intragroup_inverse_vol_keeps_group_total():
    idx = pd.date_range("2024-01-01", periods=70)
    cols = ["a", "b"]
    # 资产 a 低波动(±0.1%)，资产 b 高波动(±5%)
    ra = np.array([0.001 if i % 2 else -0.001 for i in range(70)])
    rb = np.array([0.05 if i % 2 else -0.05 for i in range(70)])
    returns = pd.DataFrame({"a": ra, "b": rb}, index=idx)
    W = pd.DataFrame(0.0, index=idx, columns=cols)
    W.iloc[-1] = [0.5, 0.5]  # 最后一天等权持仓
    out = opt.reweight_intragroup(W, returns, method="inverse_vol", lookback=60)
    last = out.iloc[-1]
    assert last.sum() == pytest.approx(1.0)  # 组总权重保持
    assert last["a"] > last["b"]             # 低波动资产权重更大


def test_reweight_intragroup_equal_is_noop():
    idx = pd.date_range("2024-01-01", periods=1)
    W = pd.DataFrame([[0.5, 0.5]], index=idx, columns=["a", "b"])
    returns = pd.DataFrame([[0.0, 0.0]], index=idx, columns=["a", "b"])
    out = opt.reweight_intragroup(W, returns, method="equal")
    assert out.equals(W)
