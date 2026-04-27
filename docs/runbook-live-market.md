# live_market worker 运维手册

> 与 `backend/workers/live_market.py` 配套；启动 / 停止 / 排障 / 监控的最小操作集。

## 1. 这玩意是干嘛的

在 A 股交易时段内，按 5min 拉取全市场 spot 快照写入 ClickHouse `stock_spot_realtime`，
供 `signal_service` 在用户触发实盘信号时取用作"今日 close 估计"。
盘后 15:00-15:30 可选归档当日全 A 1m K 到 `stock_bar_1m`（默认关闭）。

**不会自动触发信号**——它只采集数据，信号触发由用户在前端 / API 主动发起。

## 2. 上线前准备（一次性）

### 2.1 装 akshare

worker 依赖 akshare 拉取 spot / 1m K，未在 `pyproject.toml` 强制依赖：

```bash
cd backend && uv pip install akshare
# 或 pip install akshare（取决于环境）
```

### 2.2 同步交易日历

worker 启动时查 `fr_trade_calendar` 决定"今天是否要工作"。**没同步过日历 →
worker 永远 idle**（fail-safe 设计）。

```bash
curl -X POST http://localhost:8000/api/admin/sync-calendar
# 或在前端"数据维护"页点对应按钮
```

### 2.3 跑数据库 migration

```bash
clickhouse-client < backend/scripts/migrations/008_realtime_market_tables.sql
mysql -u myuser -pmypassword quant_data < backend/scripts/migrations/009_signal_runs_table.sql
mysql -u myuser -pmypassword quant_data < backend/scripts/migrations/010_signal_runs_add_top_n.sql
```

## 3. 三种运行方式

### 3.1 调试 / 单次跑（本地）

```bash
cd /path/to/factor_research
.venv/bin/python -m backend.workers.live_market --once --log-level DEBUG
```

`--once` 会跑一次完整的 phase 判定 + 对应动作然后退出，方便观察日志。

### 3.2 前台常驻（开发环境）

```bash
.venv/bin/python -m backend.workers.live_market
# 或开归档：
.venv/bin/python -m backend.workers.live_market --archive-1m
```

Ctrl+C 干净退出。

### 3.3 守护进程（生产环境）

#### macOS — launchd

```bash
# 1. 复制模板
cp backend/workers/deploy/live_market.plist.template \
   ~/Library/LaunchAgents/com.factorresearch.live_market.plist

# 2. 把 {{PROJECT_ROOT}} / {{VENV_PYTHON}} / {{LOG_DIR}} 替换为绝对路径
#    例如 /Users/<you>/factor_research, /Users/<you>/factor_research/backend/.venv/bin/python,
#    /var/log/factor_research

# 3. 加载 + 启动
launchctl load ~/Library/LaunchAgents/com.factorresearch.live_market.plist
launchctl start com.factorresearch.live_market

# 4. 查看日志
tail -f /var/log/factor_research/live_market.log

# 5. 停止 / 卸载
launchctl stop com.factorresearch.live_market
launchctl unload ~/Library/LaunchAgents/com.factorresearch.live_market.plist
```

#### Linux — supervisord

```bash
# 1. 复制模板
sudo cp backend/workers/deploy/live_market.supervisor.conf.template \
       /etc/supervisor/conf.d/live_market.conf

# 2. 替换 {{...}} 占位

# 3. 重载 + 启动
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start live_market

# 4. 状态 + 日志
sudo supervisorctl status live_market
tail -f /var/log/factor_research/live_market.log
```

#### Windows — NSSM 或前台 .bat

**最简单：双击 `.bat` 前台运行**

```cmd
copy backend\workers\deploy\live_market.bat.template C:\factor_research\start_live_market.bat
REM 编辑 bat 文件，把 {{PROJECT_ROOT}} / {{VENV_PYTHON}} 替换为绝对路径
C:\factor_research\start_live_market.bat
```

Ctrl+C 退出。适合开发期 / 不需要常驻的场景。

**生产推荐：NSSM 注册成 Windows 服务**

详细步骤见 [`backend/workers/deploy/live_market.nssm.md`](../backend/workers/deploy/live_market.nssm.md)。
要点：

