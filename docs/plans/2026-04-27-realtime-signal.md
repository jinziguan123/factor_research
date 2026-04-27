# 实盘信号系统 实施 Plan

> **关联设计**：[2026-04-27-realtime-signal-design.md](./2026-04-27-realtime-signal-design.md)

**实施策略**：MVP 路径 = S1 + S3 + S4（约 2.5 天），先把"用户手动触发 → 看到 top 组排名"打通；S2 / S5 后做。

每个 Task 是一个独立 commit，TDD 风格——先写测试或验证方法，再写实现。

---

## Stage 1: 数据采集层（akshare 适配器 + DAO + 表结构）

### S1.T1: 创建 ClickHouse 表 schema

**文件**：`backend/scripts/migrations/008_realtime_market_tables.sql`

**内容**：
- `quant_data.stock_spot_realtime` (ReplacingMergeTree, partition by trade_date, order by (symbol, snapshot_at))
- `quant_data.stock_bar_1m` (同上 schema)

**验证**：
```bash
clickhouse-client --query "SHOW CREATE TABLE quant_data.stock_spot_realtime"
clickhouse-client --query "SHOW CREATE TABLE quant_data.stock_bar_1m"
```

**Commit**：`feat(data) - 实盘信号 ClickHouse 表 schema`

---

### S1.T2: akshare 适配器 + 单测

**文件**：
- `backend/adapters/akshare_live.py` (新建)
- `backend/tests/test_akshare_live.py` (新建)

**核心函数**：
```python
def fetch_spot_snapshot() -> pd.DataFrame:
    """全市场 spot；字段中文 → 英文映射；symbol 加 .SH/.SZ 后缀。"""

def fetch_1m_bars_one(symbol: str) -> pd.DataFrame:
    """单只票当日 1m K（akshare 只接受单 symbol）。"""

def fetch_1m_bars_batch(symbols: list[str], max_workers: int = 20) -> dict[str, pd.DataFrame]:
    """ThreadPoolExecutor 并发拉，失败的 symbol 收集到 errors 不抛异常。"""
```

**单测**（不连真实 akshare，mock 返回值）：
- `test_spot_field_mapping`：mock 返回中文字段，断言转英文 + symbol 后缀对
- `test_spot_handles_suspended`：last_price=0 → is_suspended=1
- `test_symbol_suffix_routing`：6xxx → .SH, 0xxx/3xxx → .SZ, 8xxx → .BJ
- `test_batch_concurrent_collects_errors`：mock 部分票抛异常，断言其它票仍能拿到 + errors 列表正确
- `test_batch_concurrent_no_data_loss`：mock 50 只票，每只返回 5 条 bar，断言总数 250

**Commit**：`feat(adapter) - akshare 实盘行情适配器（spot + 1m K）+ 单测`

---

### S1.T3: DAO（写 ClickHouse）

**文件**：
- `backend/storage/realtime_dao.py` (新建)
- `backend/tests/test_realtime_dao.py` (新建，integration mark 跳过本地无 CK 的环境)

**核心函数**：
```python
def write_spot_snapshot(df: pd.DataFrame, snapshot_at: datetime) -> int:
    """批量 INSERT 到 stock_spot_realtime；ReplacingMergeTree 自动去重。返回写入行数。"""

def write_1m_bars(bars_dict: dict[str, pd.DataFrame]) -> int:
    """批量 INSERT 到 stock_bar_1m。"""

def latest_spot_snapshot(symbols: list[str]) -> pd.DataFrame:
    """取每只票最新一条 spot 记录（FINAL 关键字 + ORDER BY snapshot_at DESC LIMIT 1 BY symbol）。"""

def latest_spot_age_sec() -> float | None:
    """库里最新一条 spot 距 NOW() 的秒数，用于信号服务的降级判断。"""
```

**单测**（integration）：
- `test_write_spot_idempotent`：连写两次相同数据，COUNT 应等于单次（ReplacingMergeTree FINAL 保证）
- `test_latest_spot_returns_one_row_per_symbol`：写 3 个时刻 × 5 只票，断言 latest_spot 返回 5 行最新

