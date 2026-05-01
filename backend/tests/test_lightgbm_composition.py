"""LightGBM 合成 method=ml_lgb 测试：`_build_future_return_label` 纯函数测试。"""
from __future__ import annotations

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
