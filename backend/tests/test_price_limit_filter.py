"""涨跌停过滤的纯函数单测。

覆盖：
- ``_compute_price_limit_mask``：触板检测的口径与边界（NaN / 首行 / 阈值附近）；
- ``_build_weights``：``excluded_mask`` 是否真的让触板票退出 qcut 候选。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.services.backtest_service import (
    _build_weights,
    _compute_price_limit_mask,
)


def _idx(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


# ============== _compute_price_limit_mask ==============


def test_mask_first_row_all_false() -> None:
    """首行 prev_close 为 NaN，mask 应全 False（不触板）。"""
    close = pd.DataFrame({"A": [10.0, 11.0], "B": [10.0, 9.0]}, index=_idx(2))
    m = _compute_price_limit_mask(close)
    assert m.iloc[0].any() is np.False_ or not m.iloc[0].any()
    # 第二行 A: 11/10-1 = 0.10 → 触板；B: 9/10-1 = -0.10 → 触板
    assert bool(m.iloc[1, 0])
    assert bool(m.iloc[1, 1])


def test_mask_threshold_boundary() -> None:
    """阈值默认 0.097：明显低于（9.6%）不触板，明显高于（9.8% / 10%）触板。

    刻意避开 9.7% 附近的浮点边界——(10.97-10)/10 在 IEEE 754 下未必精确 ≥ 0.097，
    口径稳定性留给 ``threshold`` 参数本身去调，单测只锁"明显两侧"的方向性。
    """
    close = pd.DataFrame(
        {"A": [10.0, 10.96], "B": [10.0, 10.98], "C": [10.0, 11.00]},
        index=_idx(2),
    )
    m = _compute_price_limit_mask(close)
    assert not bool(m.iloc[1, 0])  # 9.6% < 9.7%
    assert bool(m.iloc[1, 1])  # 9.8% > 9.7%
    assert bool(m.iloc[1, 2])  # 10% > 9.7%


def test_mask_negative_limit_down() -> None:
    """跌停同样标记 True（abs ≥ threshold）。"""
    close = pd.DataFrame({"A": [10.0, 9.03]}, index=_idx(2))  # -9.7%
    m = _compute_price_limit_mask(close)
    assert bool(m.iloc[1, 0])
    # -9.69% 不触板
    close2 = pd.DataFrame({"A": [10.0, 9.031]}, index=_idx(2))
    m2 = _compute_price_limit_mask(close2)
    assert not bool(m2.iloc[1, 0])


def test_mask_handles_nan_close() -> None:
    """停牌（close=NaN）→ pct=NaN → mask=False（不视为触板）。"""
    close = pd.DataFrame({"A": [10.0, np.nan, 12.0]}, index=_idx(3))
    m = _compute_price_limit_mask(close)
    assert not bool(m.iloc[1, 0])  # NaN → False
    # 第三行 12/NaN = NaN → False（停牌恢复后无 prev_close 参考）
    assert not bool(m.iloc[2, 0])


def test_mask_custom_threshold() -> None:
    """阈值可调：threshold=0.197（适配 20% 板）下，10% 不触板，20% 触板。"""
    close = pd.DataFrame({"A": [10.0, 11.0, 13.2]}, index=_idx(3))
    m = _compute_price_limit_mask(close, threshold=0.197)
    assert not bool(m.iloc[1, 0])  # 10% < 19.7%
    assert bool(m.iloc[2, 0])  # 20% > 19.7%


# ============== _build_weights with excluded_mask ==============


def _factor_panel_with_clear_top(n_dates: int = 4, n_syms: int = 10) -> pd.DataFrame:
    """构造一张 ``n_syms`` 列 ``n_dates`` 行的因子表，每行 [1..n_syms]——top=末列。

    n_syms 默认 10：n_groups=5 时即便剔除 1~3 只票，仍 ≥ n_groups，避免触发
    "valid < n_groups → 跳过本期"分支干扰对 mask 行为本身的断言。
    """
    idx = _idx(n_dates)
    syms = [f"S{i:02d}" for i in range(n_syms)]
    data = np.tile(np.arange(1, n_syms + 1, dtype=float), (n_dates, 1))
    return pd.DataFrame(data, index=idx, columns=syms)


def test_build_weights_no_mask_baseline() -> None:
    """无 mask：n_groups=5、10 列因子值 [1..10] → top 组（label=4）= 末 2 列等权。"""
    F = _factor_panel_with_clear_top()
    W = _build_weights(F, n_groups=5, rebalance=1, position="top")
    # qcut 5 组 10 个值：每组 2 个；top label=4 = 最后 2 列 [S08, S09]
    for dt in F.index:
        assert W.loc[dt, "S08"] == 0.5
        assert W.loc[dt, "S09"] == 0.5
        # 其它列权重为 0
        assert W.loc[dt, [f"S{i:02d}" for i in range(8)]].sum() == 0.0


def test_build_weights_excluded_mask_drops_top() -> None:
    """mask 在第一调仓日把 top 组的列剔除 → top 自动右移到剩余列。"""
    F = _factor_panel_with_clear_top()
    mask = pd.DataFrame(False, index=F.index, columns=F.columns)
    # 把原 top 组（S08, S09）都 ban 掉
    mask.loc[F.index[0], ["S08", "S09"]] = True

    W = _build_weights(
        F, n_groups=5, rebalance=1, position="top", excluded_mask=mask,
    )
    # 剩 8 列 [1..8]，qcut 5 组 → top 组（label=4）= 末 2 列 [S06, S07]
    assert W.iloc[0]["S06"] == 0.5
    assert W.iloc[0]["S07"] == 0.5
    assert W.iloc[0]["S08"] == 0.0
    assert W.iloc[0]["S09"] == 0.0
    # 后续行未被剔除，top 恢复 [S08, S09]
    assert W.iloc[1]["S08"] == 0.5
    assert W.iloc[1]["S09"] == 0.5


def test_build_weights_excluded_mask_long_short() -> None:
    """long_short 下：mask 把 bottom 组的列剔除 → bottom 退到剩余列。"""
    F = _factor_panel_with_clear_top()
    mask = pd.DataFrame(False, index=F.index, columns=F.columns)
    # 把原 bottom 组（S00, S01）都 ban 掉
    mask.loc[F.index[0], ["S00", "S01"]] = True

    W = _build_weights(
        F, n_groups=5, rebalance=1, position="long_short", excluded_mask=mask,
    )
    # top 仍是 [S08, S09]（正权），bottom 退到剩 [2..9] 的最低 2 列 = [S02, S03]
    assert W.iloc[0]["S08"] > 0
    assert W.iloc[0]["S09"] > 0
    assert W.iloc[0]["S02"] < 0
    assert W.iloc[0]["S03"] < 0
    # 被剔的列权重 0
    assert W.iloc[0]["S00"] == 0.0
    assert W.iloc[0]["S01"] == 0.0


def test_build_weights_excluded_mask_too_aggressive_skips_period() -> None:
    """若 mask 把所有票都剔除，本期 valid < n_groups → 退回空仓。"""
    F = _factor_panel_with_clear_top()
    mask = pd.DataFrame(True, index=F.index, columns=F.columns)
    W = _build_weights(
        F, n_groups=5, rebalance=1, position="top", excluded_mask=mask,
    )
    assert (W.values == 0.0).all()


def test_build_weights_mask_index_partial_coverage() -> None:
    """mask 只覆盖部分日期：未被覆盖的日期不过滤（baseline 行为）。"""
    F = _factor_panel_with_clear_top()
    # 只给第一行 mask
    mask = pd.DataFrame(False, index=F.index[:1], columns=F.columns)
    mask.loc[F.index[0], ["S08", "S09"]] = True

    W = _build_weights(
        F, n_groups=5, rebalance=1, position="top", excluded_mask=mask,
    )
    # 第一行 top 退到 [S06, S07]
    assert W.iloc[0]["S06"] == 0.5
    assert W.iloc[0]["S07"] == 0.5
    # 其它行未在 mask.index 中 → 不过滤，top 仍是 [S08, S09]
    for i in range(1, len(F)):
        assert W.iloc[i]["S08"] == 0.5
        assert W.iloc[i]["S09"] == 0.5


def test_build_weights_mask_columns_partial() -> None:
    """mask 列子集（不含某些 symbol）：缺失列的 reindex 默认 False，不过滤。"""
    F = _factor_panel_with_clear_top()
    # 只给前 5 列 mask（S08/S09 不在 mask 里）
    mask = pd.DataFrame(
        False, index=F.index, columns=[f"S{i:02d}" for i in range(5)],
    )
    mask.loc[F.index[0], "S04"] = True  # 中间列被剔，不影响 top

    W = _build_weights(
        F, n_groups=5, rebalance=1, position="top", excluded_mask=mask,
    )
    # S08/S09 不在 mask 列 → reindex 后视为 False，top 仍是 [S08, S09]
    assert W.iloc[0]["S08"] == 0.5
    assert W.iloc[0]["S09"] == 0.5
