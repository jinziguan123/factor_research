"""组合级风控纯函数单测。

    uv run pytest backend/tests/test_risk_control.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import risk_control as rc


# ---------------------------- concentration_cap ----------------------------


def test_concentration_cap_single_name_water_fill():
    w = pd.Series({"a": 0.5, "b": 0.3, "c": 0.2})
    out = rc.concentration_cap(w, max_weight=0.4)
    assert out.max() <= 0.4 + 1e-9
    assert out.sum() == pytest.approx(1.0)  # 水填充保持总仓位
    assert out["a"] == pytest.approx(0.4)


def test_concentration_cap_noop_when_within_limit():
    w = pd.Series({"a": 0.3, "b": 0.3, "c": 0.4})
    out = rc.concentration_cap(w, max_weight=0.5)
    assert np.allclose(out.to_numpy(), w.to_numpy())


def test_concentration_cap_industry_shrinks_overweight():
    w = pd.Series({"a": 0.4, "b": 0.3, "c": 0.3})
    industry = pd.Series({"a": "X", "b": "X", "c": "Y"})  # X=0.7, Y=0.3
    out = rc.concentration_cap(
        w, max_weight=1.0, industry=industry, max_industry_weight=0.5
    )
    x_sum = out[["a", "b"]].sum()
    assert x_sum <= 0.5 + 1e-9          # 行业 X 缩减到上限
    assert out["c"] == pytest.approx(0.3)  # 行业 Y 不动


# ---------------------------- target_vol_scaling ----------------------------


def test_target_vol_scaling_halves_when_double_vol():
    # 单资产，年化波动率 ≈ 0.2，目标 0.1 → 缩放到半仓
    idx = pd.date_range("2024-01-01", periods=70)
    daily_vol = 0.2 / np.sqrt(252)
    r = np.array([daily_vol if i % 2 else -daily_vol for i in range(70)])
    returns = pd.DataFrame({"a": r}, index=idx)
    w = pd.Series({"a": 1.0})
    out = rc.target_vol_scaling(w, returns, target_vol=0.1, lookback=60)
    assert out["a"] == pytest.approx(0.5, rel=0.15)  # ≈ 半仓


def test_target_vol_scaling_disabled():
    idx = pd.date_range("2024-01-01", periods=10)
    returns = pd.DataFrame({"a": np.zeros(10)}, index=idx)
    w = pd.Series({"a": 1.0})
    out = rc.target_vol_scaling(w, returns, target_vol=0.0)
    assert out["a"] == pytest.approx(1.0)  # target_vol<=0 关闭


def test_target_vol_scaling_capped_by_leverage():
    # 低波动资产想放大，但 max_leverage=1 限制不加杠杆
    idx = pd.date_range("2024-01-01", periods=70)
    tiny = 0.001
    r = np.array([tiny if i % 2 else -tiny for i in range(70)])
    returns = pd.DataFrame({"a": r}, index=idx)
    w = pd.Series({"a": 1.0})
    out = rc.target_vol_scaling(w, returns, target_vol=0.5, lookback=60, max_leverage=1.0)
    assert out["a"] <= 1.0 + 1e-9  # 不超过满仓


# ---------------------------- drawdown_throttle ----------------------------


def test_drawdown_throttle_triggers_below_threshold():
    equity = pd.Series([1.0, 1.1, 0.9, 0.95])
    mult = rc.drawdown_throttle(equity, dd_threshold=0.1, throttle_factor=0.5)
    # peak=[1,1.1,1.1,1.1]; dd=[0,0,-0.18,-0.136] → idx2,3 触发
    assert mult.tolist() == [1.0, 1.0, 0.5, 0.5]


def test_drawdown_throttle_no_trigger_in_uptrend():
    equity = np.array([1.0, 1.05, 1.1, 1.2])
    mult = rc.drawdown_throttle(equity, dd_threshold=0.1)
    assert np.allclose(mult, 1.0)


def test_drawdown_throttle_empty():
    assert rc.drawdown_throttle(np.array([]), 0.1).size == 0


# ---------------------------- apply_portfolio_risk ----------------------------


def test_apply_portfolio_risk_caps_position():
    idx = pd.date_range("2024-01-01", periods=3)
    cols = ["a", "b", "c"]
    close = pd.DataFrame(
        np.cumprod(1 + 0.01 * np.ones((3, 3)), axis=0), index=idx, columns=cols
    )
    W = pd.DataFrame(0.0, index=idx, columns=cols)
    W.iloc[-1] = [0.6, 0.3, 0.1]  # a 超配
    out = rc.apply_portfolio_risk(W, close, max_position_weight=0.4)
    assert out.iloc[-1].max() <= 0.4 + 1e-9
    assert out.iloc[-1].sum() == pytest.approx(1.0)


def test_apply_portfolio_risk_disabled_is_noop():
    idx = pd.date_range("2024-01-01", periods=2)
    close = pd.DataFrame([[10.0, 10], [10, 10]], index=idx, columns=["a", "b"])
    W = pd.DataFrame([[0.5, 0.5], [0.5, 0.5]], index=idx, columns=["a", "b"])
    out = rc.apply_portfolio_risk(W, close)  # 全关
    assert out.equals(W)
