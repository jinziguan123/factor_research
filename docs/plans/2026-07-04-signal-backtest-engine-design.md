# 事件驱动·按笔管理的信号回测引擎 — 设计文档

- 日期：2026-07-04
- 状态：已与用户确认，待实现
- 相关：现有 vectorbt 分位回测见 `2026-06-23-backtest-realism-redesign.md`

## 1. 动机与问题

现有回测只有一种模式：vectorbt `from_orders` 的**因子分位定期换仓**——按调仓日对因子值
`qcut` 分组、取 top 分位等权持有到下一调仓日。它有两个硬伤，使其无法回测"事件/择时型
因子"（因子值=买入信号，如 `oversold_crash_bottom_reversal` 超跌金叉）：

1. **没有止盈止损**：出场只发生在调仓日，与价格涨跌无关。
2. **持仓级、无分笔**：同一股票是净额持仓（单一均价），无法表达"同股多次信号分笔建仓、
   每笔独立止盈止损"。

调研结论（build-vs-buy）：
- **vectorbt** `from_signals` 的 `sl_stop`/`tp_stop` 是**持仓级**（针对单一均价），做不到分笔
  独立止损；分笔需付费版 PRO 的 stop ladder 或极易出错的 Numba 回调。
- **backtrader** 事件驱动、bracket/OCO 天然支持分笔独立止损，但已停止主要开发、较慢，且需把
  现有 A股费用/T+1/涨跌停/DataService 模型适配进它。
- 需求（分笔独立止损 + 加仓 + 最小/最大持仓 + A股T+1）是**强路径依赖**，向量化不擅长。

**决策：自建一个轻量事件驱动、按笔（lot）管理的信号回测引擎**，作为第二种回测模式与现有
vectorbt 分位模式并存，复用现有 A股费用/滑点/涨跌停/T+1 原语，不引入新依赖。

## 2. 范围

本轮交付：后端引擎 + API + 单测 + **前端建回测表单分支**（用户明确要求前端本轮一起做）。

- 回测请求体新增 `mode: "quantile" | "signal"`（默认 `quantile`，向后兼容）。
- `mode="signal"` 走新引擎 `run_signal_backtest`；`mode="quantile"` 走现有 `run_backtest`。
- 产出（equity/trades/orders parquet + metrics + 基准对比）与现有回测对齐，前端详情页复用。

## 3. 用户可配置项（`mode="signal"` 时生效）

| 组 | 字段 | 类型/默认 | 含义 |
|---|---|---|---|
| 入场 | `signal_threshold` | float, 0.0 | 因子值 **>** 阈值算买入信号 |
| 仓位 | `cash_per_lot` | float, init_cash/10 | 每笔固定投入金额 |
| | `max_concurrent_lots` | int, 10 | 全组合最大并发持仓笔数（资金/风险上限） |
| | `allow_pyramiding` | bool, false | 是否允许对已持仓股加仓 |
| | `max_adds_per_symbol` | int, 0 | 每只股最多**额外**加仓笔数（allow_pyramiding=true 时生效） |
| 出场 | `stop_loss_pct` | float, 0.08 | 止损%（相对该笔/均价成本），0=关闭 |
| | `take_profit_pct` | float, 0.20 | 止盈%，0=关闭 |
| | `stop_mode` | str, "per_lot" | `per_lot` 分笔独立 ／ `avg_cost` 按均价统一 |
| | `min_hold_days` | int, 0 | 最小持仓交易日（止损优先，不锁止损） |
| | `max_hold_days` | int, 0 | 最大持仓交易日，0=不限；到期强平 |

执行/成本参数（`exec_price`/`commission_bps`/`stamp_tax_bps`/`transfer_fee_bps`/`slippage_bps`/
`impact_coef`/`filter_price_limit`/`lock_price_limit`/`init_cash`）**沿用现有字段与语义**。
分位专用字段（`n_groups`/`rebalance_period`/`position`/`weighting`/…）在 signal 模式下忽略。

