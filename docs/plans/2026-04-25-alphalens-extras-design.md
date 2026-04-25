# Alphalens 可选增强视角

日期：2026-04-25

## 动机

现有 `evaluate_factor_panel()` 已覆盖 IC / Rank IC / 分组收益 / 多空净值 / 换手率 /
体检等核心评估指标。Alphalens 作为行业标准因子分析库，提供几项我们未实现的补充视角。
将其作为**可选增强**（而非替换）集成，在评估 payload 中追加一个 `alphalens` 命名空间，
前端按其存在与否决定是否渲染增强区块。

## 三项增量指标

### A. 因子排名自相关 `rank_autocorrelation`

- 衡量因子 rank 的日间连贯性——自相关高 = 持仓稳定、换手低；低 = 信号噪声大
- 计算：`alphalens.performance.factor_rank_autocorrelation(factor_data, period=1)`
- 输出：`{"dates": [...], "values": [...]}` 同 `_series_to_obj` 格式

### B. 分组累积净值 `group_cumulative_returns`

- 各分位数的累积净值曲线，**去均值口径**（每组收益减去截面均值，消除牛/熊市影响）
- 和现有 `group_returns`（原始收益日均柱状图）互补
- 计算：`mean_return_by_quantile(by_date=True, demeaned=True)` → 逐组 `(1+daily).cumprod()`
- 输出：`{"dates": [...], "g1": [...], ..., "g5": [...]}` 同 `_df_to_obj` 格式

### C. Factor Alpha/Beta `alpha_beta`

- 因子日收益对基准收益做 OLS，拆解超额收益（alpha）和市场暴露（beta）
- 基准 = 截面等权均值收益（`close.mean(axis=1)` 的日收益率），不引入额外数据依赖
- 输出：`{"alpha": float, "beta": float, "annualized_alpha": float}`

## Payload 结构

```python
payload["alphalens"] = {
    "rank_autocorrelation": {"dates": [...], "values": [...]},
    "group_cumulative_returns": {"dates": [...], "g1": [...], ..., "g5": [...]},
    "alpha_beta": {"alpha": 0.0002, "beta": 0.15, "annualized_alpha": 0.05},
}
```

前端判断 `payload.alphalens?.rank_autocorrelation` 存在再渲染，老数据自动兼容。

## 容错策略

- `import alphalens` 失败 → 跳过整个 extras，`payload["alphalens"]` 不存在
- 单项计算异常 → 该项不写入，其他项继续
- 主管线指标不受影响

## 文件变更

| 文件 | 变更 |
|------|------|
| `backend/services/eval_service.py` | 新增 `_build_alphalens_extras()`，`evaluate_factor_panel` 末尾调用 |
| `backend/tests/test_metrics.py` | 新增 `TestAlphalensExtras` 测试类 |
| `backend/pyproject.toml` | `alphalens-reloaded` 从 dev 移入主依赖 |
| 前端 | 后续单独做，本次只做后端 |

## 设计决策记录

1. **命名空间嵌套**（而非平铺）：前端可整块判断渲染，老数据兼容
2. **分组净值去均值口径**：和现有原始口径互补，不重复
3. **基准用截面等权均值**：不引入指数数据依赖，语义匹配股票池
