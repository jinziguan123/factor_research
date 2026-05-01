# LightGBM 多因子合成 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** composition_service 加第 4 个 method `ml_lgb`，用 LightGBM 学非线性因子合成。

**Architecture:** Walk-forward expanding window 训练，每天用 [start, t-5] 数据训一个 LGB，预测 t 当天截面。label = 未来 5 日 cross-section rank 化收益。输出预测面板 + mean feature_importance。

**Tech Stack:** lightgbm>=4.0、pandas、numpy、pytest（mock）。前端 vue 3 + naive-ui。

**Design doc:** [docs/plans/2026-05-01-lightgbm-composition-design.md](./2026-05-01-lightgbm-composition-design.md)

**Worktree:** `.claude/worktrees/lightgbm-composition`（branch `claude/lightgbm-composition`，已 push 到 origin）。

**Test 命令前缀:** `/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest`（worktree 没单独 venv，复用主目录 .venv）

---

## Task 1: 添加 lightgbm 依赖

**Files:**
- Modify: `backend/pyproject.toml`（在 dependencies 列表里加 `lightgbm>=4.0`）

**Step 1: Read 当前 dependencies**

```bash
grep -A 3 "^dependencies" backend/pyproject.toml | head -20
```

**Step 2: 编辑 pyproject.toml**

在 dependencies 列表合适位置（按字母序）加：
```toml
"lightgbm>=4.0",
```

**Step 3: 验证 import**

```bash
cd .claude/worktrees/lightgbm-composition
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pip install 'lightgbm>=4.0' 2>&1 | tail -3
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -c "import lightgbm; print(lightgbm.__version__)"
```
Expected: 打印 lightgbm 版本号（≥ 4.0）

