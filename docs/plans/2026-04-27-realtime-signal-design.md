# 实盘信号 / 盘中因子选股 设计文档

**日期**：2026-04-27
**作者**：与 Claude 协作
**状态**：设计草案，待用户确认后进入实现 plan

---

## 1. 目标 (Goal)

在现有"评估 + 回测 + 多因子合成"系统之上，新增**盘中实盘信号**能力：

1. 用户可在交易时段内，**手动**触发对一组因子（单或多）按当下市场快照计算选股排名；
2. 输出当日 top 组（多头候选）+ bottom 组（空头候选）+ 每只票的当下因子值 / 分位标签；
3. **顺便**支持分钟 K 线落库归档（独立开关，默认关闭）；
4. 不对接券商，仅产出"建议清单"，由用户自己执行。

## 2. 非范围 (Non-Goals)

- ❌ 自动下单 / 券商接口（QMT、Ptrade、xtquant 等）
- ❌ 持仓状态管理（每次信号是无状态快照，不跟踪"当前账户持仓"）
- ❌ 真实成交模拟 / 滑点模型
- ❌ 自动触发（cron / 收盘前自动）—— **MVP 仅手动**，下一阶段再加
- ❌ tick 级因子（仅支持已有的日频因子，盘中以"实时快照价 = 今日 close 估计"喂入）
- ❌ 盘中分钟级专属因子（如"今日 5min 累计涨幅"等，需要分钟级 OHLC 数据完备后再做）

## 3. 整体架构

### 3.1 三层架构图

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: 实盘数据采集（新增独立进程）              │
│  backend/workers/live_market.py                    │
│                                                     │
│  ─ 盘中：spot_em 全市场快照（一次 HTTP，~300ms）    │
│       周期 5min，写 ClickHouse stock_spot_realtime │
│  ─ 盘后：hist_min_em 全量 1m K 拉取（多线程）       │
│       依赖配置开关，默认关闭                        │
│  ─ 进程模型：launchd / supervisord 守护             │
│  ─ 交易日历：9:30-11:30 + 13:00-15:00 工作          │
│       其它时段 sleep                                │
└─────────────────────────────────────────────────────┘
                         ↓ 数据流
┌─────────────────────────────────────────────────────┐
│  Layer 2: 信号计算（新增）                          │
│  backend/services/signal_service.py                │
│                                                     │
│  入口：run_signal(run_id, body) -> None            │
│  ─ 复用 backtest_service._prepare_backtest_inputs  │
│  ─ 注入"今日 = spot 快照价"作为 close 最后一行     │
│  ─ 复用 _build_weights → 取 W 矩阵最后一行         │
│  ─ 多因子合成复用 composition_service              │
│  ─ 涨跌停过滤默认开启（盘中比回测更必要）          │
│                                                     │
│  入口路由：                                         │
│    POST /api/signals       创建（异步）             │
│    GET  /api/signals       列表                     │
│    GET  /api/signals/{id}  详情（含 top 组）        │
│    DELETE /api/signals/{id}                        │
└─────────────────────────────────────────────────────┘
                         ↓ 持久化
