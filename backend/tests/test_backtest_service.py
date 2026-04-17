"""backtest_service 单元级 smoke 测试。

本文件不触发真实的 VectorBT 回测（那需要 seed 股票行情 + qfq 因子 + 注册因子，
成本高且属于冒烟集成范畴，留给 Task 12 的端到端冒烟脚本覆盖）。这里只验证：

1. 模块可 import、``run_backtest`` 可调用（pickle 路径友好，为 Task 8 ProcessPool 铺路）；
2. ``_build_weights`` 两种 position 模式的数学正确性：top-only 与 long_short。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def test_backtest_service_imports():
    """模块顶层函数可 import + callable，是 ProcessPool 最低要求。"""
    from backend.services.backtest_service import run_backtest

    assert callable(run_backtest)


def test_build_weights_top_only_mode():
    """top-only：只持最高分组，组内等权，权重和 == 1。"""
    from backend.services.backtest_service import _build_weights

    idx = pd.date_range("2024-04-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(10)]
    # 因子值 = 列索引（每天相同）；qcut 5 组、每组 2 只，top 组 = S8, S9。
    F = pd.DataFrame(
        np.tile(np.arange(10), (10, 1)).astype(float), index=idx, columns=cols
    )
    W = _build_weights(F, n_groups=5, rebalance=1, position="top")
    for dt in W.index:
        # 非 top 组（S0..S7）权重恒 0；允许浮点误差但数值应为严格 0。
        assert W.loc[dt, cols[:8]].abs().sum() == pytest.approx(0.0, abs=1e-9)
        # top 组（S8, S9）等权加和 == 1。
        assert W.loc[dt, cols[8:]].sum() == pytest.approx(1.0, rel=1e-6)


def test_build_weights_long_short_mode():
    """long_short：top 正权 + bottom 负权；行和约为 0，正/负各 2 个非零。"""
    from backend.services.backtest_service import _build_weights

    idx = pd.date_range("2024-04-01", periods=10, freq="B")
    cols = [f"S{i}" for i in range(10)]
    F = pd.DataFrame(
        np.tile(np.arange(10), (10, 1)).astype(float), index=idx, columns=cols
    )
    W = _build_weights(F, n_groups=5, rebalance=1, position="long_short")
    for dt in W.index:
        # 多空净敞口为 0（|long|=|short|=1，各 2 只）。
        assert W.loc[dt].sum() == pytest.approx(0.0, abs=1e-9)
        assert (W.loc[dt] > 0).sum() == 2
        assert (W.loc[dt] < 0).sum() == 2


def test_build_weights_all_tied_row_does_not_crash():
    """回归测试：某调仓日横截面所有因子值完全相同时，pandas 1.x 里
    ``pd.qcut(..., duplicates='drop', labels=False)`` 静默返回全 NaN（而非 ValueError），
    先前的 ``int(labels.max())`` 会崩。修复后应当把这一期视为空仓而非抛异常。

    触发来源：NegReturnArgmaxRank 这类离散 rank 因子，在全市场某日 argmax 一致时
    cross-rank 全等 → factor 全 0 → qcut 退化。
    """
    from backend.services.backtest_service import _build_weights

    idx = pd.date_range("2024-04-01", periods=5, freq="B")
    cols = [f"S{i}" for i in range(10)]
    # 第一行全 0（触发全 NaN labels）；其余行有正常横截面差异，应能正常分组。
    F = pd.DataFrame(
        np.tile(np.arange(10, dtype=float), (5, 1)), index=idx, columns=cols
    )
    F.iloc[0, :] = 0.0

    W = _build_weights(F, n_groups=5, rebalance=1, position="long_short")
    # 第一期空仓：10 列全 0。
    assert W.iloc[0].abs().sum() == pytest.approx(0.0, abs=1e-9)
    # 之后调仓正常（top=S8,S9 / bottom=S0,S1）。
    assert W.iloc[1].sum() == pytest.approx(0.0, abs=1e-9)
    assert (W.iloc[1] > 0).sum() == 2
    assert (W.iloc[1] < 0).sum() == 2