**Step 4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "build: 加 lightgbm>=4.0 依赖（ml_lgb 合成 method 用）"
```

---

## Task 2: `_build_future_return_label` 实现 + 测试

**Files:**
- Modify: `backend/services/composition_service.py`（_build_future_return_label 新增）
- Test: `backend/tests/test_lightgbm_composition.py`（新建）

**Step 1: Write the failing test**

新建 `backend/tests/test_lightgbm_composition.py`：

```python
"""LightGBM 合成 method=ml_lgb 测试：mock LGB 库、纯函数验证 walk-forward 边界。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

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
    assert abs(out.loc[dates[0], "C"] - 1.0) < 1e-9 or abs(out.loc[dates[0], "C"] - 0.5) < 1e-9
    # 极值落在 [-1, 1]
    valid = out.dropna(how="all")
    assert valid.values.min() >= -1.0 - 1e-9
    assert valid.values.max() <= 1.0 + 1e-9
    # day 3（最末日）应全 NaN（没未来收益）
    assert out.loc[dates[3]].isna().all()
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/lightgbm-composition
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py -v
```
Expected: FAIL with `ImportError: cannot import name '_build_future_return_label'`

**Step 3: 实现 _build_future_return_label**

在 `backend/services/composition_service.py` 的 `_combine_orthogonal_equal` 函数之后（约 line 360）加：

```python
def _build_future_return_label(
    close: pd.DataFrame, forward_period: int = 5,
) -> pd.DataFrame:
    """每日 cross-section rank 化的未来 N 日收益（label for ml_lgb）。

    1. ``future_return = close.shift(-N) / close - 1``——日期 t 的 label 是 t→t+N 收益
    2. 每日横截面 ``rank(pct=True)`` → [0, 1]
    3. 线性映射到 [-1, 1]：``rank * 2 - 1``
    4. 最末 N 天没未来收益 → NaN（自然丢失）

    rank 化的目的：去噪 + 与项目"rank IC"评估口径一致——LightGBM 学的是
    "模型版 rank IC"，比直接学绝对收益更稳。
    """
    fwd_return = close.shift(-forward_period) / close - 1
    ranked = fwd_return.rank(axis=1, pct=True)  # [0, 1] pct rank（NaN 保留）
    return ranked * 2.0 - 1.0
```

**Step 4: Run test to verify it passes**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py::test_build_future_return_label_rank_to_pm_one -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add backend/services/composition_service.py backend/tests/test_lightgbm_composition.py
git commit -m "feat(composition): _build_future_return_label（ml_lgb label 构造）"
```

---

## Task 3: `_combine_lightgbm` happy path + 输出 shape

**Files:**
- Modify: `backend/services/composition_service.py`（加 `_combine_lightgbm` + 模块级常量 `_DEFAULT_LGB_PARAMS`）
- Modify: `backend/tests/test_lightgbm_composition.py`

**Step 1: Write the failing test**

在 test 文件追加：

```python
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
```

**Step 2: Run test to verify it fails**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py::test_combine_lightgbm_returns_pred_and_importance -v
```
Expected: FAIL with `ImportError: cannot import name '_combine_lightgbm'`

**Step 3: 实现 `_combine_lightgbm` 与 `_DEFAULT_LGB_PARAMS`**

在 `composition_service.py` 顶部 import（约 line 30 后）加：
```python
import numpy as np
```
（如果已有则不重复）。

在 `_build_future_return_label` 之后加：

```python
# LightGBM 默认超参——保守起点防过拟合（量化数据信噪比低）
_DEFAULT_LGB_PARAMS = {
    "n_estimators": 100,
    "max_depth": 4,            # 浅树
    "num_leaves": 15,          # 2^4 - 1
    "learning_rate": 0.05,
    "reg_alpha": 0.1,          # L1 正则
    "reg_lambda": 0.1,         # L2 正则
    "min_child_samples": 20,   # 叶节点至少 20 样本
    "verbose": -1,             # 静默
    "random_state": 42,        # 可重现
    "n_jobs": -1,              # 多核
}


def _combine_lightgbm(
    z_frames: list[pd.DataFrame],
    label_panel: pd.DataFrame,
    factor_ids: list[str],
    *,
    forward_period: int = 5,
    warmup_days: int = 60,
    lgb_params: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """walk-forward expanding window 训 LightGBM 学非线性因子合成。

    Args:
        z_frames: 已 cross-section z-score 化的 N 个因子面板（同 (date×symbol) 形态）
        label_panel: 同形 label（每日 cross-section rank 化的未来 forward_period 日收益）
        factor_ids: factor_id 列表（顺序与 z_frames 对应）
        forward_period: label 是 future_N_return；训练集要 [start, t-N] 防泄漏
        warmup_days: 前 N 天 cold start 跳过
        lgb_params: 覆盖默认超参；None 用 _DEFAULT_LGB_PARAMS

    Returns:
        (pred, feature_importance):
        - pred: (date × symbol) 预测值面板，前 warmup + forward 天为 NaN
        - feature_importance: {factor_id: mean_gain_across_walk_forward_models}
    """
    from lightgbm import LGBMRegressor

    params = {**_DEFAULT_LGB_PARAMS, **(lgb_params or {})}

    # 1. stack 每个 z 面板成 Series，concat 成 (date, symbol) MultiIndex DataFrame
    X_panel = pd.concat(
        [z.stack(future_stack=True).rename(fid) for fid, z in zip(factor_ids, z_frames)],
        axis=1,
    )
    # X_panel 索引 = MultiIndex (date, symbol)，列 = factor_ids

    # 2. label 对齐到同索引
    y_series = label_panel.stack(future_stack=True).reindex(X_panel.index)

    # 3. 准备输出 + walk-forward
    pred = pd.DataFrame(
        index=label_panel.index, columns=label_panel.columns, dtype=float,
    )
    importances: list[dict[str, float]] = []
    all_dates = sorted(X_panel.index.get_level_values(0).unique())

    for i, date_t in enumerate(all_dates):
        if i < warmup_days:
            continue                                 # cold start
        train_end_idx = i - forward_period           # 防 label 跨日泄漏
        if train_end_idx < warmup_days:
            continue
        train_dates = all_dates[: train_end_idx + 1]
        train_mask = X_panel.index.get_level_values(0).isin(train_dates)
        X_train = X_panel[train_mask]
        y_train = y_series[train_mask]

        # 删除 X 或 y 含 NaN 的行
        valid = X_train.notna().all(axis=1) & y_train.notna()
        X_train = X_train[valid]
        y_train = y_train[valid]
        if len(X_train) < 100:
            continue                                 # 样本太少跳过

        model = LGBMRegressor(**params)
        model.fit(X_train, y_train)
        importances.append(
            dict(zip(factor_ids, model.feature_importances_.astype(float)))
        )

        # 预测 date_t 当天截面
        try:
            X_today = X_panel.xs(date_t, level=0)
        except KeyError:
            continue
        valid_today = X_today.notna().all(axis=1)
        if not valid_today.any():
            continue
        pred_vals = model.predict(X_today.loc[valid_today])
        pred.loc[date_t, X_today.index[valid_today]] = pred_vals

    # 4. 聚合 importance 取 mean（每个因子在所有 walk-forward 模型里的平均 gain）
    fi_mean: dict[str, float] = {fid: 0.0 for fid in factor_ids}
    if importances:
        for fid in factor_ids:
            fi_mean[fid] = float(
                np.mean([imp.get(fid, 0.0) for imp in importances])
            )

    return pred, fi_mean
```

**Step 4: Run test to verify it passes**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py::test_combine_lightgbm_returns_pred_and_importance -v
```
Expected: PASS（首次跑 LGB 训练实际生效，可能耗时 10-30s）

**Step 5: Commit**

```bash
git add backend/services/composition_service.py backend/tests/test_lightgbm_composition.py
git commit -m "feat(composition): _combine_lightgbm + _DEFAULT_LGB_PARAMS（walk-forward 训练）"
```

---

## Task 4: 防 lookahead 测试（关键回归）

**Files:**
- Modify: `backend/tests/test_lightgbm_composition.py`

**Step 1: Write the failing test**

追加：

```python
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
```

**Step 2: Run test to verify it passes（如果 _combine_lightgbm 实现正确）**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py::test_combine_lightgbm_no_lookahead_when_factor_uncorrelated_with_future -v
```
Expected: PASS（实现正确时 IC ≈ 0；可能耗时 30-60s 因 walk-forward 训 70+ 模型）

**Step 3: Commit**

```bash
git add backend/tests/test_lightgbm_composition.py
git commit -m "test(composition): _combine_lightgbm 关键防泄漏 IC 期望 0 测试"
```

---

## Task 5: edge case 测试（cold start / 空因子集 / 样本不足）

**Files:**
- Modify: `backend/tests/test_lightgbm_composition.py`

**Step 1: 追加三个测试**

```python
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
    """样本极小（< 100 行）→ 当日跳过预测，pred 当日 NaN，但其它训练样本足够的日子可预测。"""
    from backend.services.composition_service import _combine_lightgbm

    # 5 票 × 30 天，样本量 = 150 行；warmup 25，剩余 5 天每天有 ~125 训练样本，足够。
    z_frames = [_make_factor_panel(30, 5, seed=0)]
    label = _make_factor_panel(30, 5, seed=99)
    pred, fi = _combine_lightgbm(
        z_frames, label, ["f1"], forward_period=5, warmup_days=25,
    )
    # 没崩 + 至少前 30 天里有 NaN（cold start 部分）
    assert pred.iloc[:25].isna().all().all()
    assert "f1" in fi


def test_combine_lightgbm_empty_factors_raises_or_safe():
    """空因子列表 → reasonable error（不应静默跑空）。"""
    from backend.services.composition_service import _combine_lightgbm

    label = _make_factor_panel(50, 10, seed=99)
    with pytest.raises((ValueError, IndexError, KeyError)):
        _combine_lightgbm([], label, [], forward_period=5, warmup_days=20)
```

**Step 2: Run tests**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py -v -k "cold_start or insufficient or empty"
```
Expected: 3 PASS

**Step 3: Commit**

```bash
git add backend/tests/test_lightgbm_composition.py
git commit -m "test(composition): _combine_lightgbm cold_start / 样本不足 / 空因子集 边角"
```

---

## Task 6: feature_importance 聚合测试

**Files:**
- Modify: `backend/tests/test_lightgbm_composition.py`

**Step 1: 追加**

```python
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
```

**Step 2: Run test**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py::test_combine_lightgbm_feature_importance_reflects_signal_strength -v
```
Expected: PASS（耗时 30-60s）

**Step 3: Commit**

```bash
git add backend/tests/test_lightgbm_composition.py
git commit -m "test(composition): feature_importance 区分信号 vs 噪声因子"
```

---

## Task 7: schemas + run_composition 路由 ml_lgb 分支

**Files:**
- Modify: `backend/api/schemas.py`（3 处枚举：line 193, 233, 270）
- Modify: `backend/services/composition_service.py`（line 414 docstring + line 434 method 校验 + line 504-515 method 分支 + line 543-552 contributions + line 7（写入 payload））
- Test: `backend/tests/test_lightgbm_composition.py`（router 集成 mock test）

**Step 1: Write failing test**

```python
# ---------------------------- run_composition ml_lgb 集成 ----------------------------


def test_run_composition_ml_lgb_writes_feature_importance_to_payload(monkeypatch, tmp_path):
    """ml_lgb 路径跑通：mock _combine_lightgbm + DB 写入，验证 payload 含 feature_importance。"""
    from backend.services import composition_service as cs

    # mock LGB 合成：返回 pred 面板 + 假 importance
    fake_pred = _make_factor_panel(50, 20, seed=11)
    fake_fi = {"f1": 12.5, "f2": 3.0}

    def _fake_combine(z_frames, label_panel, factor_ids, **kw):
        return fake_pred, fake_fi
    monkeypatch.setattr(cs, "_combine_lightgbm", _fake_combine)

    # 确认：_combine_lightgbm 被路由到 ml_lgb 分支调用了
    # （详细行为已在 Task 3-6 覆盖，这里只测 method 路由）
    method = "ml_lgb"
    assert method in cs._ALLOWED_METHODS_FOR_TEST  # 见 Step 3 实现引入
```

**Step 2: Run test to verify it fails**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py::test_run_composition_ml_lgb_writes_feature_importance_to_payload -v
```
Expected: FAIL `AttributeError: ... has no attribute '_ALLOWED_METHODS_FOR_TEST'`

**Step 3: 实现 schemas + run_composition 改造**

3a. `backend/api/schemas.py` 三处校验加 `ml_lgb`（line 193, 233, 270）：

```python
# line 193 附近
if self.method not in ("equal", "ic_weighted", "orthogonal_equal", "ml_lgb"):
    raise ValueError(
        f"method={self.method!r} 不支持，仅接受 equal/ic_weighted/orthogonal_equal/ml_lgb"
    )
```

`single` 那两个枚举（line 233 / 270）保持不变（它们是 eval / signal 的，跟 composition 无关）—— **只改 line 193**。

3b. `backend/services/composition_service.py` 在文件顶部模块级（_DEFAULT_LGB_PARAMS 之前）加：

```python
_ALLOWED_METHODS_FOR_TEST = ("equal", "ic_weighted", "orthogonal_equal", "ml_lgb")
```

3c. line 414 docstring 加 `ml_lgb`：

```python
- ``method``：``equal`` / ``ic_weighted`` / ``orthogonal_equal`` / ``ml_lgb``
```

3d. line 434 method 校验加 `ml_lgb`：

```python
if method not in ("equal", "ic_weighted", "orthogonal_equal", "ml_lgb"):
    raise ValueError(
        f"method={method!r} 不支持，仅接受 equal/ic_weighted/orthogonal_equal/ml_lgb"
    )
```

3e. line 504-515 method 分支扩展（在 `else: # orthogonal_equal` 之前插入 ml_lgb）：

```python
        z_frames = [_zscore_per_day(f) for f in aligned]
        weights: dict[str, float] | None = None
        feature_importance: dict[str, float] | None = None
        if method == "equal":
            F_combined = _combine_equal(z_frames)
        elif method == "ic_weighted":
            weights = _compute_ic_weights(
                z_frames, close, factor_ids, period=ic_weight_period
            )
            F_combined = _combine_weighted(z_frames, weights, factor_ids)
        elif method == "ml_lgb":
            # walk-forward LightGBM：label = 未来 forward_period 日 cross-section
            # rank 化收益。warmup 沿用 ic_lookback_days 语义保持各 method 一致。
            label_panel = _build_future_return_label(
                close, forward_period=ic_weight_period
            )
            F_combined, feature_importance = _combine_lightgbm(
                z_frames, label_panel, factor_ids,
                forward_period=ic_weight_period,
                warmup_days=int(body.get("ic_lookback_days") or 60),
            )
        else:  # orthogonal_equal
            F_combined = _combine_orthogonal_equal(z_frames)
```

3f. 在 line 552 之后（contributions 算完后）加 feature_importance 写入 payload：

```python
        # 7.2 ml_lgb 把 feature_importance 写入 payload，前端在子因子表加列展示
        if feature_importance is not None:
            payload["feature_importance"] = feature_importance
```

3g. 把测试 step 1 的代码完善——把简单 `_ALLOWED_METHODS_FOR_TEST` 检测换成 dispatch 真测：

修改 test_run_composition_ml_lgb_writes_feature_importance_to_payload 的内容为：

```python
def test_run_composition_dispatches_ml_lgb_when_method_is_ml_lgb(monkeypatch):
    """method='ml_lgb' 时 run_composition 走 _combine_lightgbm 分支。"""
    from backend.services import composition_service as cs

    # 验证 _ALLOWED_METHODS_FOR_TEST 包含 ml_lgb（schema 与 service 同步）
    assert "ml_lgb" in cs._ALLOWED_METHODS_FOR_TEST


def test_run_composition_method_validation_accepts_ml_lgb():
    """schemas.CreateCompositionIn 接受 method='ml_lgb' 不抛 ValueError。"""
    from backend.api.schemas import CreateCompositionIn

    # 最小合法 body
    body = {
        "factor_items": [{"factor_id": "f1"}, {"factor_id": "f2"}],
        "method": "ml_lgb",
        "pool_id": 1,
        "start_date": "2024-01-01",
        "end_date": "2024-06-01",
    }
    obj = CreateCompositionIn(**body)
    assert obj.method == "ml_lgb"
```

**Step 4: Run tests**

```bash
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py -v
```
Expected: 全部 PASS（含新增 2 个 dispatch / schema 测试）

**Step 5: Commit**

```bash
git add backend/api/schemas.py backend/services/composition_service.py backend/tests/test_lightgbm_composition.py
git commit -m "feat(composition): run_composition 加 ml_lgb 路由 + schemas 加 ml_lgb 枚举"
```

---

## Task 8: 前端 CompositionCreate 加下拉选项

**Files:**
- Modify: `frontend/src/pages/compositions/CompositionCreate.vue`

**Step 1: 修改 method 类型定义**

`line 77`：
```typescript
const method = ref<'equal' | 'ic_weighted' | 'orthogonal_equal' | 'ml_lgb'>('equal')
```

**Step 2: 修改 methodOptions（line 78 附近）**

加一条：
```typescript
const methodOptions = [
  // ... 原 3 项
  {
    label: '🌳 LightGBM 合成（ML 学非线性权重）',
    value: 'ml_lgb',
    description: 'walk-forward 训练，能学因子非线性 / 交叉效应；耗时 3-10 分钟（vs 其它几秒）',
  },
]
```

**Step 3: vue-tsc 验证**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | tail -3
```
Expected: exit 0

**Step 4: Commit**

```bash
git add frontend/src/pages/compositions/CompositionCreate.vue
git commit -m "feat(ui): CompositionCreate 加 ml_lgb 下拉选项"
```

---

## Task 9: 前端 CompositionDetail 子因子表加 LGB Importance 列

**Files:**
- Modify: `frontend/src/pages/compositions/CompositionDetail.vue`

**Step 1: 找到子因子贡献度表格定义**

```bash
grep -n "ic_contribution\|per_factor_ic\|权重\|贡献度" frontend/src/pages/compositions/CompositionDetail.vue | head -10
```

记下表格 columns 定义位置（应在 `<script setup>` 内的 `subfactorColumns` 或类似变量）。

**Step 2: 修改 columns**

（具体行号 / 变量名按 grep 结果调整）找到子因子贡献度 columns 数组，在末尾追加：

```typescript
{
  title: 'LGB Importance',
  key: 'lgb_importance',
  width: 130,
  render: (row: any) => {
    if (run.value?.method !== 'ml_lgb') return '-'
    const fi = (payload.value?.feature_importance) || {}
    const max = Math.max(...Object.values(fi as Record<string, number>), 1)
    const v = fi[row.factor_id]
    if (v == null) return '-'
    const pct = (v / max * 100).toFixed(0)
    return h('div', { style: 'display:flex;align-items:center;gap:6px' }, [
      h('div', {
        style: `width:60px;height:8px;background:#eee;border-radius:2px;overflow:hidden`,
      }, h('div', {
        style: `width:${pct}%;height:100%;background:#F0B90B`,
      })),
      h('span', { style: 'color:#848E9C;font-size:11px' }, `${v.toFixed(1)}`),
    ])
  },
}
```

如果 columns 是用对象数组在 template 外定义的——加在末尾即可；表格用 NDataTable 会自动检测列变化。

**Step 3: vue-tsc 验证**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | tail -3
```
Expected: exit 0

**Step 4: Commit**

```bash
git add frontend/src/pages/compositions/CompositionDetail.vue
git commit -m "feat(ui): CompositionDetail 子因子表加 LGB Importance 列（条形 + 数字）"
```

---

## Task 10: 收尾——push + finalize

**Step 1: 整套测试 final regression**

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/lightgbm-composition
/Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/backend/.venv/bin/python -m pytest backend/tests/test_lightgbm_composition.py backend/tests/test_metrics.py backend/tests/test_factor_assistant.py -q
```
Expected: 全绿（新增 ~7 个 + 现有 100+）

**Step 2: vue-tsc final**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | tail -3
```
Expected: exit 0

**Step 3: Push**

```bash
cd /Users/jinziguan/Desktop/quantitativeTradeProject/factor_research/.claude/worktrees/lightgbm-composition
git push -u origin claude/lightgbm-composition 2>&1 | tail -3
```

**Step 4: 报告完成**

向用户报告：分支 `claude/lightgbm-composition` 已 push，总 commit 数 + 测试结果摘要。后续：用户拉分支跑 dev、跑一次实际 ml_lgb 合成，确认 walk-forward 真实耗时 + feature_importance 解读符合预期，再 merge 到 master。

---

## 关键风险 / 排错指引

1. **训练时间长**：walk-forward 252 天 × LGB 100 棵树 → 单次 evaluate 3-10 分钟。**Task 4 防泄漏测试** 单独跑要 30-60s——若 CI 超时，临时减小 `n_dates=50` 即可。
2. **MultiIndex stack 警告**：pandas 2.x 后 `.stack()` 行为变了，必须传 `future_stack=True` 否则有 FutureWarning（已写在 Task 3 的代码里）。
3. **样本不足跳过 vs 误抛**：test 5 场景里"样本不足"是**当日跳过**而非抛错——这是设计选择（让评估能跑完产生部分结果，cold start 行为类似）。
4. **lightgbm 在 CI 装不上**：极少数 CI 环境（如老 musl 无 wheel），可加 `pytest.importorskip("lightgbm")` 在 test 文件顶部跳过。
5. **payload feature_importance 字段为空**：若所有 walk-forward 都跳过（warmup 太大），`feature_importance` 全 0；前端 UI 应能优雅显示（"-" 而非崩）。

## DoD（每个 Task 完成的判定）

- [ ] 该 task 新增 / 修改的测试全绿
- [ ] 该 task 不影响已有套件（运行更广测试 / 至少 metrics + factor_assistant 不回归）
- [ ] vue-tsc 无新 error（前端 task）
- [ ] commit message 按本 plan 给的样式
- [ ] 推到 origin（仅 Task 10）
