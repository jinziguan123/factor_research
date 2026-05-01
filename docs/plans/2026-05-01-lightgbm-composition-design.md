# LightGBM 多因子合成（method = `ml_lgb`）设计

> 关联调研：[docs/research/2026-04-30-qlib-rd-agent.md](../research/2026-04-30-qlib-rd-agent.md)
> 类型：扩展 `composition_service` 的合成方法
> 借鉴对象：qlib 用 LightGBM 做"多因子 → 单一 alpha 信号"合成

## Goal

给 `composition_service` 加第 4 个 method `ml_lgb`，用 LightGBM 学因子的**非线性合成**。突破现有 `equal` / `ic_weighted` / `orthogonal_equal` 三种线性 / 半线性方法的天花板：能学到"低换手 + 高动量"这种因子交叉效应。

## 非目标（YAGNI）

- 不做超参 UI 配置（先固定保守超参）
- 不做 GPU 加速（数据规模 < 100MB，PCIe 传输 amortize 不掉）
- 不做 hyperparameter tuning（用户拍板再做）
- 不做模型持久化（每次 run 重训）
- 不暴露 `forward_period` 给 UI 选（固定 5）

## Architecture

整体走现有 `composition_service.run_composition` 的同款流水线，仅在"合成阶段"分支加新 method：

```
load N factors (各 z-score 化)
   ↓
load future_5d_returns + cross-section rank → label
   ↓
method == "ml_lgb" → _combine_lightgbm(z_frames, label_panel) → pred 面板
                                                  ↓
                                          (副产物) mean feature_importance → payload
   ↓
evaluate_factor_panel(pred, close, ...)  # 同其它 method 共用 IC / 分组 / 多空指标
   ↓
写 fr_composition_runs / fr_composition_metrics / payload_json
```

## Data Flow & Algorithm

### Walk-forward expanding window 训练 / 推理

```python
def _combine_lightgbm(
    z_frames: list[pd.DataFrame],   # N 个因子，每个已 cross-section z-score
    label_panel: pd.DataFrame,      # 同形 (date × symbol)，每日 rank 化的未来 5d 收益
    factor_ids: list[str],
    *,
    forward_period: int = 5,        # label 是 future_5d_return → 训练 / 预测要隔 5 日防泄漏
    warmup_days: int = 60,          # 前 N 天没足够历史训模型
    lgb_params: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """
    返回：
      - pred: 合成预测面板（date × symbol），跟其它 method 同形态
      - feature_importance: {factor_id: mean_gain_across_walk_forward_models}
    """
    # 1. 把 N 个 z 面板拼成 (date, symbol) → N 维 X，对齐 NaN
    X_panel = pd.concat([z.stack().rename(fid) for fid, z in zip(factor_ids, z_frames)], axis=1)
    # X_panel 索引 = MultiIndex (date, symbol)，列 = factor_ids

    # 2. label：把 label_panel 对齐到同 (date, symbol) 索引
    y_series = label_panel.stack().reindex(X_panel.index)

    # 3. Walk-forward
    all_dates = sorted(X_panel.index.get_level_values("date").unique())
    pred = pd.DataFrame(index=label_panel.index, columns=label_panel.columns, dtype=float)
    importances = []  # 每天训完一个模型，收集一份 importance

    for i, date_t in enumerate(all_dates):
        if i < warmup_days:
            continue                       # 前 warmup 天 cold start，跳过
        train_end = i - forward_period     # 关键：减 forward_period 防 label 跨日泄漏
        if train_end < warmup_days:
            continue
        train_dates = all_dates[:train_end + 1]
        # 训练子集
        train_mask = X_panel.index.get_level_values("date").isin(train_dates)
        X_train = X_panel[train_mask].dropna()
        y_train = y_series[train_mask].reindex(X_train.index).dropna()
        common = X_train.index.intersection(y_train.index)
        if len(common) < 100:
            continue                       # 样本太少跳过
        X_train, y_train = X_train.loc[common], y_train.loc[common]

        model = LGBMRegressor(**(lgb_params or _DEFAULT_LGB_PARAMS))
        model.fit(X_train, y_train)
        importances.append(dict(zip(factor_ids, model.feature_importances_)))

        # 预测 date_t 当天截面
        X_today = X_panel.xs(date_t, level="date").reindex(columns=factor_ids)
        valid_mask = X_today.notna().all(axis=1)
        if not valid_mask.any():
            continue
        pred_today = pd.Series(
            model.predict(X_today.loc[valid_mask]),
            index=X_today.index[valid_mask],
        )
        pred.loc[date_t, pred_today.index] = pred_today.values

    # 聚合 importance 取 mean
    fi_mean = {fid: 0.0 for fid in factor_ids}
    if importances:
        for fid in factor_ids:
            fi_mean[fid] = float(np.mean([imp.get(fid, 0.0) for imp in importances]))

    return pred, fi_mean
```

### 防 lookahead 关键

- 训练用 `[start, t - forward_period]`，**减 forward_period 防 label 跨日泄漏**——若 t=D，y_D 是 D~D+5 收益，则训练数据日期不能晚于 D-5（否则训练集见过含未来收益信息的样本）
- 第一个有效预测日 = `warmup_days + forward_period`（默认 60 + 5 = 65）
- 早于此的日期 pred = NaN（与 ic_weighted 的 cold start 一致）