**Commit**：`feat(storage) - 实盘行情 DAO（spot / 1m K 批量写 + 查询）`

---

## Stage 3: 信号服务（先于 S2 做，因为 MVP 优先）

> **暂时跳过 S2 worker**，让用户手动触发"拉一次 spot 写库"作为前置 → S3 信号服务能跑通。

### S3.T1: 创建 fr_signal_runs 表 schema

**文件**：`backend/scripts/migrations/009_signal_runs_table.sql`

**内容**：见设计文档 §4.2 完整 schema。

**Commit**：`feat(data) - fr_signal_runs MySQL 表 schema`

---

### S3.T2: signal_service 核心 + 单测

**文件**：
- `backend/services/signal_service.py` (新建)
- `backend/tests/test_signal_service.py` (新建)

**核心入口**：
```python
def run_signal(run_id: str, body: dict) -> None:
    """异步任务入口；状态机 pending → running → success/failed。"""

def _build_signal_close_with_realtime(
    historical_close: pd.DataFrame,
    spot_df: pd.DataFrame,
    today: pd.Timestamp,
) -> pd.DataFrame:
    """把 spot 一行拼到历史 close 末尾作为"today close"。

    停牌票（is_suspended=1 或 last_price=0）用昨日 close ffill；
    新股 / 退市票（spot 缺）跳过。
    """
```

**单测**（纯函数，无 DB）：
- `test_build_close_appends_one_row_for_today`
- `test_suspended_stock_uses_prev_close_ffill`
- `test_missing_symbol_in_spot_is_skipped`
- `test_compose_method_single`：单因子直接用，不走合成
- `test_compose_method_equal_etc`：复用 composition 已有逻辑（mock z_frames）
- `test_top_bottom_extracted_from_last_row_of_W`：构造确定性因子值，断言 top symbol 列表正确
- `test_filter_price_limit_drops_today_limit_up_from_top`

**Commit**：`feat(service) - signal_service 核心：spot 拼接 + 复用 _build_weights 取 W 末行`

---

### S3.T3: signals 路由

**文件**：
- `backend/api/routers/signals.py` (新建)
- `backend/api/schemas.py` (修改：加 `CreateSignalIn`)
- `backend/api/main.py` (修改：注册 router)

**端点**：
```
POST   /api/signals          创建（异步）
GET    /api/signals          列表（带过滤）
GET    /api/signals/{id}     详情（含 payload）
GET    /api/signals/{id}/status
POST   /api/signals/{id}/abort
DELETE /api/signals/{id}
POST   /api/signals/batch-delete   ← 复用上次的 BatchDeleteIn
```

**手动验证**：
```bash
# 启动后端
curl -X POST http://localhost:8000/api/signals \
  -H 'Content-Type: application/json' \
  -d '{"factor_items": [{"factor_id": "momentum_n"}], "method": "single", "pool_id": 5}'
# 应返回 run_id

curl http://localhost:8000/api/signals/<run_id>
# status 转 success 后应有 payload.top
```

**Commit**：`feat(api) - signals 路由（CRUD + 异步触发 + 批删）`

---

## Stage 4: 前端

### S4.T1: api/signals.ts + types

**文件**：
- `frontend/src/api/signals.ts` (新建)

**Hooks**：
```typescript
useSignals(params), useSignal(runId), useCreateSignal(), useDeleteSignal(),
useBatchDeleteSignals(), useAbortSignal()
```

**Commit**：`feat(api) - 前端 signals API hooks`

---

### S4.T2: SignalCreate.vue

**文件**：`frontend/src/pages/signals/SignalCreate.vue`

**表单**：参考 CompositionCreate.vue 结构，多复用：
- 因子动态清单（最多 8 个，复用 NDynamicTags + factor select）
- 合成方法（单因子 = method='single'）
- IC 回看窗口（仅 ic_weighted 显示）
- 股票池
- 分组数
- 用实时数据 / 涨跌停过滤 NSwitch（默认 ON）

**验证**：手动跑通"提交 → 跳详情"。

**Commit**：`feat(ui) - 实盘信号创建页`

---

### S4.T3: SignalList.vue + SignalDetail.vue