┌─────────────────────────────────────────────────────┐
│  Layer 3: 展示                                      │
│  ─ MySQL: fr_signal_runs (主表) + JSON payload     │
│  ─ 前端：                                            │
│    SignalCreate.vue   (新建)                        │
│    SignalList.vue     (历史触发列表)                │
│    SignalDetail.vue   (top 组排名 + 当下行情)       │
└─────────────────────────────────────────────────────┘
```

### 3.2 数据流分离

**两条数据流完全独立，互不依赖**：

| 数据流 | 数据源 | 频率 | 落库 | 用途 |
|---|---|---|---|---|
| **A. spot 快照** | `stock_zh_a_spot_em` | 5min（盘中常驻） | ClickHouse `stock_spot_realtime` | 给 signal_service 算因子 |
| **B. 1m K 归档** | `stock_zh_a_hist_min_em` | 1m / 5min（可配） | ClickHouse `stock_bar_1m` | 历史回看、未来分钟级因子 |

**关键洞见**：信号服务只依赖 A，不依赖 B。即便 B 没开，盘中信号也能跑。

## 4. 数据模型

### 4.1 新增 ClickHouse 表

#### `quant_data.stock_spot_realtime`
盘中全市场快照，按时间和 symbol 主键。

```sql
CREATE TABLE IF NOT EXISTS quant_data.stock_spot_realtime (
    symbol      LowCardinality(String),  -- "000001.SZ"
    snapshot_at DateTime,                 -- 拉取时刻（精度到秒）
    trade_date  Date,                     -- 当日（便于按日分区）
    last_price  Float64,                  -- 最新成交价（来源 spot_em.最新价）
    open        Float64,
    high        Float64,
    low         Float64,
    prev_close  Float64,                  -- 昨收
    pct_chg     Float64,                  -- 当下涨跌幅
    volume      Int64,                    -- 累计成交量
    amount      Float64,                  -- 累计成交额
    bid1        Float64,                  -- 买一
    ask1        Float64,                  -- 卖一
    is_suspended UInt8                    -- 是否停牌（spot 中 last=0 视为停）
)
ENGINE = ReplacingMergeTree
PARTITION BY trade_date
ORDER BY (symbol, snapshot_at);
```

**ReplacingMergeTree 选择理由**：每 5min 一次快照，同一只票同一秒不会重复，但极端情况下重启或重试可能写双份，靠 (symbol, snapshot_at) 去重。

#### `quant_data.stock_bar_1m`
1m K 线归档表。

```sql
CREATE TABLE IF NOT EXISTS quant_data.stock_bar_1m (
    symbol      LowCardinality(String),
    trade_time  DateTime,                  -- bar 起始时间，如 2026-04-27 09:30:00
    trade_date  Date,
    open        Float64,
    high        Float64,
    low         Float64,
    close       Float64,
    volume      Int64,
    amount      Float64
)
ENGINE = ReplacingMergeTree
PARTITION BY trade_date
ORDER BY (symbol, trade_time);
```

**幂等性**：`ReplacingMergeTree` + 主键 (symbol, trade_time) 让"重复拉取同一分钟"自动去重，配合 #3 决策里讨论的"akshare 累积快照语义"，保证不丢、不重。

### 4.2 新增 MySQL 表

#### `fr_signal_runs`
每次信号触发的主表，结构对齐 `fr_factor_eval_runs` + `fr_composition_runs`。

```sql
CREATE TABLE IF NOT EXISTS fr_signal_runs (
    run_id           VARCHAR(64) PRIMARY KEY,
    -- 信号配置（复用 composition 的字段）
    factor_items_json JSON,                          -- [{factor_id, params}, ...]
    method           VARCHAR(32) DEFAULT 'equal',    -- equal / ic_weighted / orthogonal_equal / single
    pool_id          INT NOT NULL,
    n_groups         INT DEFAULT 5,
    -- 触发时机
    as_of_time       DATETIME NOT NULL,              -- 触发时刻（盘中调用时是 NOW()）
    as_of_date       DATE NOT NULL,                  -- 当日交易日
    -- 数据源选择
    use_realtime     TINYINT(1) DEFAULT 1,           -- 1=用 spot 快照, 0=用昨日 close
    filter_price_limit TINYINT(1) DEFAULT 1,         -- 默认开（盘中更必要）
    -- 状态
    status           ENUM('pending','running','success','failed','aborting','aborted') DEFAULT 'pending',
    progress         INT DEFAULT 0,
    error_message    TEXT,
    -- 时间
    created_at       DATETIME(6) NOT NULL,
    started_at       DATETIME(6),
    finished_at      DATETIME(6),
    -- 输出
    n_holdings_top   INT,                             -- top 组实际入选数（剔除涨停后）
    n_holdings_bot   INT,
    payload_json     JSON,                            -- {top: [...], bottom: [...], spot_meta: {...}}
    INDEX idx_as_of_date (as_of_date),
    INDEX idx_status (status),
    INDEX idx_created (created_at)
);
```

**`payload_json` 结构**：

```json
{
  "top": [
    {"symbol": "600519.SH", "name": "贵州茅台",
     "factor_value_composite": 1.82, "factor_value_breakdown": {"momentum_n": 0.95, "bbic": 0.87},
     "weight": 0.05, "last_price": 1620.5, "pct_chg": 0.012}
  ],
  "bottom": [...],
  "weights": {"momentum_n": 0.6, "bbic": 0.4},  // 仅 ic_weighted
  "ic_contributions": {...},                      // 复用 composition 已有逻辑
  "spot_meta": {
    "snapshot_at": "2026-04-27 14:30:15",
    "n_symbols_total": 5024,
    "n_suspended": 13,
    "n_limit_up": 87,
    "n_limit_down": 14
  }
}
```

## 5. Layer 1: 数据采集层详细设计

### 5.1 进程模型

`backend/workers/live_market.py`：独立进程，**不挂在 FastAPI 主进程上**。

启动方式：
```bash
cd backend && python -m backend.workers.live_market --config config/live_market.yaml
```

进程职责：
1. **状态机**：根据当前时刻判断进入哪个阶段
2. **盘中**（9:25-11:30, 13:00-15:00）：每 5min 拉一次 spot，写 ClickHouse
3. **盘后**（15:00-15:30）：批量拉 1m K 全量（仅当配置开关开启）
4. **非交易时段**：sleep + 30min 心跳日志

```python
# 伪代码
async def main_loop():
    while True:
        now = datetime.now()
        phase = determine_phase(now)  # 'idle' | 'spot' | 'eod_archive' | 'closed'

        if phase == 'spot':
            try:
                await fetch_and_write_spot()
            except Exception as e:
                log.error("spot fetch failed: %s", e)
                # 失败不退出进程，下一轮继续
            await asyncio.sleep(SPOT_INTERVAL_SEC)

        elif phase == 'eod_archive' and config.archive_1m_enabled:
            try:
                await fetch_and_write_1m_bars()
            except Exception:
                log.exception("1m archive failed")
            # 一天只跑一次，标记完成后睡到次日
            mark_archived(today)

        else:  # idle / closed
            await asyncio.sleep(60)
