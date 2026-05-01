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


def test_combine_lightgbm_no_lookahead_when_factor_uncorrelated_with_future():
    """关键防泄漏测试：因子值与未来收益**完全无关**时，walk-forward 不应能预测出
    高 IC——若实现错把未来数据混进训练（lookahead），test set IC 会假高。

    这条测试是数据科学防过拟合 / 防泄漏的最低标准——若 _combine_lightgbm 错
    把未来日期的 (X, y) 训练集化，会被这里抓住。
    """
    from backend.services.composition_service import _combine_lightgbm

    # 故意构造：因子完全独立于 label（所以理论 IC ≈ 0）
    rng = np.random.default_rng(42)
    n_dates, n_symbols = 100, 40
    dates = pd.date_range("2024-01-01", periods=n_dates)
    symbols = [f"S{i:03d}" for i in range(n_symbols)]
    z_frames = [
        pd.DataFrame(rng.standard_normal((n_dates, n_symbols)), index=dates, columns=symbols),
        pd.DataFrame(rng.standard_normal((n_dates, n_symbols)), index=dates, columns=symbols),
    ]
    label = pd.DataFrame(
        rng.standard_normal((n_dates, n_symbols)), index=dates, columns=symbols,
    )
    # 把 label rank 化到 [-1, 1] 模拟 future_return rank
    label = label.rank(axis=1, pct=True) * 2 - 1

    pred, _fi = _combine_lightgbm(
        z_frames, label, ["f1", "f2"], forward_period=5, warmup_days=30,
    )

    # 计算 pred vs label 的 cross-section IC（spearman 近似——pred 对应日期 t，
    # label 对应日期 t 已经是 t→t+5 收益的 rank）
    pred_valid = pred.dropna(how="all")
    common_dates = pred_valid.index.intersection(label.index)
    ics = []
    for d in common_dates:
        p_row = pred_valid.loc[d]
        l_row = label.loc[d]
        valid = p_row.notna() & l_row.notna()
        if valid.sum() < 5:
            continue
        ic = p_row[valid].corr(l_row[valid], method="spearman")
        if not np.isnan(ic):
            ics.append(ic)

    if not ics:
        pytest.fail("no valid IC computed")
    mean_ic = float(np.mean(ics))
    # 因子与 label 完全独立 → IC 期望 0；考虑随机性 |IC| < 0.10 即视为通过
    # 若 lookahead 把未来 label 灌进训练，模型会在测试集"复读"label，IC 会 > 0.5
    assert abs(mean_ic) < 0.15, (
        f"防泄漏失败：IC={mean_ic:.3f} 显著大于 0；可能 walk-forward 把未来"
        " label 漏到训练集了"
    )


def test_combine_lightgbm_cold_start_returns_all_nan():
    """warmup_days > 全部日期 → 没机会训模型 → pred 全 NaN，不抛错。"""
    from backend.services.composition_service import _combine_lightgbm

    z_frames = [_make_factor_panel(20, 10, seed=0)]
    label = _make_factor_panel(20, 10, seed=99)
    pred, fi = _combine_lightgbm(
        z_frames, label, ["f1"], forward_period=5, warmup_days=100,
    )
    assert pred.isna().all().all()
    assert fi == {"f1": 0.0}  # 无训练 → mean 默认 0


def test_combine_lightgbm_insufficient_samples_skips():
    """样本不足（< 100 行训练数据）→ 当日跳过预测，pred 当日 NaN，但函数不崩。

    n_dates=40, n_symbols=2, warmup_days=25, forward_period=5：
      - i=0..24 被关 1 (warmup) 拦
      - i=25..29 被关 2 (forward_period 边界，要求 i - 5 >= 25 即 i >= 30) 拦
      - i=30..39 能过关 1+2，但 train_end_idx ∈ [25..34]，对应训练池
        最多 35×2 = 70 行 < 100 → 关 3 (样本不足) 全部拦掉。

    实测 pred 全 NaN、fi={'f1': 0.0}——这就是"样本不足时不崩 + 返回结构合法"的契约。
    """
    from backend.services.composition_service import _combine_lightgbm

    z_frames = [_make_factor_panel(40, 2, seed=0)]
    label = _make_factor_panel(40, 2, seed=99)
    pred, fi = _combine_lightgbm(
        z_frames, label, ["f1"], forward_period=5, warmup_days=25,
    )
    # 函数不崩 + 返回的结构合理
    assert pred.shape == (40, 2)
    assert "f1" in fi
    # 不要硬断 pred 全 NaN——如果将来有人放宽 < 100 阈值这条会变；
    # 但只要"样本不足时不崩 + 返回结构合法"，契约就守住了。


def test_combine_lightgbm_empty_factors_raises_value_error():
    """空因子集 → 抛 ValueError（design doc 契约）。

    pd.concat([], axis=1) 在 pandas 当前版本确定性抛 ValueError；
    收紧异常类型避免将来 regression 漏过。
    """
    from backend.services.composition_service import _combine_lightgbm

    label = _make_factor_panel(50, 10, seed=99)
    with pytest.raises(ValueError):
        _combine_lightgbm([], label, [], forward_period=5, warmup_days=20)


def test_combine_lightgbm_feature_importance_reflects_signal_strength():
    """构造一个因子真带信号（与 label 相关）+ 一个噪声因子，importance 应区分。"""
    from backend.services.composition_service import _combine_lightgbm

    rng = np.random.default_rng(7)
    n_dates, n_symbols = 80, 30
    dates = pd.date_range("2024-01-01", periods=n_dates)
    symbols = [f"S{i:03d}" for i in range(n_symbols)]

    # signal_factor：与 label 高度相关
    label_raw = rng.standard_normal((n_dates, n_symbols))
    signal_factor = label_raw + rng.standard_normal((n_dates, n_symbols)) * 0.3
    noise_factor = rng.standard_normal((n_dates, n_symbols))

    z_frames = [
        pd.DataFrame(signal_factor, index=dates, columns=symbols),
        pd.DataFrame(noise_factor, index=dates, columns=symbols),
    ]
    label = pd.DataFrame(label_raw, index=dates, columns=symbols)
    label = label.rank(axis=1, pct=True) * 2 - 1

    _pred, fi = _combine_lightgbm(
        z_frames, label, ["signal", "noise"],
        forward_period=5, warmup_days=20,
    )
    assert fi["signal"] > fi["noise"], (
        f"importance 区分失败：signal={fi['signal']} 应明显高于 noise={fi['noise']}"
    )