### Label 构造

```python
def _build_future_return_label(close: pd.DataFrame, forward_period: int = 5) -> pd.DataFrame:
    """每日 cross-section rank 化的未来 N 日收益。

    1. 计算 future_return = close.shift(-N) / close - 1
    2. 每日 cross-section rank → 等比缩放到 [-1, 1]
    3. 缺值（最末 N 天没未来）保持 NaN
    """
    fwd = close.shift(-forward_period) / close - 1
    ranked = fwd.rank(axis=1, pct=True)        # [0, 1] pct rank
    return ranked * 2 - 1                       # [-1, 1]
```

## Hyperparameters

固定保守起点，写在 `_DEFAULT_LGB_PARAMS` 模块常量里：

```python
_DEFAULT_LGB_PARAMS = {
    "n_estimators": 100,
    "max_depth": 4,           # 浅树防过拟合
    "num_leaves": 15,         # 2^4 - 1，与 max_depth 匹配
    "learning_rate": 0.05,
    "reg_alpha": 0.1,         # L1 正则
    "reg_lambda": 0.1,        # L2 正则
    "min_child_samples": 20,  # 叶节点至少 20 样本
    "verbose": -1,            # 静默
    "random_state": 42,       # 可重现
    "n_jobs": -1,             # 多核
}
```

调参留给将来——先看默认效果。

## Persistence

- `fr_composition_runs.method` 增加 `ml_lgb` 枚举值
- `fr_composition_metrics.payload_json` 顶层加 `feature_importance: {factor_id: float}`（仅 ml_lgb 写）
- `weights` / `per_factor_ic` 字段保持空（ml_lgb 没有线性权重，但下游"贡献度"可以用 importance 占比近似）

## API 改动

`backend/api/schemas.py` 的 `CreateCompositionIn.method` 字段允许 `"ml_lgb"`。后端 `run_composition` 调用 `_combine_lightgbm`。

前端 `CompositionCreate.vue` 加下拉选项 "LightGBM 合成（ML 学权重）"。

## 前端展示

`CompositionDetail.vue` 现有"子因子 IC + 权重 + 贡献度"表加一列 **LGB Importance**：
- v-if `method == 'ml_lgb'` 才显示这列
- 数据源：`payload.feature_importance[factor_id]`
- 渲染：相对值条形图（max 100% 归一），方便对比

## Error Handling

| 失败情形 | 处理 |
|---|---|
| `lightgbm` 包未安装 | service 层 import 异常 → 友好报错 "未安装 lightgbm，请 pip install" |
| 因子样本太少（< 100 行） | 当日跳过预测，pred 留 NaN |
| 所有 walk-forward 都跳过（warmup > 区间） | pred 全 NaN，feature_importance 空 dict，不抛错（让用户从空指标自己看出问题） |
| 训练失败（数据 NaN / inf） | log warn + 跳过当日，不阻断后续 |

## Testing

新建 `backend/tests/test_lightgbm_composition.py`，~7 case：

1. **happy path**：合成 N 因子 → pred shape 匹配 + 非空 + feature_importance 非空
2. **walk-forward 防泄漏**：mock 因子 = 未来收益 + 噪声 → IC 应 ≈ 噪声水平（如果训练混进未来则 IC 假高，会被这条断言拦下）
3. **cold start**：前 warmup_days 全 NaN
4. **空因子集**：抛 ValueError
5. **样本不足**：少于 100 行训练数据 → pred 全 NaN，不崩
6. **forward_period 边界**：t-forward_period < warmup → 跳过
7. **feature_importance 聚合**：多个 walk-forward 模型的 importance 取 mean

## Dependencies

新增：`lightgbm>=4.0`（PyPI 主流版本，wheel 覆盖 Windows / Linux / macOS）

`backend/pyproject.toml`：
```toml
dependencies = [
    ...,
    "lightgbm>=4.0",
]
```

## DoD

- 后端 `_combine_lightgbm` 7 case 全绿
- composition_service 整套测试 + factor_assistant 测试无回归
- 前端 vue-tsc 通过
- 实际跑一次 5 因子 × 3 月窗口的 ml_lgb 合成，CompositionDetail 能看到 importance 列

## 风险与已知 trade-offs

| 风险 | 缓解 |
|---|---|
| Walk-forward 训练 252 天 ≈ 几分钟（vs 现有几秒） | 用户已接受；仅"完整年度评估"才慢，60 天小窗口快 |
| 默认超参可能不是最优 | 后续可补 UI 配置（YAGNI 先固定） |
| LGB 训练 log 噪声 | 配 `verbose=-1` 静默 |
| 小样本 cold start | 与 IC 加权一致，前 65 天 NaN 直观 |

## 与现有方法对比

| Method | 学的是什么 | 复杂度 | 适用场景 |
|---|---|---|---|
| `equal` | 1/N 等权 | 最低 | 因子彼此独立 / 全部正向 |
| `ic_weighted` | 历史 IC 加权 | 低 | 因子线性贡献，方向稳定 |
| `orthogonal_equal` | 正交后等权 | 中 | 因子相关性高 |
| **`ml_lgb`** | **非线性 + 因子交互** | 高 | **多因子且怀疑有交叉效应** |

`ml_lgb` 不取代前三者——简单场景仍用 `equal` / `ic_weighted` 更可解释。