```

### 5.2 akshare 适配器

`backend/adapters/akshare_live.py`：

```python
def fetch_spot_snapshot() -> pd.DataFrame:
    """全市场实时报价快照（一次 HTTP，~300ms，~5000 票）。

    返回字段：[symbol, last_price, open, high, low, prev_close, pct_chg, volume, amount, ...]
    错误处理：HTTP 超时 / 字段缺失 → 抛 RuntimeError 让上层重试。
    """

def fetch_1m_bars(symbol: str, start_time: datetime, end_time: datetime) -> pd.DataFrame:
    """单只票当日 1m K 序列（无时间窗口入参，akshare 默认返回当日全量）。

    返回字段：[trade_time, open, high, low, close, volume, amount]
    """

def fetch_1m_bars_batch(symbols: list[str], max_workers: int = 20) -> pd.DataFrame:
    """ThreadPoolExecutor 并行拉取 N 只票当日 1m K，合并成宽表后返回。

    并发数默认 20（实测安全值）；失败的 symbol 跳过 + 收集错误列表。
    """
```

### 5.3 关键工程坑（必须处理）

#### 5.3.1 交易日历
A 股节假日不交易，临时停市偶发。盘中 worker 必须先查"今天是否交易日"。

来源：复用现有 `quant_data.stock_bar_1d` 的最近一条 trade_date，或独立维护 `quant_data.trading_calendar` 表。

#### 5.3.2 集合竞价 vs 连续竞价
- 9:15-9:25 集合竞价，**不出 1m K**，spot_em 也可能返回开盘前价
- 9:30:00 第一根 1m K 才生成
- 11:30-13:00 午休，无新 K
- 14:57-15:00 收盘集合竞价

**策略**：worker 在这些边界时段不强行拉数据，避免拉到无效快照。具体阈值：
- 9:25 之前：sleep
- 9:25-9:30：可拉 spot（集合竞价价），不拉 1m K
- 9:30-11:30：正常
- 11:30-13:00：sleep
- 13:00-15:00：正常
- 15:00 后：触发 archive 一次

#### 5.3.3 字段映射
akshare `stock_zh_a_spot_em` 返回的字段名是中文（"代码"、"最新价"、"涨跌幅"...），适配器内统一转英文 + 标准 symbol（带 .SH / .SZ 后缀）。

A 股代码补 `.SH/.SZ` 规则：
- 6 开头 → 沪市 `.SH`
- 0/3 开头 → 深市 `.SZ`
- 8/4 开头 → 北交所 `.BJ`（暂不入选范围）

#### 5.3.4 IP 频控
- spot_em 一次 HTTP 拉全量，频率 5min 一次，**不会触发限流**
- hist_min_em 单次单票，5000 票 × 20 线程 ≈ 75-150s，**临界但通常不触发**
- 兜底：失败重试 3 次 + 指数退避，全市场失败率 > 5% 触发告警（写日志 + 不阻塞）

#### 5.3.5 配置文件
`backend/config/live_market.yaml`（新增）：

```yaml
spot:
  enabled: true
  interval_sec: 300        # 5min
  retry_max: 3

