# 行业/市值中性化 + 轻量风格归因

**日期**: 2026-05-04
**状态**: 已设计，待实现
**范围**: 阶段 1（中性化） + 阶段 2（风格因子归因）

## 概述

在因子评估 pipeline 中增加行业/市值中性化步骤，以及 5 个轻量 Barra 风格因子用于暴露度归因。中性化为可选步骤（默认开启），结果与原始指标对比展示在评估详情页。

## 子项目划分

| 子项目 | 内容 | 依赖 |
|--------|------|------|
| A. 数据导入 | 行业历史、市值、PB 从 akshare → MySQL | 无 |
| B. 中性化 | NeutralizationService + eval pipeline 集成 | A |
| C. 风格归因 | 5 个风格因子 + AttributionService + 前端展示 | A, B |

## 子项目 A：数据导入

### 新增 MySQL 表

**`fr_industry_history`**（行业分类历史快照）

| 列 | 类型 | 说明 |
|-----|------|------|
| symbol | VARCHAR(16) PK | 股票代码 |
| snapshot_date | DATE PK | 快照日期 |
| industry_l1 | VARCHAR(64) | 一级行业（申万） |
| industry_l2 | VARCHAR(64) | 二级行业 |
| classification | VARCHAR(32) | 分类标准（sw/csrc） |

**`fr_daily_market_cap`**（日频市值）

| 列 | 类型 | 说明 |
|-----|------|------|
| symbol_id | UInt32 PK | 股票 ID |
| trade_date | DATE PK | 交易日 |
| total_mv | DECIMAL(18,2) | 总市值 |
| float_mv | DECIMAL(18,2) | 流通市值 |

**`fr_daily_pb`**（日频市净率）

| 列 | 类型 | 说明 |
|-----|------|------|
| symbol_id | UInt32 PK | 股票 ID |
| trade_date | DATE PK | 交易日 |
| pb | DECIMAL(10,4) | 市净率 |

### 数据来源（akshare，均免费）

| 数据 | akshare 接口 | 拉取方式 |
|------|-------------|---------|
| 行业分类 | `stock_board_industry_name_em()` | 日频全量拉取，只写变化行 |
| 市值 + PB | `stock_zh_a_spot_em()` | 日频全量拉取，每次约 5000 条 |

### 新增文件

| 文件 | 职责 |
|------|------|
| `backend/scripts/migrations/009_add_market_cap_pb.sql` | fr_daily_market_cap + fr_daily_pb DDL |
| `backend/scripts/migrations/010_add_industry_history.sql` | fr_industry_history DDL |
| `backend/adapters/akshare/industry.py` | 行业分类拉取 → 写入 |
| `backend/adapters/akshare/market_data.py` | 市值 + PB 拉取 → 写入 |
| `backend/scripts/backfill_market_data.py` | 历史回填脚本（一次性） |

### DataService 扩展

新增 3 个方法：

```python
def load_market_cap(self, symbols, start, end) -> pd.DataFrame
    # index=trade_date, columns=symbol, values=total_mv

def load_pb(self, symbols, start, end) -> pd.DataFrame
    # index=trade_date, columns=symbol, values=pb

def load_industry(self, symbols, as_of_date) -> pd.Series
    # index=symbol, values=industry_l1
    # 取 as_of_date 之前最近的行业快照
```

## 子项目 B：中性化服务

### NeutralizationService

新增 `backend/services/neutralization.py`。

**核心算法**：逐日截面 OLS 回归取残差

```
for each trade_date:
    y = factor_values[cross-section]           # N×1
    X = [log(mktcap), industry_dummies]        # N×(k+1)
    β = lstsq(X, y)
    residual = y - X @ β
```

**边界处理**：
- 单行业 < 3 只股票 → 合并为 "其他"
- 缺失行业 → 排除该样本
- 缺失市值 → 排除该样本
- NaN 因子值 → 排除该样本，输出保留 NaN

**接口**：

```python
class NeutralizationService:
    def neutralize(factor_panel, market_cap, industry,
                   min_industry_size=3) -> pd.DataFrame
    def neutralize_with_industry_only(...) -> pd.DataFrame
    def neutralize_with_market_cap_only(...) -> pd.DataFrame
```

### Eval Pipeline 集成

`run_eval()` 新增参数 `neutralize: bool = True`（默认开启）。

流程变更：

1. `factor.compute(ctx, params)` → F_raw
2. 加载 close / mktcap / industry
3. **if neutralize**: F_neut = NeutralizationService().neutralize(...)
4. evaluate_factor_panel(F_raw) → raw_metrics
5. **if neutralize**: evaluate_factor_panel(F_neut) → neut_metrics
6. 合并存储到 `fr_factor_eval_metrics`