```cmd
REM 1. 下载 NSSM 解压到 C:\nssm\
REM 2. 管理员 cmd 跑：
C:\nssm\win64\nssm.exe install FactorResearchLiveMarket
REM 3. GUI 里配置：
REM    - Application: python.exe 绝对路径
REM    - Arguments: -m backend.workers.live_market [--archive-1m]
REM    - Startup directory: 项目根
REM    - I/O: 指定 stdout/stderr 日志文件
REM    - Environment: MYSQL_* / CLICKHOUSE_*
REM 4. 启动：
sc start FactorResearchLiveMarket
REM 5. 查日志（PowerShell 实时跟踪）：
Get-Content C:\factor_research\logs\live_market.log -Wait -Tail 50
```

## 4. CLI 参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `--no-spot` | False | 关闭 spot 拉取（debug 用） |
| `--spot-interval N` | 300 | spot 间隔秒数；< 60 易触发限流 |
| `--archive-1m` | False | 启用盘后 15:00-15:30 自动 1m K 归档 |
| `--archive-workers N` | 20 | 归档并发数；> 30 易被 akshare 限流 |
| `--once` | False | 单次执行后退出（调试用） |
| `--log-level X` | INFO | DEBUG / INFO / WARNING / ERROR |

## 5. 日志解读

启动时：
```
2026-04-27 09:24:55 [INFO] live_market worker started: LiveMarketConfig(...)
```

盘中正常：
```
2026-04-27 09:30:01 [INFO] [spot] wrote 5024 rows at 09:30:01
2026-04-27 09:35:02 [INFO] [spot] wrote 5024 rows at 09:35:02
```

idle 心跳（每 30min 一条）：
```
2026-04-27 12:00:00 [INFO] [idle] phase=idle trading_day=True now=2026-04-27 12:00:00
```

归档（仅 `--archive-1m` 时）：
```
2026-04-27 15:01:00 [INFO] [eod_archive] starting 1m K archive for 2026-04-27
2026-04-27 15:08:23 [INFO] [eod_archive] done: symbols=5024 bars=1205760 errors=12
```

异常吞掉但继续：
```
2026-04-27 14:35:02 [ERROR] [spot] fetch/write failed; will retry next loop
Traceback (most recent call last):
  ...
```

## 6. 常见问题

### 6.1 worker 启动后一直 idle，不拉 spot？

最可能：`fr_trade_calendar` 没同步当前月份。检查：

```sql
SELECT * FROM fr_trade_calendar WHERE market='CN' AND trade_date >= CURDATE() ORDER BY trade_date LIMIT 5;
```

无结果就调 `/api/admin/sync-calendar` 同步。

### 6.2 IP 被 akshare 限流（HTTP 5xx / timeout 大量出现）？

- 把 `--spot-interval` 调到 600（10min）
- 把 `--archive-workers` 降到 10
- 临时停一段时间让对端 IP 黑名单过期

### 6.3 ClickHouse 写入慢 / 卡住？

worker 单进程串行写，主要瓶颈是 ClickHouse 服务端。检查：

- `SELECT count() FROM quant_data.stock_spot_realtime WHERE trade_date = today();`
- 看 ClickHouse 自身的 system.merges / system.parts，确认没被 background merge 拖死

### 6.4 怎么验证 spot 数据"足够新鲜"？

worker 自己不监控；service 在创建信号时调 `latest_spot_age_sec()`：> 600s 自动
降级到"用昨日 close"模式并在前端 banner 提示。

要主动监控可以加一个 cron 定时调 `/api/admin/...`（暂未实现），或者直接：

```sql
SELECT max(snapshot_at) FROM quant_data.stock_spot_realtime WHERE trade_date = today();
```

值距 `now()` > 10min 时报警。

## 7. 性能参考

- spot 一次拉取（~5024 票 + 写库）：~500ms-1s（取决于网络 + ClickHouse 速度）
- 1m K 归档全 A：~10-15min（240 bar/票 × 5024 票，20 并发）
- worker 进程常驻内存：~50-100MB（Python + 几个连接）

## 8. 关闭整个数据采集

短期（保留进程，不拉新数据）：
```bash
launchctl stop com.factorresearch.live_market    # macOS
sudo supervisorctl stop live_market             # Linux
```

长期（卸载守护）：
```bash
launchctl unload ~/Library/LaunchAgents/com.factorresearch.live_market.plist  # macOS
sudo rm /etc/supervisor/conf.d/live_market.conf && sudo supervisorctl reread  # Linux
```

数据仍在 ClickHouse（不会被 worker 清理），需要清空表再操作。