## 4. 核心数据结构

```python
@dataclass
class Lot:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float      # 已含滑点的成交价
    qty: float              # 股数（按 100 股整手向下取整）
    sl_price: float | None  # per_lot 模式下每笔独立；avg_cost 模式用合并仓的均价重算
    tp_price: float | None
    lot_id: int
    add_seq: int            # 0=首仓，1..N=第 N 次加仓

# Book: symbol -> list[Lot]（同股多笔）
```

- `stop_mode="per_lot"`：每个 Lot 独立 sl/tp（= entry_price×(1±pct)），独立触发、只卖该笔。
- `stop_mode="avg_cost"`：同股所有 Lot 视为合并仓，sl/tp 按持仓加权**均价**动态重算
  （每次加仓后刷新）；触发时整只清仓。

## 5. 事件循环（逐交易日，先卖后买）

对每个交易日 t，遍历股票（固定字典序，保证可复现），每股先处理出场、再处理入场：

### A. 出场（优先级从高到低）
对该股的每个 Lot（`avg_cost` 模式下按合并仓判定一次）：

1. **止损（最高优先，止损优先于锁仓）**：当日 `low ≤ sl_price` → 卖。
   成交价 = 跳空（`open ≤ sl_price`）时用 `open`（更不利），否则用 `sl_price`。
2. **止盈**：`min_hold_days` 已满足 且 当日 `high ≥ tp_price` → 卖。
   成交价 = 跳空（`open ≥ tp_price`）时用 `open`，否则用 `tp_price`。
3. **到期强平**：持仓交易日数 ≥ `max_hold_days` → 次日开盘价卖（走 `exec_price`）。

约束：
- **A股 T+1**：入场当日不可卖；`min_hold_days`、`max_hold_days` 均从入场次日起计。
- `lock_price_limit=true` 且当日封跌停 → 卖不出，滞留到下一交易日（沿用现有涨跌停语义）。
- 停牌（价缺失）当日：不触发、不成交，顺延。

### B. 入场/加仓（当日因子值 > `signal_threshold`）
- **未持仓**：并发笔数 < `max_concurrent_lots` 且 现金 ≥ `cash_per_lot` 且 非封涨停/停牌
  → 开新 Lot。
- **已持仓**：`allow_pyramiding` 且 该股 add_seq 数 < `max_adds_per_symbol` 且 现金够
  → 加一笔**等额** Lot（`cash_per_lot`）。`avg_cost` 模式下刷新合并仓均价与 sl/tp。
- 成交价 = **T+1 开盘**（`exec_price`，含费用+滑点+冲击），与现有回测口径一致。
- 资金不足 / 达并发上限 / 封涨停 → 跳过该信号并记录到 skipped 明细。

### C. 估值
当日 `close` mark 全部持仓 + 现金 → 当日净值，写 equity 曲线。

### D. ⚙️ 边界规则（已与用户确认的默认）
- 同日 `high` 触止盈 **且** `low` 触止损 → **止损优先**（悲观，避免高估收益）。
- 触发计价用当日 `high`/`low`；入场/强平用 `T+1 open`。
- 费用方向相关：买入=佣金+过户费；卖出=佣金+过户费+印花税（复用 `execution` 层）。

## 6. 复用映射（避免重复造轮子）

| 需求 | 复用现有 |
|---|---|
| 因子信号面板 | `_load_or_compute_factor`（factor.compute → 宽表；signal=值>阈值） |
| open/high/low/close 对齐面板 | `_prepare_backtest_inputs` 内的 `_aligned(field)` 逻辑，抽公共 helper |
| 成交价（T+1 open/vwap） | `execution.build_exec_price` |
| 方向相关费用/滑点/冲击 | `execution` 现有费率数组构造 |
| 涨跌停/停牌可交易性 | `_compute_directional_limit_masks` |
| 状态机/进度/中断 | `_update_status`、`check_abort` |
| 落盘 & 指标 payload | `_stats_to_payload` 思路、`_benchmark_metrics`、parquet 写法、metrics/artifacts DB 写入 |