### 存储扩展

`fr_factor_eval_metrics` 新增字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| neut_ic_mean | DOUBLE | 中性化后 IC 均值 |
| neut_ic_ir | DOUBLE | 中性化后 IC IR |
| neut_rank_ic_mean | DOUBLE | 中性化后 Rank IC |
| neut_rank_ic_ir | DOUBLE | 中性化后 Rank IC IR |
| neut_long_short_annret | DOUBLE | 中性化后多空年化收益 |
| neut_payload_json | MEDIUMTEXT | 中性化后完整评估 JSON |

### API 变更

`POST /api/evals` 请求体新增字段：

```python
neutralize: bool = Field(default=True, description="是否做行业+市值中性化")
```

## 子项目 C：风格因子 + 归因

### 5 个轻量 Barra 风格因子

均实现为 `BaseFactor` 子类，放在 `backend/factors/riskmodel/`，`category="riskmodel"`。

| factor_id | display_name | 公式 | 数据源 | warmup |
|-----------|-------------|------|--------|--------|
| size_mv | 规模因子 | `log(total_mv)` | fr_daily_market_cap | 1d |
| value_ep | 价值因子 | `1 / PB` | fr_daily_pb | 1d |
| momentum_12m1m | 动量因子 | `cumret(t-250, t-21)` | stock_bar_1d close | ~375d |
| volatility_60d | 波动因子 | `std(daily_ret, 60d)` | stock_bar_1d close | ~90d |
| liquidity_20d | 流动性因子 | `mean(turnover, 20d)` | stock_bar_1d + mktcap | ~30d |

所有因子 `supported_freqs = ("1d",)`，`adjust="qfq"`。

### AttributionService

新增 `backend/services/attribution.py`。

**算法**：每日截面回归 `alpha_factor ~ Σ β_i × style_i + ε`

```python
@dataclass
class AttributionResult:
    exposures: dict[str, pd.Series]   # {"Size": β_series, ...}
    r_squared: pd.Series              # 每日 R²
    residual: pd.DataFrame            # 风格中性化残差

class AttributionService:
    def decompose(factor_panel, style_panels) -> AttributionResult
```

在 eval pipeline 中，中性化后也计算归因（当 neutralize=True 且风格因子数据可用时）。

### 文件结构

```
backend/factors/riskmodel/
  __init__.py
  size.py
  value.py
  momentum_12m1m.py
  volatility.py
  liquidity.py

backend/services/
  neutralization.py    # 中性化服务
  attribution.py        # 风格归因服务
```

## 前端改动

### EvalCreate.vue — 中性化开关

新增 checkbox：`☑ 行业+市值中性化`，默认选中。创建评估时传递 `neutralize: true/false`。

### EvalDetail.vue — 中性化对比 + 风格暴露

新增两个区块（当 neutralize=true 且 neut_metrics 存在时显示）：

**区块 1：中性化效果对比**
- 表格列：指标、原始值、中性化后值、变化
- 展示 IC Mean / IC IR / Rank IC Mean / Rank IC IR / Long-Short 年化收益

**区块 2：风格暴露度**
- 柱状图：5 个风格因子的平均暴露度（时序均值）
- 折线图：各风格暴露度时序（过去评估窗口）

### 前端文件

| 文件 | 变更 |
|------|------|
| `frontend/src/pages/evals/EvalCreate.vue` | 新增 neutralize checkbox |
| `frontend/src/pages/evals/EvalDetail.vue` | 新增中性化对比 + 风格暴露区块 |
| `frontend/src/api/evals.ts` | EvalRun 新增 neut_* 字段，创建请求新增 neutralize 参数 |
| `backend/api/schemas.py` | EvalCreate 请求体新增 neutralize 字段 |
| `backend/api/routers/evals.py` | 传递 neutralize 到 eval_service.run_eval() |

## 实现顺序

1. 数据导入（子项目 A）—— DDL + 适配器 + 回填
2. 中性化服务 + pipeline 集成（子项目 B）—— 后端核心
3. 风格因子 + 归因（子项目 C）—— 因子实现 + 服务
4. 前端展示 —— 创建页 + 详情页
5. 集成验证

## 错误处理

| 场景 | 处理 |
|------|------|
| 行业数据缺失（某天某股无行业） | 排除该样本，不参与回归 |
| 市值数据缺失 | 排除该样本 |
| 某日有效样本不足（< 10 只） | 该日中性化结果全为 NaN |
| PB=0 或缺失 | Value 因子该点返回 NaN |
| akshare 拉取失败 | 记录日志，跳过当日更新 |
| 风格因子 compute 失败 | 归因不可用，前端不显示暴露区块 |
