# 因子研究平台（factor_research）设计文档

> 日期：2026-04-16
> 状态：已与用户确认，等待进入实施
> 作者：Claude（与用户 jinziguan 共同设计）

## 0. 背景与定位

本项目旨在提供量化交易中**因子快速回测**的平台，能够展示因子对于股票交易的重要指标（IC、Rank IC、分组收益、换手率等），并支持数据可视化和导出。

- 短期目标：架构清晰、使用便利、高性能；放弃 T+1、仅做多的约束以聚焦因子研究本身
- 长期目标：对接 AI 进行因子分析和优化

与同级目录现有项目的关系：
- `timing_driven_backtest/`：择时驱动回测平台（已成熟）
- `my_quant_utils/`：共享量化工具库
- 本项目 `factor_research/` 作为独立项目，**拷贝复用**这两者的数据读取/元数据管理代码，但所有服务、API、前端独立实现

## 1. 关键设计决策

| # | 决策点 | 选择 |
|---|-------|-----|
| Q1 | 与 timing_driven 的关系 | 复用底层代码但拷贝进本项目，彻底独立 |
| Q2 | 数据频率 | MVP 仅日频；架构保留多频率扩展位（1d/1m/5m/...） |
| Q3 | 日频数据落地 | 新建 ClickHouse 物化表 `stock_bar_1d`，每日增量聚合 |
| Q3a | 前复权 parquet 获取方式 | 手工从 Windows 主机拷贝到开发机 |
| Q3b | 前复权因子存储 | 落 MySQL 新表 `fr_qfq_factor`（生产库原无此表），读取时内存相乘 |
| Q4 | 因子定义方式 | Python 函数/类注册，支持热加载 |
| Q5 | 因子值持久化 | 永久入库 ClickHouse `factor_value_1d`，按 `(factor_id, version, params_hash)` 复用 |
| Q6 | 后端框架与任务模型 | FastAPI + ProcessPoolExecutor（经由 BackgroundTasks 提交） |
| Q7 | 前端技术栈 | Vue3 + TypeScript + Vite + Naive UI + ECharts + Vue Query + Pinia |
| Q8.1 | 用户体系 | 单用户（`owner_key=factor_research`，与 timing_driven 的默认池隔离），不做登录 |
| Q8.2 | 股票池管理 UI | 可增删改 + 批量粘贴导入 |
| Q8.3 | MVP 评估指标 | IC / Rank IC / 分组收益 / 多空组合 / 换手率 / 因子值分布 / 累计曲线 |
| Q8.3 | 默认参数 | 5 分组，forward_periods = 1/5/10 日 |
| Q8.4 | 评估 vs 回测 | 两者都做（评估算指标；回测基于因子构造组合走 VectorBT） |
| Q8.5 | 部署形态 | 本地开发 + docker-compose 内网部署 |
| UI | 视觉风格 | Binance.US 风格（Binance Yellow `#F0B90B` 为主色） |
| DB | 表命名规则 | 本项目新建表一律加 `fr_` 前缀与主业务表分离；复用 `stock_symbol` / `stock_pool` / `stock_pool_symbol` |

## 2. 整体架构

```
┌────────────────────── 浏览器 ──────────────────────┐
│ Vue3 + Naive UI + ECharts + Pinia + Vue Query     │
│  - 因子列表页 / 因子详情页                          │
│  - 评估任务页（IC/分组/换手）                       │
│  - 回测任务页（VectorBT 净值曲线）                  │
│  - 股票池管理页 / 数据维护页                         │
└────────────┬───────────────────────────────────────┘
             │ REST (axios)
             ▼
┌────────────────────── FastAPI ─────────────────────┐
│  api/         routers: factors/pools/evals/        │
│               backtests/runs/data/admin            │
│  runtime/     BackgroundTasks + ProcessPool +      │
│               watchdog 热加载                       │
│  services/    FactorRegistry / EvalService /       │
│               BacktestService / DataService        │
│  factors/     用户定义因子（Python，热加载）        │
│  engine/      核心计算（pandas/numpy/numba/vbt）   │
│  storage/     clickhouse_*.py / mysql_*.py         │
└──────┬──────────────────────────┬──────────────────┘
       │                          │
       ▼                          ▼
┌────────────────────┐      ┌────────────────────┐
│  MySQL             │      │  ClickHouse        │
│ 共享（只读/读写）    │      │  (K 线 + 因子值)    │
│ ├─ stock_symbol    │      │ stock_bar_1m       │
│ ├─ stock_pool      │      │ stock_bar_1d  ← 新 │
│ └─ stock_pool_symbol│     │ factor_value_1d ←新│
│                    │      │                    │
│ factor_research 新建│      │                    │
│ ├─ fr_qfq_factor   │      │                    │
│ ├─ fr_factor_meta  │      │                    │
│ ├─ fr_factor_eval_*│      │                    │
│ └─ fr_backtest_*   │      │                    │
└────────────────────┘      └────────────────────┘
                                     ▲
                                     │ 手工拷贝 parquet
                     ┌──────────────────────────────┐
                     │ merged_adjust_factors.parquet │
                     │ （Windows 主机）               │
                     └──────────────────────────────┘
```