为复用 open/high/low/close + 成本 prep，把 `_prepare_backtest_inputs` 中"价格与成本"部分抽成
共享函数 `_prepare_price_cost(body)`，quantile 与 signal 两条路径共用；signal 路径不构造 `size`
权重（那是分位专用）。

## 7. 后端改动

- 新增 `backend/services/signal_backtest_service.py`：
  - `run_signal_backtest(run_id, body)`：状态机 + prepare + 事件循环 + 落盘（结构对齐 `run_backtest`）。
  - `simulate_signal_book(...)` **纯函数**：吃 open/high/low/close/signal 面板 + 费用数组 + 配置，
    吐 (equity Series, trades DataFrame, orders DataFrame, skipped 明细)。可独立单测、不碰 DB。
- `backtest_entry` / `run_backtest` 顶部按 `body.get("mode","quantile")` 分发到新引擎。
- `CreateBacktestIn` 增加 §3 的字段（全部带默认值，向后兼容）。

## 8. 前端改动（本轮）

`frontend/src/pages/backtests/BacktestCreate.vue`（Naive UI，`n-form-item` 平铺表单）：
- 顶部加"回测模式"`n-radio-group`：分位换仓 / 信号驱动。
- `mode="signal"` 时：
  - 隐藏分位专用项（分组数 / 调仓周期 / 持仓方式 / 权重方法）。
  - 显示 §3 的信号配置项：止盈% / 止损% / 止损模式(per_lot|avg_cost) / 每笔金额 /
    最大并发笔数 / 允许加仓(switch) / 每股最大加仓笔数 / 最小持仓天数 / 最大持仓天数 /
    信号阈值。
  - 执行/成本项（成交价、费率、滑点、涨跌停开关、初始资金）两模式共用。
- `frontend/src/api/backtests.ts` 的 create 请求体类型加上新字段。
- 详情页 `BacktestDetail.vue`：trades 表增加"触发原因/持有天数/加仓序号"列；指标区增加
  胜率 / 盈亏比 / 平均持有天数 / 触发原因分布（若详情页是通用渲染则最小改动）。

## 9. 产出与指标

- `equity.parquet`（净值）、`trades.parquet`（**按笔**：symbol/entry_date/entry_price/exit_date/
  exit_price/qty/pnl/return_pct/hold_days/exit_reason(stop_loss|take_profit|max_hold)/add_seq）、
  `orders.parquet`（逐笔买卖单）。
- 指标：总收益 / 年化 / 最大回撤 / 夏普 + **胜率 / 盈亏比 / 平均持有天数 / 触发原因分布 /
  跳过信号数**；基准对比复用等权市场组合。

## 10. 测试

`backend/tests/` 新增 `test_signal_backtest.py`，对 `simulate_signal_book` 纯函数构造已知价格序列，逐条验证：
1. 止损触发（含跳空穿越用 open 成交）
2. 止盈触发（含 min_hold 未满足时止盈被延后）
3. 止损优先于最小持仓锁仓
4. 同日双触发 → 止损优先
5. 最大持仓天数强平
6. A股 T+1：入场当日不可卖
7. `per_lot` vs `avg_cost` 两种止损模式行为差异
8. 加仓：等额加仓、`max_adds_per_symbol` 上限、资金不足跳过
9. `max_concurrent_lots` 并发上限
10. 无未来函数：截断未来数据重算，历史成交/净值不变

## 11. 明确不做（YAGNI）

- 跟踪止损（trailing）/ ATR 动态止损（本轮固定百分比；预留 `stop_type` 扩展位不实现）。
- 卖出信号 / 反向做空（本因子只有买入信号）。
- 浮盈/回调条件加仓（本轮加仓只在"再次出现买入信号"时触发）。
- 分笔金字塔倍数加仓（本轮等额；`cash_per_lot` 已够表达）。
