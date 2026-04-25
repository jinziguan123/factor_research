# Alphalens 可选增强视角 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `evaluate_factor_panel()` 中集成 Alphalens，产出三项增量指标（排名自相关、分组累积净值、alpha/beta），写入 `payload["alphalens"]` 命名空间。

**Architecture:** 新增 `_build_alphalens_extras(F, close, fwd_periods, n_groups)` 纯函数，内部用 `get_clean_factor_and_forward_returns` 转换数据格式后调用 Alphalens API。整体 try/except 包裹，失败不影响主管线。在 `evaluate_factor_panel` 组装 payload 后调用并追加。

**Tech Stack:** alphalens-reloaded 0.4.6, pandas, numpy, statsmodels (alphalens 依赖)

---

### Task 1: 将 alphalens-reloaded 移入主依赖

**Files:**
- Modify: `backend/pyproject.toml:38-43`

**Step 1: 修改 pyproject.toml**

把 `alphalens-reloaded>=0.4.6` 从 `[dependency-groups] dev` 移到 `[project] dependencies`：

```toml
# dependencies 末尾加：
    "alphalens-reloaded>=0.4.6",
```

从 dev 组删除该行。

**Step 2: 运行 uv sync 验证依赖解析**

Run: `uv sync --project backend`
Expected: 无报错

**Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "build: alphalens-reloaded 从 dev 移入主依赖"
```

---

### Task 2: 写 _build_alphalens_extras 的失败测试

**Files:**
- Test: `backend/tests/test_metrics.py`

**Step 1: 写三项指标 + 容错的测试**

在 `TestAlphalensCrossValidation` 后面新增 `TestAlphalensExtras` 类：

```python
class TestAlphalensExtras:
    """_build_alphalens_extras 端到端测试。"""

    @pytest.fixture()
    def close(self):
        return _mk_close(n_dates=120, n_syms=20, seed=42)

    @pytest.fixture()
    def factor(self, close):
        rng = np.random.default_rng(99)
        return pd.DataFrame(
            rng.standard_normal(close.shape), index=close.index, columns=close.columns,
        )

    def test_returns_dict_with_three_keys(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras
        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        assert "rank_autocorrelation" in result
        assert "group_cumulative_returns" in result
        assert "alpha_beta" in result

    def test_rank_autocorrelation_format(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras
        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        ra = result["rank_autocorrelation"]
        assert "dates" in ra and "values" in ra
        assert len(ra["dates"]) == len(ra["values"])
        assert len(ra["dates"]) > 50  # 120 天至少 50+ 个自相关点

    def test_group_cumulative_returns_format(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras
        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        gcr = result["group_cumulative_returns"]
        assert "dates" in gcr
        group_keys = [k for k in gcr if k.startswith("g")]
        assert len(group_keys) == 5  # 5 个分组
        assert all(len(gcr[k]) == len(gcr["dates"]) for k in group_keys)

    def test_group_cumulative_returns_is_demeaned(self, factor, close):
        """去均值口径：各组首日累积净值之和应接近 n_groups（各组从 1 起步且收益去均值后总和趋零）。"""
        from backend.services.eval_service import _build_alphalens_extras
        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        gcr = result["group_cumulative_returns"]
        group_keys = sorted(k for k in gcr if k.startswith("g"))
        # 最后一天各组净值之和 ≈ n_groups（去均值后多空对冲，总和接近初始值）
        final_sum = sum(gcr[k][-1] for k in group_keys if gcr[k][-1] is not None)
        assert abs(final_sum - 5.0) < 1.0, f"去均值后各组终值之和应 ≈5，实际 {final_sum}"

    def test_alpha_beta_format(self, factor, close):
        from backend.services.eval_service import _build_alphalens_extras
        result = _build_alphalens_extras(factor, close, fwd_periods=[1], n_groups=5)
        ab = result["alpha_beta"]
        assert "alpha" in ab and "beta" in ab and "annualized_alpha" in ab
        assert isinstance(ab["alpha"], float)
        assert isinstance(ab["beta"], float)

    def test_perfect_factor_has_positive_alpha(self, close):
        """完美因子（未来收益）的 alpha 应显著为正。"""
        from backend.services.eval_service import _build_alphalens_extras
        perfect = close.shift(-1) / close - 1
        result = _build_alphalens_extras(perfect, close, fwd_periods=[1], n_groups=5)
        assert result["alpha_beta"]["annualized_alpha"] > 0.5

    def test_graceful_on_empty_factor(self):
        """空因子表不崩，返回空 dict。"""
        from backend.services.eval_service import _build_alphalens_extras
        result = _build_alphalens_extras(
            pd.DataFrame(), pd.DataFrame(), fwd_periods=[1], n_groups=5,
        )
        assert result == {}
```

**Step 2: 运行测试确认失败**

Run: `uv run --project backend pytest backend/tests/test_metrics.py::TestAlphalensExtras -v`
Expected: FAIL（`_build_alphalens_extras` 不存在）

---

### Task 3: 实现 _build_alphalens_extras

**Files:**
- Modify: `backend/services/eval_service.py`

**Step 1: 在 `_build_health` 函数之前（约第 143 行后）添加新函数**

```python
def _build_alphalens_extras(
    F: pd.DataFrame,
    close: pd.DataFrame,
    *,
    fwd_periods: list[int],
    n_groups: int,
) -> dict:
    """调用 Alphalens 计算增量指标，返回 dict 供 payload["alphalens"] 使用。

    三项增量：rank_autocorrelation / group_cumulative_returns / alpha_beta。
    任何一项失败只跳过该项；整体失败返回空 dict。前端按 key 存在与否决定渲染。
    """
    try:
        import alphalens
    except ImportError:
        log.debug("alphalens 未安装，跳过增强指标")
        return {}

    if F.empty or close.empty:
        return {}

    import warnings

    try:
        factor_long = F.stack()
        factor_long.index.names = ["date", "asset"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            factor_data = alphalens.utils.get_clean_factor_and_forward_returns(
                factor_long, close,
                periods=tuple(fwd_periods),
                quantiles=n_groups,
                max_loss=1.0,
            )
    except Exception:
        log.warning("alphalens get_clean_factor 失败，跳过增强指标", exc_info=True)
        return {}

    base_period_col = factor_data.columns[0]  # e.g. "1D"
    extras: dict = {}

    # A. 因子排名自相关
    try:
        ranks_wide = (
            factor_data.groupby(level="date")["factor"]
            .rank()
            .reset_index()
            .pivot(index="date", columns="asset", values="factor")
        )
        autocorr = ranks_wide.corrwith(ranks_wide.shift(1), axis=1).dropna()
        extras["rank_autocorrelation"] = _series_to_obj(autocorr)
    except Exception:
        log.warning("alphalens rank_autocorrelation 失败", exc_info=True)

    # B. 分组累积净值（去均值口径）
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mr, _ = alphalens.performance.mean_return_by_quantile(
                factor_data, by_date=True, demeaned=True,
            )
        daily = mr[base_period_col].unstack(level="date").T.sort_index()
        daily.columns = range(len(daily.columns))
        cum = (1 + daily).cumprod()
        extras["group_cumulative_returns"] = _df_to_obj(cum)
    except Exception:
        log.warning("alphalens group_cumulative_returns 失败", exc_info=True)

    # C. Factor Alpha/Beta
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ab = alphalens.performance.factor_alpha_beta(factor_data)
        ann_alpha = float(ab.loc["Ann. alpha", base_period_col])
        beta = float(ab.loc["beta", base_period_col])
        daily_alpha = (1 + ann_alpha) ** (1 / 252) - 1
        extras["alpha_beta"] = {
            "alpha": _nan_to_none(daily_alpha),
            "beta": _nan_to_none(beta),
            "annualized_alpha": _nan_to_none(ann_alpha),
        }
    except Exception:
        log.warning("alphalens alpha_beta 失败", exc_info=True)

    return extras
```

**Step 2: 运行测试**

Run: `uv run --project backend pytest backend/tests/test_metrics.py::TestAlphalensExtras -v`
Expected: 8/8 PASS

---

### Task 4: 在 evaluate_factor_panel 中接入

**Files:**
- Modify: `backend/services/eval_service.py:367-401`

**Step 1: 在 payload 组装后、structured 组装前（约第 378 行后）插入调用**

```python
    # Alphalens 增强视角（可选，失败不影响主管线）
    al_extras = _build_alphalens_extras(
        F, close, fwd_periods=fwd_periods, n_groups=n_groups,
    )
    if al_extras:
        payload["alphalens"] = al_extras
```

**Step 2: 写集成测试验证 payload 中出现 alphalens key**

在 `TestEvaluateFactorPanelSanity` 中加一条：

```python
    def test_payload_contains_alphalens_extras(self, noise_factor, close):
        from backend.services.eval_service import evaluate_factor_panel

        payload, _ = evaluate_factor_panel(
            noise_factor, close, forward_periods=[1], n_groups=5,
        )
        assert "alphalens" in payload
        al = payload["alphalens"]
        assert "rank_autocorrelation" in al
        assert "group_cumulative_returns" in al
        assert "alpha_beta" in al
```

**Step 3: 运行全量 test_metrics.py**

Run: `uv run --project backend pytest backend/tests/test_metrics.py -v`
Expected: 全部 PASS

**Step 4: Commit**

```bash
git add backend/services/eval_service.py backend/tests/test_metrics.py
git commit -m "feat: 集成 Alphalens 增强视角（排名自相关 / 分组累积净值 / alpha-beta）"
```

---

### Task 5: 回归验证

**Step 1: 跑全量测试**

Run: `uv run --project backend pytest backend/tests/ -v --tb=short`
Expected: 全部 PASS，无回归

**Step 2: 验证现有评估管线测试不受影响**

特别关注：test_data_service_fundamentals, test_factors_pit, test_metrics 全绿。