**关键数据流：**

1. **冷启动一次性**：用 `import_qfq.py` 把前复权 parquet 导入 MySQL 新表 `fr_qfq_factor`；用 `aggregate_bar_1d.py` 从 `stock_bar_1m` 物化出 `stock_bar_1d`
2. **每日增量**：新交易日的分钟 K 线到位后，触发 `stock_bar_1d` 增量任务；新版 parquet 到来时重导复权因子
3. **因子评估**：前端提交 → 后台 ProcessPool → 读日线（前复权）→ `factor.compute()` → 落 `factor_value_1d` → 算 IC / 分组 / 换手 → 落 `fr_factor_eval_runs` / `fr_factor_eval_metrics` → 前端轮询拉结果
4. **因子回测**：从 `factor_value_1d` 查因子值（命中缓存就复用）→ 构造持仓矩阵 → `vbt.Portfolio.from_orders` → 落 `fr_backtest_runs` / `fr_backtest_metrics` / `fr_backtest_artifacts`
5. **热加载**：watchdog 监听 `backend/factors/`；文件变动时重新 import 模块、扫描 `BaseFactor` 子类、更新 `fr_factor_meta`；worker 进程通过 `max_tasks_per_child=5` 自然 recycle 获取新版本

## 3. 数据层

### 3.1 MySQL 表

**复用（生产库已有，本项目只读；不做 DDL）**

- `stock_symbol`（symbol_id ↔ 'SSE/SZSE 代码' ↔ name 的映射，生产库已存在 16790+ 条）
- `stock_pool` / `stock_pool_symbol`（股票池；factor_research 用 `owner_key='factor_research'` 与 timing_driven 的数据隔离）

**新建（factor_research 专属，统一加 `fr_` 前缀）**

```sql
-- 【新增】前复权因子（生产库没有此表，本项目需建）
CREATE TABLE `fr_qfq_factor` (
  `symbol_id`         int unsigned NOT NULL,
  `trade_date`        date NOT NULL,
  `factor`            double NOT NULL,
  `source_file_mtime` bigint unsigned NOT NULL DEFAULT 0,
  `created_at`        datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`symbol_id`, `trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】因子元数据（由热加载机制维护）