archive_1m:
  enabled: false           # 默认关闭（用户 #3 决策）
  trigger_after_close: true
  max_workers: 20

trading_hours:
  morning: ["09:25", "11:30"]
  afternoon: ["13:00", "15:00"]

clickhouse_database: "quant_data"
log_level: "INFO"
```

## 6. Layer 2: 信号服务详细设计

### 6.1 核心入口

```python
# backend/services/signal_service.py

def run_signal(run_id: str, body: dict) -> None:
    """计算一次实盘信号。

    Args:
        run_id: fr_signal_runs.run_id (路由层 INSERT 时生成)
        body: {
            factor_items: [{factor_id, params}, ...],
            method: 'equal' | 'ic_weighted' | 'orthogonal_equal' | 'single',
            pool_id: int,
            n_groups: int = 5,
            use_realtime: bool = True,        # True=spot, False=昨日 close
            filter_price_limit: bool = True,
        }

    流程：
    1. 加载因子（复用 _load_or_compute_factor）
    2. 加载 close panel：
       - 历史段：从 fr_qfq_factor + stock_bar_1d 取
       - 当日：根据 use_realtime
         - True: 从 stock_spot_realtime 取最新一行 last_price，作为 today close
         - False: 取昨日 close 当 today close（保守降级）
    3. 拼接 close（历史 + 当日实时一行）
    4. 复用 _build_weights 构造权重
    5. 取最后一行 W 作为 top/bottom 候选
    6. 写 fr_signal_runs.payload_json
    """
```

### 6.2 关键设计：盘中 close 拼接

```
历史 close panel (来自 stock_bar_1d, 已 qfq):
    2026-04-25  600519.SH  1612.30
    2026-04-26  600519.SH  1620.50

实时快照 (来自 stock_spot_realtime 最新一条):
    snapshot_at='2026-04-27 14:30:15', symbol='600519.SH', last_price=1635.80

拼接后喂给因子的 close:
    2026-04-25  600519.SH  1612.30   (历史)
    2026-04-26  600519.SH  1620.50   (历史)
    2026-04-27  600519.SH  1635.80   (实时一行，trade_date=今日)