**文件**：
- `frontend/src/pages/signals/SignalList.vue`
- `frontend/src/pages/signals/SignalDetail.vue`

**SignalList**：复用 EvalList 结构（过滤 + 表格 + 批删）。

**SignalDetail**：
- 顶部基础信息（NDescriptions）
- top 组表格：列 = 排名 / 代码 / 名称 / 当下涨跌幅 / 因子综合值 / 各子因子值 / 当下报价
- bottom 组表格（同结构）
- spot_meta 摘要（NCard 小卡片）
- 状态 pending/running 时 1.5s 轮询

**Commit**：`feat(ui) - 实盘信号列表 + 详情页`

---

### S4.T4: 路由 + 侧边栏菜单

**文件**：
- `frontend/src/router/index.ts` (3 条路由)
- `frontend/src/layouts/MainLayout.vue` 或菜单配置（加菜单项 "实盘信号"）

**Commit**：`feat(ui) - 实盘信号侧边栏 + 路由`

---

## ⏸ MVP 检查点

到此 MVP 闭环：用户提交因子配置 → 后端拉 spot + 算因子 → 前端看 top 组。
**约 2.5 天**。

下面 S2 / S5 是增强，可酌情排期：

---

## Stage 2: live_market_worker 守护进程

### S2.T1: worker 主进程 + 配置

**文件**：
- `backend/workers/live_market.py` (新建)
- `backend/config/live_market.yaml` (新建)
- `backend/workers/__init__.py`

**主循环**：见设计文档 §5.1 伪代码

**Commit**：`feat(worker) - live_market 主循环 + 配置`

---

### S2.T2: 交易日历 + 阶段判定

**文件**：`backend/workers/trading_calendar.py`

```python
def is_trading_day(date: pd.Timestamp) -> bool: ...
def determine_phase(now: datetime) -> Literal['idle', 'spot', 'eod_archive', 'closed']: ...
```

**单测**：覆盖 9:25 / 9:30 / 11:30 / 13:00 / 15:00 边界 + 周六 + 节假日。

**Commit**：`feat(worker) - 交易日历 + 阶段判定 + 单测`

---

### S2.T3: launchd / supervisord 配置 + 文档

**文件**：
- `backend/workers/live_market.plist` (macOS launchd 模板)
- `backend/workers/live_market.supervisor.conf` (Linux 模板)
- `docs/runbook-live-market.md` (新建运维手册)

**Commit**：`feat(worker) - 守护进程配置 + 运维文档`

---

## Stage 5: 1m K 归档（开关）

### S5.T1: 归档逻辑 + 多线程并发

**文件**：`backend/services/bar_archive_service.py`

**核心函数**：
```python
def archive_1m_bars(target_date: date, max_workers: int = 20) -> dict:
    """盘后批量拉所有票当日 1m K 落库。返回统计 dict（成功 / 失败 / 跳过的 symbol）。"""
```

**单测**：mock akshare，验证并发数、错误收集、ReplacingMergeTree 幂等性。

**Commit**：`feat(archive) - 1m K 归档服务（并发 + 幂等）`

---

### S5.T2: 接入 worker 的 eod_archive 阶段

**文件**：`backend/workers/live_market.py` (修改)

仅当配置 `archive_1m.enabled=true` 时触发。

**Commit**：`feat(worker) - 接入 1m K 归档（默认关闭）`

---

## Stage 6: 文档收尾

### S6.T1: README / CLAUDE.md 加章节

- 添加"实盘信号"章节，介绍架构、如何启动 worker、如何手动触发
- 添加"运维"章节，介绍日志位置、常见错误处理

**Commit**：`docs - 实盘信号 + worker 运维章节`

---

## 进度跟踪建议

每个 Task 完成后：
1. 跑相关单测，确保通过
2. 手动验证（API 端点 / UI 交互）
3. `git commit` （一个 task 一个 commit）
4. 在本文件 Task 标题前加 ✅

到 MVP 检查点（S4.T4 完成）时：
- 在测试环境跑一整个交易日，验证盘中信号能稳定产出
- 收集第一批用户反馈再决定 S2 / S5 排期