CREATE TABLE `fr_factor_meta` (
  `factor_id`       varchar(64)  NOT NULL,
  `display_name`    varchar(128) NOT NULL,
  `category`        varchar(64)  NOT NULL,
  `description`     varchar(1000) DEFAULT NULL,
  `params_schema`   longtext,
  `default_params`  longtext,
  `supported_freqs` varchar(64) NOT NULL DEFAULT '1d',
  `code_hash`       char(40) NOT NULL,
  `version`         int unsigned NOT NULL DEFAULT 1,
  `is_active`       tinyint(1) NOT NULL DEFAULT 1,
  `updated_at`      datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`factor_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】因子评估任务
CREATE TABLE `fr_factor_eval_runs` (
  `run_id`          varchar(64) NOT NULL,
  `factor_id`       varchar(64) NOT NULL,
  `factor_version`  int unsigned NOT NULL,
  `params_hash`     char(40) NOT NULL,
  `params_json`     longtext,
  `pool_id`         bigint unsigned NOT NULL,
  `freq`            varchar(8) NOT NULL DEFAULT '1d',
  `start_date`      date NOT NULL,
  `end_date`        date NOT NULL,
  `forward_periods` varchar(64) NOT NULL,
  `n_groups`        tinyint unsigned NOT NULL DEFAULT 5,
  `status`          varchar(16) NOT NULL,
  `progress`        tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`   text,
  `created_at`      datetime NOT NULL,
  `started_at`      datetime DEFAULT NULL,
  `finished_at`     datetime DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`, `status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】因子评估结果
CREATE TABLE `fr_factor_eval_metrics` (
  `run_id`             varchar(64) NOT NULL,
  `ic_mean`            double, `ic_std` double, `ic_ir` double,
  `ic_win_rate`        double, `ic_t_stat` double,
  `rank_ic_mean`       double, `rank_ic_std` double, `rank_ic_ir` double,
  `turnover_mean`      double,
  `long_short_sharpe`  double,
  `long_short_annret`  double,
  `payload_json`       longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】回测任务（独立一份，不改动生产 backtest_runs）
CREATE TABLE `fr_backtest_runs` (
  `run_id`          varchar(64) NOT NULL,
  `name`            varchar(255) DEFAULT NULL,
  `factor_id`       varchar(64) NOT NULL,
  `factor_version`  int unsigned NOT NULL,
  `params_hash`     char(40) NOT NULL,
  `params_json`     longtext,
  `pool_id`         bigint unsigned NOT NULL,
  `freq`            varchar(8) NOT NULL DEFAULT '1d',
  `start_date`      date NOT NULL,
  `end_date`        date NOT NULL,
  `status`          varchar(16) NOT NULL,
  `progress`        tinyint unsigned NOT NULL DEFAULT 0,
  `error_message`   text,
  `created_at`      datetime(6) NOT NULL,
  `started_at`      datetime(6) DEFAULT NULL,
  `finished_at`     datetime(6) DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_factor_status` (`factor_id`, `status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】回测指标
CREATE TABLE `fr_backtest_metrics` (
  `run_id`        varchar(64) NOT NULL,
  `total_return`  double NOT NULL DEFAULT 0,
  `annual_return` double NOT NULL DEFAULT 0,
  `sharpe_ratio`  double NOT NULL DEFAULT 0,
  `max_drawdown`  double NOT NULL DEFAULT 0,
  `win_rate`      double NOT NULL DEFAULT 0,
  `trade_count`   int NOT NULL DEFAULT 0,
  `payload_json`  longtext,
  PRIMARY KEY (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 【新增】回测产物路径
CREATE TABLE `fr_backtest_artifacts` (
  `run_id`         varchar(64) NOT NULL,
  `artifact_type`  varchar(64) NOT NULL,
  `artifact_path`  varchar(500) NOT NULL,
  PRIMARY KEY (`run_id`, `artifact_type`),
  KEY `idx_run_id` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.2 ClickHouse 新增表

```sql
-- 【新增】日频 K 线物化表（存未复权；读取时乘因子得到前复权）
CREATE TABLE quant_data.stock_bar_1d
(
    `symbol_id`  UInt32,
    `trade_date` Date,
    `open`       Float32, `high` Float32, `low` Float32, `close` Float32,
    `volume`     UInt64,  `amount_k` UInt32,
    `version`    UInt64,
    `updated_at` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYear(trade_date)
ORDER BY (symbol_id, trade_date)
SETTINGS index_granularity = 8192;

-- 【新增】因子值表（窄表/长格式）
CREATE TABLE quant_data.factor_value_1d
(
    `factor_id`      LowCardinality(String),
    `factor_version` UInt32,
    `params_hash`    FixedString(40),
    `symbol_id`      UInt32,
    `trade_date`     Date,
    `value`          Float64,
    `version`        UInt64,
    `updated_at`     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (factor_id, toYear(trade_date))
ORDER BY (factor_id, factor_version, params_hash, symbol_id, trade_date);
```

**字段单位说明**：`volume` 升级为 UInt64（日级累加后接近 UInt32 上限）；`amount_k`（千元单位）保持 UInt32（上限 4.29 万亿元，远高于单只股票单日最大成交额）。

### 3.3 读取层 API（`backend/storage/data_service.py`）

```python
class DataService:
    def load_bars(
        self, symbols: list[str], start: date, end: date,
        freq: Literal["1d","1m","5m","15m","30m","60m"] = "1d",
        adjust: Literal["none","qfq"] = "qfq",
        fields: tuple = ("open","high","low","close","volume","amount_k"),
    ) -> dict[str, pd.DataFrame]: ...

    def load_panel(
        self, symbols: list[str], start: date, end: date,
        freq="1d", field="close", adjust="qfq",
    ) -> pd.DataFrame:  # index=trade_date, columns=symbol
        ...

    def resolve_pool(self, pool_id: int, as_of: date | None = None) -> list[str]: ...

    def load_factor_values(
        self, factor_id: str, factor_version: int, params_hash: str,
        symbols: list[str], start: date, end: date,
    ) -> pd.DataFrame: ...

    def save_factor_values(
        self, factor_id: str, factor_version: int, params_hash: str,
        frame: pd.DataFrame,
    ) -> None: ...
```

MVP 只实现 `freq="1d"`。扩展新频率时：
- 新建 `stock_bar_<freq>` / `factor_value_<freq>` 表
- `DataService` 加对应读取分支
- 因子代码与上层逻辑不变

## 4. 因子定义 + 热加载

### 4.1 基类

```python
# backend/engine/base_factor.py
@dataclass
class FactorContext:
    data: "DataService"
    symbols: list[str]
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    warmup_days: int

class BaseFactor:
    factor_id:       ClassVar[str]
    display_name:    ClassVar[str]
    category:        ClassVar[str]
    description:     ClassVar[str] = ""
    params_schema:   ClassVar[dict] = {}
    default_params:  ClassVar[dict] = {}
    supported_freqs: ClassVar[tuple[str, ...]] = ("1d",)

    def required_warmup(self, params: dict) -> int: ...
    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame: ...
        # 返回宽表：index=trade_date，columns=symbol
```

### 4.2 示例因子

```python
# backend/factors/reversal/reversal_n.py
class ReversalN(BaseFactor):
    factor_id     = "reversal_n"
    display_name  = "N 日反转"
    category      = "reversal"
    description   = "-1 × 过去 N 日收益率，值越高说明过去跌得越多、反转预期越强"
    params_schema = {"window": {"type":"int","default":20,"min":2,"max":252}}
    default_params = {"window": 20}

    def required_warmup(self, params):
        return int(params["window"]) + 5

    def compute(self, ctx, params):
        window = int(params["window"])
        close = ctx.data.load_panel(
            ctx.symbols, ctx.start_date.date() - pd.Timedelta(days=ctx.warmup_days).days,
            ctx.end_date.date(), field="close", adjust="qfq",
        )
        return -close.pct_change(window).loc[ctx.start_date:]
```

### 4.3 热加载

- `backend/runtime/factor_registry.py`：单例 `FactorRegistry`，扫描 `backend/factors/`、注册子类、维护 `fr_factor_meta`
- `backend/runtime/hot_reload.py`：`watchdog` 监听 `.py` 文件；变动时 `importlib.reload` → 重新扫描 → 对比 `code_hash`，变化则 `version += 1` 并 UPDATE `fr_factor_meta`
- **热加载只作用于 API 进程**。Worker 进程启动时 `scan_and_register()` 一次；设置 `max_tasks_per_child=5`，每跑 5 个任务自然 recycle → 获取新版本
- 紧急情况：`POST /api/factors/reload` 强制重建 ProcessPool

### 4.4 目录结构

```
backend/factors/
  base.py
  reversal/
    reversal_n.py
    reversal_12_1.py
  momentum/
    momentum_n.py
  volatility/
    realized_vol.py
  volume/
    turnover_ratio.py
  custom/
```

### 4.5 参数哈希

```python
def params_hash(params: dict) -> str:
    return hashlib.sha1(
        json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
```

## 5. 评估引擎（EvalService）

### 5.1 计算口径

- **IC**：每日横截面 `corr(F_t, R_t^k)`；不是时序 corr
- **Rank IC**：每日横截面 `spearman(F_t, R_t^k)`
- **分组**：`pd.qcut` 每日独立 rank；NaN 因子值当日剔除
- **分组收益**：每组下一期等权平均前瞻收益
- **多空组合**：`top_group_return - bottom_group_return`
- **换手率**：每日 top 组与前一日 top 组的 symmetric diff / 组大小的均值
- **因子值分布**：按月取样一次，合并成直方图

### 5.2 流程

```
1. 解析股票池 → symbols
2. factor = registry.get(factor_id)
3. ctx = FactorContext(..., warmup=factor.required_warmup(params))
4. F = factor.compute(ctx, params)  # 或命中 factor_value_1d 缓存
5. save_factor_values(factor_id, version, params_hash, F)
6. close = data.load_panel(..., field="close", adjust="qfq")
7. R[k] = close.shift(-k)/close - 1, k in forward_periods
8. 计算 ic_series / rank_ic_series / group_returns / long_short / turnover / value_hist
9. 聚合结构化 metrics + payload_json → INSERT fr_factor_eval_metrics
10. UPDATE fr_factor_eval_runs.status='success'
```

### 5.3 进度阶段

| 阶段 | 进度 |
|-----|-----|
| 解析 & 读数据 | 10% |
| compute 因子值 | 40% |
| IC / RankIC | 55% |
| 分组收益 | 75% |
| 换手率 + 分布 | 85% |
| 落库 & 收尾 | 100% |

## 6. 回测引擎（BacktestService）

### 6.1 参数

```
factor_id, params, pool_id, start_date, end_date,
freq="1d",
n_groups=5,
rebalance_period=1,            # 日
position="top"|"long_short",
cost_bps=3,
init_cash=1e7
```

### 6.2 流程

```
1. 拿 factor 值（优先命中 factor_value_1d 缓存；不够再 compute 补齐入库）
2. 生成权重矩阵 W：每 rebalance_period 日取一次因子横截面排名
   - top=top Q 分位等权
   - long_short=top Q 等正权 + bottom Q 等负权
   - 非调仓日沿用上一期权重
3. size = W * init_cash / close
4. vbt.Portfolio.from_orders(close, size, fees=cost_bps/1e4, freq=freq)
5. 导出 equity / orders / trades / stats 到 parquet
6. INSERT fr_backtest_runs / fr_backtest_metrics / fr_backtest_artifacts
```

### 6.3 关键约束（本项目显式放弃以聚焦因子研究）

- **不做 T+1 约束**：买入当日可卖出
- **允许做空**：`long_short` 模式下 bottom 组权重为负
- **成交价**：当日 close（MVP 用 close，未来需要 next-open / vwap 再扩展）

## 7. REST API

### 7.1 路由总览

```
GET  /api/health

# 因子
GET  /api/factors
GET  /api/factors/{factor_id}
POST /api/factors/reload

# 股票池
GET    /api/pools
POST   /api/pools
GET    /api/pools/{pool_id}
PUT    /api/pools/{pool_id}
DELETE /api/pools/{pool_id}
POST   /api/pools/{pool_id}:import

# K 线
GET /api/bars/daily

# 评估
POST   /api/evals
GET    /api/evals
GET    /api/evals/{run_id}
GET    /api/evals/{run_id}/status
DELETE /api/evals/{run_id}

# 回测
POST   /api/backtests
GET    /api/backtests
GET    /api/backtests/{run_id}
GET    /api/backtests/{run_id}/status
GET    /api/backtests/{run_id}/equity
GET    /api/backtests/{run_id}/orders
GET    /api/backtests/{run_id}/trades
DELETE /api/backtests/{run_id}

# 数据维护
POST /api/admin/bar_1d:aggregate
POST /api/admin/qfq:import
GET  /api/admin/jobs
```

### 7.2 统一响应结构

```json
// 成功
{ "code": 0, "data": {...} }
// 失败
{ "code": 4001, "message": "pool not found", "detail": {...} }
```

### 7.3 轮询策略

MVP 用 HTTP 轮询。Vue Query `refetchInterval` 当状态为 `pending`/`running` 时每 1.5s 轮询 `/status`，终态时停止。

### 7.4 Eval payload 结构

```jsonc
{
  "ic": {
    "1":  { "dates": [...], "values": [...] },
    "5":  { "dates": [...], "values": [...] },
    "10": { "dates": [...], "values": [...] }
  },
  "rank_ic": { "1": {...}, "5": {...}, "10": {...} },
  "group_returns": { "dates": [...], "g1": [...], "g2": [...], ..., "g5": [...] },
  "long_short_equity": { "dates": [...], "values": [...] },
  "turnover_series":   { "dates": [...], "values": [...] },
  "value_hist": { "bins": [...], "counts": [...] }
}
```

## 8. 前端

### 8.1 技术栈

Vue3 + TypeScript + Vite + Naive UI + ECharts + `@tanstack/vue-query` + Pinia + axios。

### 8.2 目录结构

```
frontend/src/
├── main.ts
├── App.vue
├── router/
├── api/          # client.ts + factors.ts + pools.ts + evals.ts + backtests.ts
├── pages/
│   ├── dashboard/
│   ├── factors/{FactorList.vue, FactorDetail.vue}
│   ├── pools/{PoolList.vue, PoolEditor.vue}
│   ├── evals/{EvalCreate.vue, EvalDetail.vue}
│   ├── backtests/{BacktestCreate.vue, BacktestDetail.vue}
│   └── admin/DataOps.vue
├── components/
│   ├── layout/{AppSidebar.vue, AppHeader.vue, StatusBadge.vue}
│   ├── charts/{IcSeriesChart, GroupReturnsChart, EquityCurveChart,
│   │           TurnoverChart, ValueHistogram}
│   ├── forms/{ParamsFormRenderer, PoolSelector, DateRangePicker, FreqSelector}
│   └── tables/{RunsTable, OrdersTable, TradesTable}
├── stores/ui.ts
├── styles/{theme.ts, tokens.scss, global.scss}
└── utils/{format.ts, echarts.ts}
```

### 8.3 路由

```
/                          Dashboard
/factors                   FactorList
/factors/:factor_id        FactorDetail
/pools                     PoolList
/pools/new | /pools/:id    PoolEditor
/evals/new                 EvalCreate (?factor_id= 预填)
/evals/:run_id             EvalDetail
/backtests/new             BacktestCreate
/backtests/:run_id         BacktestDetail
/admin                     DataOps
```

### 8.4 Naive UI 主题（Binance 风格）

```ts
export const binanceThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#F0B90B', primaryColorHover: '#FFD000',
    primaryColorPressed: '#D0980B', primaryColorSuppl: '#F0B90B',
    successColor: '#0ECB81', errorColor: '#F6465D',
    warningColor: '#FFD000', infoColor: '#1EAEDB',
    textColorBase: '#1E2026', textColor1: '#1E2026',
    textColor2: '#32313A', textColor3: '#848E9C',
    bodyColor: '#FFFFFF', cardColor: '#FFFFFF',
    borderColor: '#E6E8EA',
    fontFamily: 'Inter, "BinancePlex", Arial, sans-serif',
    borderRadius: '8px', borderRadiusSmall: '6px',
  },
  Button: { borderRadiusMedium: '6px', heightMedium: '36px' },
  Card:   { borderRadius: '12px', paddingMedium: '20px' },
}
```

字体优先 Inter（开源），BinancePlex 作为可选回退。

### 8.5 关键页面

**EvalCreate**：因子选择 → 动态参数表单（`ParamsFormRenderer` 根据 `params_schema` 渲染）→ 股票池 → 日期范围 → forward_periods tag → n_groups → 提交。

**EvalDetail**：2×3 图表网格（IC 累计、Rank IC、换手率、5 分组累计、多空净值、因子值分布）+ metrics 表 + 导出 + "一键拿这套参数去回测"按钮。

**BacktestDetail**：净值曲线（基准可叠加）+ drawdown + metrics 卡 + 订单/成交表（分页）。

## 9. 任务运行时

### 9.1 任务模型

```python
TASK_POOL = ProcessPoolExecutor(max_workers=FR_TASK_WORKERS, max_tasks_per_child=5)

@router.post("/evals")
def create_eval(body, background_tasks):
    run_id = new_run_id()
    mysql.insert_eval_run(run_id, body, status="pending")
    background_tasks.add_task(_submit, run_id, body)
    return {"code": 0, "data": {"run_id": run_id, "status": "pending"}}

def _submit(run_id, body):
    TASK_POOL.submit(run_eval_entry, run_id, body)
```

### 9.2 失败处理

- 捕获 worker 端异常 → UPDATE `*_runs.status='failed'`, `error_message=traceback`
- API 端持有 future 的弱引用（仅用于取消）；真实状态以 DB 为准
- 进程崩溃由 ProcessPoolExecutor 自动重建

### 9.3 热加载与 worker 的协作

- API 进程：watchdog 监听代码变动 → 更新 `fr_factor_meta.code_hash/version`（前端立刻可看到新版本）
- Worker 进程：`max_tasks_per_child=5` 让进程跑完 5 个任务就退出，再启动时加载新代码
- 手动触发：`POST /api/factors/reload` 关闭并重建 ProcessPool

## 10. 部署与配置

### 10.1 本地开发

```bash
# 后端
cd factor_research/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn api.main:app --reload --port 8000

# 前端
cd factor_research/frontend
npm install
npm run dev
```

### 10.2 Docker Compose

```yaml
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    environment:
      CLICKHOUSE_HOST: 172.30.26.12
      MYSQL_HOST:      172.30.26.12
      QFQ_FACTOR_PATH: /data/qfq/merged_adjust_factors.parquet
      FR_TASK_WORKERS: "2"
    volumes:
      - ./data:/data
  frontend:
    build: ./frontend        # multi-stage: node build → nginx
    ports: ["80:80"]
    depends_on: [backend]
```

### 10.3 环境变量

```
CLICKHOUSE_HOST / PORT / DATABASE
MYSQL_HOST / PORT / USER / PASSWORD / DATABASE
QFQ_FACTOR_PATH
FR_TASK_WORKERS=2
FR_LOG_LEVEL=INFO
FR_HOT_RELOAD=true
FR_OWNER_KEY=default
```

### 10.4 脚本

```
scripts/
  init_clickhouse.sql
  init_mysql.sql
  aggregate_bar_1d.py   # 全量/增量聚合
  import_qfq.py         # 复用 timing_driven/adjust_factor_importer.py
  seed_pools.py         # （可选）沪深300/中证500 示例池
```

### 10.5 测试

后端：`pytest tests/` —— data_service / factor_registry / eval_service 数学正确性 / api 端到端（依赖本地测试 CH + MySQL）。

前端：`vitest` —— 关键组件单测，图表用快照。

## 11. 项目目录总览

```
factor_research/
├── README.md
├── docker-compose.yml
├── docs/
│   └── plans/
│       └── 2026-04-16-factor-research-design.md   ← 本文档
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .env.example
│   ├── api/            # FastAPI 路由
│   ├── runtime/        # ProcessPool + 热加载
│   ├── services/       # EvalService / BacktestService / FactorRegistry
│   ├── engine/         # base_factor + 数学计算
│   ├── factors/        # 用户定义因子（热加载）
│   ├── storage/        # ClickHouse / MySQL 读写
│   ├── scripts/
│   └── tests/
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    └── src/            # 详见 §8.2
```

## 12. 风险与开放问题

1. **复权因子的时效性**：目前手工从 Windows 拷贝 parquet；若忘记更新会导致最新交易日因子值污染。建议在 UI 显示"fr_qfq_factor 最新日期 = XXXX-XX-XX"。
2. **ProcessPool 下的 pandas 内存占用**：5000 只股票 × 5000 日 × 多字段面板数据约几百 MB；并发 2 个任务时峰值可能 1-2 GB。MVP 够用，若扩展到更多 worker 需要监控。
3. **VectorBT 的 freq 参数与真实调仓节奏**：回测默认每日可调仓（`rebalance_period=1`），若改成周调/月调，组合再平衡的现金管理逻辑要复核。
4. **因子值表数据量**：按估算（5000 股 × 5000 日 × 100 因子 × 3 版本 = 7.5 亿行），ClickHouse 单机完全能扛，但 partition 策略需要监控是否有过多小 part。
5. **热加载的边界情况**：因子文件写入时可能处于中间状态（编辑器保存到一半）；watchdog 需要 debounce + 校验 import 成功才入库。
6. **BinancePlex 字体版权**：Binance 的专有字体，不能直接商用。默认使用 Inter 作为替代，BinancePlex 可选。

## 13. 下一步

调用 `writing-plans` 技能生成分步实施计划，计划将按以下大致顺序推进：

1. 项目脚手架 + 配置体系
2. 数据层（ClickHouse/MySQL schema、读取层、复用复权因子导入、日频聚合）
3. 因子基类 + 注册 + 热加载 + 内置示例因子
4. 评估引擎（含数学单测）
5. 回测引擎
6. FastAPI 路由 + 任务模型
7. 前端脚手架 + 主题 + 路由骨架
8. 前端关键页面（按 Dashboard → FactorList → EvalCreate → EvalDetail → BacktestCreate → BacktestDetail → PoolList → DataOps 顺序）
9. Docker Compose 打包
10. 端到端冒烟测试