```

注意点：
- **复权一致性**：当日实时价是不复权的，需要用昨日的复权因子近似复权（A 股复权因子在交易日内不变）
- **停牌票**：spot 数据中 last_price=0 或 is_suspended=1 → close 用昨日 close ffill
- **新股 / 退市票**：spot 没有该 symbol → 跳过

### 6.3 多因子合成的口径

复用 `composition_service` 的 3 种 method，但 IC 权重的计算需要选择窗口：

- **方案 A**：用过去 60-120 个交易日的历史 IC 算权重，盘中实盘信号用这个权重
- **方案 B**：把 IC 权重的回看窗口作为 schema 字段暴露给用户

**MVP 选 B**：用户在创建信号时，类似 composition 一样指定 `ic_lookback_days`（默认 60）。

### 6.4 涨跌停过滤复用

直接复用 `backtest_service._compute_price_limit_mask` + `_build_weights(excluded_mask=...)`，
保持与回测口径一致。盘中场景：
- "今日触板" 用 spot 数据当下的 pct_chg 判断
- 触板的票从 top / bottom 候选中剔除

## 7. Layer 3: 前端展示

### 7.1 路由 & 侧边栏

新增菜单项「实盘信号」：
- `/signals` → SignalList.vue（历史信号列表）
- `/signals/new` → SignalCreate.vue
- `/signals/:runId` → SignalDetail.vue

### 7.2 SignalCreate.vue

表单（复用 CompositionCreate / BacktestCreate 的 widget）：
- 因子清单（NDynamicTags 风格，最多 8 个）
- 合成方法（NSelect: equal / ic_weighted / orthogonal_equal / single）
- IC 回看窗口（仅 ic_weighted 显示，默认 60）
- 股票池（PoolSelector）
- 分组数（NInputNumber，默认 5）
- 用实时数据（NSwitch，默认 ON）
- 涨跌停过滤（NSwitch，默认 ON）
- 提交按钮 → POST /api/signals → 跳详情

### 7.3 SignalDetail.vue

布局：
1. 顶部：基础信息（触发时间 / 数据源 / 股票池 / 状态）
2. 中部：top 组排名表（NDataTable）
   - 列：排名 / 代码 / 名称 / 当下涨跌幅 / 因子综合值 / 各子因子值 / 当下报价
3. 下部：bottom 组排名表（同结构）
4. 侧栏：spot_meta 摘要（快照时间 / 涨停数 / 跌停数 / 停牌数）

刷新机制：
- 状态 pending/running 时 1.5s 轮询（已有模式）
- success 后停轮询；用户可手动"再次触发"按钮重跑

## 8. 工程取舍与已知风险

### 8.1 实时性 vs 一致性

- 盘中信号是 **5min 延迟**（spot 拉取间隔）
- 用户想要"绝对实时"则要降到 1min 间隔，但 5000 票快照接口对 akshare 服务端压力线性放大
- **MVP 5min 起步**，不够再调

### 8.2 因子计算的"未成熟"问题

日频因子在盘中只能拿到"今日尚未收盘"的近似值：
- `momentum_n(120)`：今日影响 1/120，几乎可忽略
- `realized_vol(20)`：今日 close 估计偏差 → vol 估计也偏差，但量级小
- `bbic`：MA(3) 今日权重 1/3，近似性最差

**结论**：盘中信号在 14:00 之后已较稳定，14:30+ 接近收盘价、最可靠。
**前端提示**：在信号详情页显示"快照时刻：14:30，距离收盘 30min，置信度: 中等"等提示。

### 8.3 worker 进程稳定性

独立进程的运维问题：
- 进程崩溃：依赖 launchd / supervisord 自动重启
- 数据库断连：连接池自动重连，单次写入失败不退进程
- 日志：写到 `logs/live_market.log`，按日 rotate
- 监控：暂时手工 `tail -f`，未来可加健康检查端点

### 8.4 数据完整性兜底

如果 worker 在某一段时间挂了，spot 数据会缺一段。补偿：
- 信号服务发现 `stock_spot_realtime` 最新一条距 NOW() > 10min 时，**降级**到 `use_realtime=false`（用昨日 close）+ 提示用户
- 1m K 归档若中途失败，盘后批量任务在 15:30 整点再触发一次（兜底）

## 9. 分阶段实施路径

| Stage | 内容 | 工作量 | 验证 |
|---|---|---|---|
| **S1** | DataService + 表结构 + akshare 适配器 + 单测 | 1 天 | spot 拉取写库成功，单测覆盖字段映射 |
| **S2** | live_market_worker 主循环 + 配置 + 进程守护 | 0.5 天 | worker 跑一个完整交易日，日志无错误 |
| **S3** | signal_service + signals 路由 + fr_signal_runs 表 + 单测 | 1 天 | 手动触发一次成功生成 top 组 |
| **S4** | 前端 SignalCreate / List / Detail | 0.5 天 | 完整 UX 跑通 |
| **S5** | 1m K 归档（可选开关） + 多线程 + 盘后批量 | 0.5 天 | 1m K 落库不丢、不重 |
| **S6** | 文档 + CLAUDE.md 加运维章节 | 0.25 天 | |
| **总计** | | **~3.75 天** | |

**MVP 优先级**：S1 + S3 + S4 是核心（约 2.5 天），S2（自动 worker）可暂时手动跑 spot 拉取脚本，S5 完全可后做。

## 10. 待确认问题（用户已确认全部，留作记录）

1. 数据源 → akshare ✅
2. 订阅范围 → 关注池（默认全盘）✅
3. K 线粒度 → 1m，实时落盘默认关闭，配置开关 ✅
4. 触发方式 → MVP 仅手动 ✅
5. 部署 → 独立进程 + supervisord ✅

## 11. 后续可能演进（Out of MVP）

- **自动触发**：cron 定时（每 30min / 收盘前 5min）
- **持仓状态跟踪**：fr_portfolio_state 表，记录用户当前实盘持仓 → 算"加减仓"
- **分钟级因子**：基于 `stock_bar_1m` 的 `intraday_momentum_5m`、`opening_breakout` 等
- **多用户**：当前单用户假设，未来加 user_id 隔离
- **告警**：信号产生后通过邮件 / IM 推送
