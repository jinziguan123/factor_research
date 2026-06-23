"""样本外验证框架：Walk-Forward 滚动回测 + Purged K-Fold 交叉验证。

**纯函数 / 纯计算，无 DB 依赖**，便于单测（与 ``metrics.py`` / ``execution.py`` 同风格）。
用来回答："这个因子的预测力是真实的，还是对历史窗口过拟合出来的？"——通过对比
样本内（IS）与样本外（OOS）IC，量化 alpha 衰减。

两种切分：
- **Walk-Forward**：固定/扩张训练窗 + 紧随其后的测试窗，按 step 向前滚动。模拟真实
  的"用过去预测未来"。
- **Purged K-Fold**：把时间轴分 K 折，每折轮流做测试集，训练集为其余折但**剔除测试
  集前后 embargo 期**——因为 forward return 跨越边界会让紧邻样本的标签泄露进训练集。

窗口用整数位置 ``[lo, hi)`` 半开区间表示，与 ``DataFrame.iloc`` 对齐。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backend.services import metrics


def _f(x) -> float | None:
    """标量 → float，NaN/inf/非数 → None（JSON 安全）。"""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def walk_forward_windows(
    n: int,
    train_size: int,
    test_size: int,
    step: int | None = None,
    anchored: bool = False,
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """生成 Walk-Forward 窗口序列。

    Args:
        n: 序列总长度（交易日数）。
        train_size: 训练窗长度。
        test_size: 测试窗长度。
        step: 每次向前滚动的步长；缺省 = test_size（相邻测试窗不重叠）。
        anchored: True 时训练窗从 0 起扩张（anchored / expanding）；False 为固定长度滚动。

    Returns:
        ``[((train_lo, train_hi), (test_lo, test_hi)), ...]``，整数位置半开区间。
        放不下一个完整 (train + test) 时返回空列表。
    """
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size / test_size 必须 ≥ 1")
    if n <= 0:
        return []
    step = test_size if step is None else step
    if step < 1:
        raise ValueError("step 必须 ≥ 1")

    out: list[tuple[tuple[int, int], tuple[int, int]]] = []
    start = 0
    while start + train_size + test_size <= n:
        tr_lo = 0 if anchored else start
        tr_hi = start + train_size
        te_lo = tr_hi
        te_hi = tr_hi + test_size
        out.append(((tr_lo, tr_hi), (te_lo, te_hi)))
        start += step
    return out


def purged_kfold_windows(
    n: int,
    n_splits: int,
    embargo: int = 0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """生成 Purged K-Fold 的 (train_idx, test_idx) 序列。

    每折连续切分做测试集；训练集 = 其余位置，但**剔除测试集前后各 embargo 期**，
    防止 forward return 跨边界造成标签泄露（López de Prado《Advances in Financial
    Machine Learning》的 purging + embargo 思想）。

    Args:
        n: 序列总长度。
        n_splits: 折数，≥ 2。
        embargo: 测试集前后各剔除的期数，≥ 0。

    Returns:
        ``[(train_idx, test_idx), ...]``，元素为整数位置 ndarray。``n < n_splits`` 返回空。
    """
    if n_splits < 2:
        raise ValueError("n_splits 必须 ≥ 2")
    if embargo < 0:
        raise ValueError("embargo 必须 ≥ 0")
    if n < n_splits:
        return []

    bounds = np.linspace(0, n, n_splits + 1).astype(int)
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(n_splits):
        te_lo, te_hi = int(bounds[k]), int(bounds[k + 1])
        if te_hi <= te_lo:
            continue
        purge_lo = max(0, te_lo - embargo)
        purge_hi = min(n, te_hi + embargo)
        mask = np.ones(n, dtype=bool)
        mask[purge_lo:purge_hi] = False  # 测试集 + 前后 embargo 都不进训练集
        train_idx = np.nonzero(mask)[0]
        test_idx = np.arange(te_lo, te_hi)
        out.append((train_idx, test_idx))
    return out


def _ic_summary_on(
    F: pd.DataFrame, fwd: pd.DataFrame, rows: np.ndarray | slice
) -> dict:
    """在给定行子集上算横截面 IC 序列并汇总。"""
    sub_f = F.iloc[rows]
    sub_r = fwd.iloc[rows]
    ic = metrics.cross_sectional_ic(sub_f, sub_r)
    return metrics.ic_summary(ic)


def oos_validation_report(
    F: pd.DataFrame,
    close: pd.DataFrame,
    *,
    forward_periods: list[int],
    scheme: str,
    train_size: int | None = None,
    test_size: int | None = None,
    step: int | None = None,
    anchored: bool = False,
    n_splits: int = 5,
    embargo: int = 0,
) -> dict:
    """对 (因子宽表, close 宽表) 做样本外验证，返回逐窗口 + 汇总指标。

    Args:
        F: 因子宽表，index=trade_date、columns=symbol。
        close: qfq close 宽表，与 F 同构（上游 inner-align）。
        forward_periods: 前瞻期；用第一个作为 IC 计算的基准期。
        scheme: ``"walk_forward"`` 或 ``"purged_kfold"``。
        train_size/test_size/step/anchored: walk_forward 参数。
        n_splits/embargo: purged_kfold 参数。

    Returns:
        dict：``{scheme, n_windows, windows:[...], summary:{is_ic_mean, oos_ic_mean,
        oos_ic_std, oos_ic_ir, ic_decay_ratio}}``。``ic_decay_ratio = oos/is``，越接近 1
        越稳健，越小（甚至变号）越提示过拟合。
    """
    fwd_periods = [int(x) for x in forward_periods]
    base = fwd_periods[0] if fwd_periods else 1
    fwd = close.shift(-base) / close - 1
    n = len(F.index)

    windows_out: list[dict] = []

    if scheme == "walk_forward":
        if train_size is None or test_size is None:
            raise ValueError("walk_forward 需要 train_size 和 test_size")
        for (tr_lo, tr_hi), (te_lo, te_hi) in walk_forward_windows(
            n, train_size, test_size, step, anchored
        ):
            tr_sum = _ic_summary_on(F, fwd, slice(tr_lo, tr_hi))
            te_sum = _ic_summary_on(F, fwd, slice(te_lo, te_hi))
            windows_out.append({
                "train_range": [tr_lo, tr_hi],
                "test_range": [te_lo, te_hi],
                "train_dates": [
                    str(F.index[tr_lo].date()), str(F.index[tr_hi - 1].date())
                ],
                "test_dates": [
                    str(F.index[te_lo].date()), str(F.index[te_hi - 1].date())
                ],
                "train_ic_mean": _f(tr_sum["ic_mean"]),
                "test_ic_mean": _f(te_sum["ic_mean"]),
                "test_ic_ir": _f(te_sum["ic_ir"]),
            })
    elif scheme == "purged_kfold":
        for fold, (tr_idx, te_idx) in enumerate(
            purged_kfold_windows(n, n_splits, embargo)
        ):
            tr_sum = _ic_summary_on(F, fwd, tr_idx)
            te_sum = _ic_summary_on(F, fwd, te_idx)
            windows_out.append({
                "fold": fold,
                "train_size": int(len(tr_idx)),
                "test_range": [int(te_idx[0]), int(te_idx[-1]) + 1],
                "test_dates": [
                    str(F.index[te_idx[0]].date()),
                    str(F.index[te_idx[-1]].date()),
                ],
                "train_ic_mean": _f(tr_sum["ic_mean"]),
                "test_ic_mean": _f(te_sum["ic_mean"]),
                "test_ic_ir": _f(te_sum["ic_ir"]),
            })
    else:
        raise ValueError(
            f"scheme 必须是 'walk_forward' 或 'purged_kfold'，收到 {scheme!r}"
        )

    test_ics = [w["test_ic_mean"] for w in windows_out if w["test_ic_mean"] is not None]
    train_ics = [
        w["train_ic_mean"] for w in windows_out if w["train_ic_mean"] is not None
    ]
    oos_ic_mean = float(np.mean(test_ics)) if test_ics else None
    oos_ic_std = float(np.std(test_ics, ddof=1)) if len(test_ics) > 1 else None
    oos_ic_ir = (
        oos_ic_mean / oos_ic_std
        if (oos_ic_mean is not None and oos_ic_std not in (None, 0.0))
        else None
    )
    is_ic_mean = float(np.mean(train_ics)) if train_ics else None
    decay = (
        oos_ic_mean / is_ic_mean
        if (is_ic_mean not in (None, 0.0) and oos_ic_mean is not None)
        else None
    )

    return {
        "scheme": scheme,
        "n_windows": len(windows_out),
        "windows": windows_out,
        "summary": {
            "is_ic_mean": _f(is_ic_mean),
            "oos_ic_mean": _f(oos_ic_mean),
            "oos_ic_std": _f(oos_ic_std),
            "oos_ic_ir": _f(oos_ic_ir),
            "ic_decay_ratio": _f(decay),
        },
    }
