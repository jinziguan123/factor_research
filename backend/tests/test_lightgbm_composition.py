"""LightGBM 合成 method=ml_lgb 测试：`_build_future_return_label` 纯函数测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------- _build_future_return_label ----------------------------


def test_build_future_return_label_rank_to_pm_one():
    """每日 cross-section rank → [-1, 1] 区间。"""
    from backend.services.composition_service import _build_future_return_label

    # 4 个日期 × 3 只票，构造已知排序的 close
    dates = pd.date_range("2024-01-01", periods=4)
    close = pd.DataFrame(
        # forward_period=1 时 future_return = close.shift(-1) / close - 1
        # day 0 → return: A=0.1, B=0.2, C=0.3 (升序)
        # day 1 → return: A=-0.1, B=0, C=0.1
        # day 2 → return: A=0.5, B=0.4, C=0.3 (降序)
        # day 3 → 全 NaN（最末日没未来）
        {"A": [1.0, 1.1, 0.99, 1.485], "B": [1.0, 1.2, 1.2, 1.68], "C": [1.0, 1.3, 1.43, 1.859]},
        index=dates,
    )

    out = _build_future_return_label(close, forward_period=1)

    # day 0：A 排名最低（-1）、C 排名最高（+1）
    assert out.loc[dates[0], "A"] < out.loc[dates[0], "B"] < out.loc[dates[0], "C"]
    assert abs(out.loc[dates[0], "C"] - 1.0) < 1e-9
    # 极值落在 [-1, 1]
    valid = out.dropna(how="all")
    assert valid.values.min() >= -1.0 - 1e-9
    assert valid.values.max() <= 1.0 + 1e-9
    # day 3（最末日）应全 NaN（没未来收益）
    assert out.loc[dates[3]].isna().all()


# ---------------------------- _combine_lightgbm happy path ----------------------------


def _make_factor_panel(n_dates: int, n_symbols: int, seed: int = 0) -> pd.DataFrame:
    """生成 (date × symbol) 浮点面板，z-score 化的随机数。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates)
    symbols = [f"S{i:03d}" for i in range(n_symbols)]
    data = rng.standard_normal((n_dates, n_symbols))
    return pd.DataFrame(data, index=dates, columns=symbols)


def test_combine_lightgbm_returns_pred_and_importance():
    """happy path：3 因子 × 80 天 × 30 票 → 输出 pred 面板 + feature_importance dict。"""
    from backend.services.composition_service import _combine_lightgbm

    z_frames = [
        _make_factor_panel(80, 30, seed=1),
        _make_factor_panel(80, 30, seed=2),
        _make_factor_panel(80, 30, seed=3),
    ]
    factor_ids = ["f1", "f2", "f3"]
    label_panel = _make_factor_panel(80, 30, seed=99)  # 用作 label（已 [-1, 1] 风格）

    pred, fi = _combine_lightgbm(
        z_frames, label_panel, factor_ids,
        forward_period=5, warmup_days=20,
    )

    # 输出 shape 与原因子一致
    assert pred.shape == (80, 30)
    # 前 warmup+forward_period 天应为 NaN（cold start）
    assert pred.iloc[:25].isna().all().all()
    # warmup 后至少有非 NaN 预测
    assert pred.iloc[26:].notna().any().any()
    # feature_importance 三个键都在
    assert set(fi.keys()) == {"f1", "f2", "f3"}
    # importance 值都 ≥ 0
    assert all(v >= 0 for v in fi.values())
