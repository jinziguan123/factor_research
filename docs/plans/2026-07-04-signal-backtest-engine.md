# 信号回测引擎 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 新增第二种回测模式 `signal`——事件驱动、按笔（lot）管理，支持人工止盈止损、加仓、分笔独立/均价统一止损、最小/最大持仓天数，专为事件择时型因子。

**Architecture:** 核心是一个**纯函数** `simulate_signal_book`（逐交易日、先卖后买的事件循环，按 Lot 跟踪持仓），外包一层 `run_signal_backtest` 编排（状态机 + 价格成本 prepare + 落盘），由 `run_backtest` 按 `body["mode"]` 分发。价格/成本 prepare 与现有 vectorbt 分位回测共用。前端建回测表单加"回测模式"分支。

**Tech Stack:** Python 3.10 / pandas / numpy（无新依赖，不用 vectorbt）；后端 FastAPI + Pydantic v2；前端 Vue 3 + Naive UI + TypeScript。测试 pytest（`backend/.venv/bin/pytest`，从项目根运行，`pythonpath=["."]`）。

设计文档：`docs/plans/2026-07-04-signal-backtest-engine-design.md`。

---

## 关键约定（所有任务遵守）

- **测试运行**：`cd` 到项目根，`backend/.venv/bin/pytest backend/tests/test_signal_backtest.py -v`。
- **import 风格**：`from backend.services import signal_backtest as sbt`。
- **成交价/费用口径**（沿用现有 `execution` 层）：
  - 买入有效价 = `exec_price × (1 + slippage)`；现金流出 = `qty × 买入有效价 × (1 + buy_fee_rate)`。
  - 卖出有效价 = `fill_price × (1 - slippage)`；现金流入 = `qty × 卖出有效价 × (1 - sell_fee_rate)`。
  - `buy_fee_rate = (commission_bps + transfer_fee_bps)/1e4`；
    `sell_fee_rate = (commission_bps + transfer_fee_bps + stamp_tax_bps)/1e4`。
- **整手**：`qty = floor(cash_per_lot / 买入有效价 / 100) × 100`；`qty==0` → 跳过该笔。
- **T+1**：t 日出信号 → t+1 日开盘（`exec_price[t+1]`）成交建仓；建仓当日不可卖。
- **触发计价**：止损用当日 `low`、止盈用当日 `high`；入场/强平用 `exec_price`。
- **提交**：每个 Task 末尾 commit，中文 message，结尾带 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

## Phase 1：核心引擎纯函数（TDD）

所有 Phase 1 任务只碰两个文件：
- Create: `backend/services/signal_backtest.py`
- Test: `backend/tests/test_signal_backtest.py`

### 配置与结果数据结构（Task 1 建立，后续任务逐步填充字段）

```python
# signal_backtest.py
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


@dataclass
class SignalConfig:
    signal_threshold: float = 0.0        # 因子值 > 阈值算买入信号
    cash_per_lot: float = 1_000_000.0
    max_concurrent_lots: int = 10
    allow_pyramiding: bool = False
    max_adds_per_symbol: int = 0
    stop_loss_pct: float = 0.08          # 0=关闭
    take_profit_pct: float = 0.20        # 0=关闭
    stop_mode: str = "per_lot"           # per_lot | avg_cost
    min_hold_days: int = 0
    max_hold_days: int = 0               # 0=不限
    buy_fee_rate: float = 0.00026
    sell_fee_rate: float = 0.00076


@dataclass
class Lot:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float                   # 已含滑点+费用前的成交价
    qty: float
    sl_price: float | None
    tp_price: float | None
    lot_id: int
    add_seq: int                         # 0=首仓


@dataclass
class SignalResult:
    equity: pd.Series                    # index=date, 组合净值
    trades: pd.DataFrame                 # 按笔平仓记录
    orders: pd.DataFrame                 # 逐笔买卖单
    skipped: pd.DataFrame                # 被跳过的信号（原因）
```

**主纯函数签名**（所有面板已对齐同 index×columns）：

```python
def simulate_signal_book(
    signal: pd.DataFrame, open_: pd.DataFrame, high: pd.DataFrame,
    low: pd.DataFrame, close: pd.DataFrame, exec_price: pd.DataFrame,
    slippage: pd.DataFrame, limit_up_mask: pd.DataFrame | None,
    limit_down_mask: pd.DataFrame | None, init_cash: float, cfg: SignalConfig,
) -> SignalResult:
    ...
```

`trades` 列：`symbol, entry_date, entry_price, exit_date, exit_price, qty, pnl, return_pct, hold_days, exit_reason(stop_loss|take_profit|max_hold|end_of_data), add_seq, lot_id`。

---

### Task 1：脚手架 + Lot/Config/Result 数据结构

**Step 1: 写失败测试**

```python
# backend/tests/test_signal_backtest.py
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from backend.services import signal_backtest as sbt


def _panels(prices: dict[str, list[float]], dates: pd.DatetimeIndex,
            hl_spread: float = 0.0):
    """构造 open/high/low/close 面板。默认 o=h=l=c=price（无日内波动），
    hl_spread>0 时 high=close*(1+spread), low=close*(1-spread)。"""
    close = pd.DataFrame(prices, index=dates)
    open_ = close.copy()
    high = close * (1 + hl_spread)
    low = close * (1 - hl_spread)
    return open_, high, low, close


def test_config_and_result_types_exist():
    cfg = sbt.SignalConfig(cash_per_lot=1e6)
    assert cfg.stop_mode == "per_lot"
    lot = sbt.Lot(symbol="A", entry_date=pd.Timestamp("2026-01-05"),
                  entry_price=10.0, qty=100, sl_price=9.2, tp_price=12.0,
                  lot_id=1, add_seq=0)
    assert lot.qty == 100
```

**Step 2: 跑测试确认失败** — `... -k test_config_and_result_types_exist -v`，预期 ImportError/AttributeError。

**Step 3: 实现** — 建 `signal_backtest.py`，粘贴上面的 `SignalConfig`/`Lot`/`SignalResult` 定义 + 一个 `simulate_signal_book` 骨架（`raise NotImplementedError`）。

**Step 4: 跑测试确认通过。**

**Step 5: 提交** — `feat(signal-bt): 信号回测引擎脚手架与数据结构`。

---

### Task 2：基础建仓 + 持有 + 数据末尾平仓（无止损止盈）

先把事件循环骨架跑通：出信号→T+1 建仓→持有到数据末尾按 close 强平（`exit_reason="end_of_data"`），逐日 close 估值出净值。此任务 `stop_loss_pct=0, take_profit_pct=0`。

**Step 1: 写失败测试**

```python
def test_entry_t1_and_equity_curve():
    dates = pd.date_range("2026-01-05", periods=5, freq="B")
    # A: 第0日出信号，第1日开盘=10 建仓，之后涨到 12
    open_, high, low, close = _panels(
        {"A": [10, 10, 11, 12, 12]}, dates)
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, max_concurrent_lots=5,
                           stop_loss_pct=0.0, take_profit_pct=0.0,
                           buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(
        signal, open_, high, low, close, close, slip, None, None,
        init_cash=1000.0, cfg=cfg)
    # 第1日开盘价10成交，1000/10=100股（整手）
    assert res.orders.iloc[0]["side"] == "buy"
    assert res.orders.iloc[0]["qty"] == 100
    # 末日净值 = 100股 × 12 = 1200（现金 1000-1000=0）
    assert res.equity.iloc[-1] == pytest.approx(1200.0)
    # 数据末尾强平一笔
    assert (res.trades["exit_reason"] == "end_of_data").all()
    assert len(res.trades) == 1
```

**Step 2: 跑测试确认失败**（NotImplementedError）。

**Step 3: 实现** — 事件循环核心：
- 预转 numpy（`signal/open_/high/low/close/exec_price/slippage` 的 `.to_numpy()`，列序 = `close.columns`）。
- `book: dict[str, list[Lot]]`，`cash`，`equity=[]`，`orders=[]`，`trades=[]`，`skipped=[]`，`lot_counter`。
- 逐日 `t`（`0..n-1`）：**A 出场**（本任务只在 `t==n-1` 末日对所有 Lot 按 `close` 平仓，reason=`end_of_data`）；**B 入场**：`signal[t] > threshold` 的股票，若 `t+1 < n`（有次日成交），并发数未满、现金够 → 用 `exec_price[t+1]` 建仓（本任务成交在 t+1，记 orders）。注意：建仓发生在"处理到 t 日信号时预约 t+1 成交"——实现上更简单的等价写法是：**在 t 日先执行"昨日(t-1)预约的建仓"**。用一个 `pending_entries` 列表跨日传递，避免同日 close 前视。**C 估值**：`cash + Σ qty×close[t]` 入 `equity`。
- 末日把未平仓 Lot 全部按 `close` 平仓写 trades。
- 组装 `SignalResult`（DataFrame 化）。

> 实现提示：把"信号→T+1 成交"实现为 `pending`：遍历到 t 日末尾时，把 t 日新信号加入 `pending`；每日开头先消化 `pending`（用当日 `exec_price` 成交，即上一日信号的次日）。这样天然满足 T+1 且无前视。

**Step 4: 跑测试确认通过。**

**Step 5: 提交** — `feat(signal-bt): 事件循环骨架——T+1建仓/持有/末尾平仓/净值`。

---

### Task 3：止损（当日 low 触发，跳空穿越用 open）

**Step 1: 写失败测试**

```python
def test_stop_loss_intraday_low():
    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    # 建仓价10，止损8%→止损位9.2；第3日 low=9.0 触发止损，成交在9.2
    close = pd.DataFrame({"A": [10, 10, 10, 9.5, 10, 10]}, index=dates)
    open_ = close.copy()
    high = close.copy()
    low = pd.DataFrame({"A": [10, 10, 10, 9.0, 10, 10]}, index=dates)
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.08,
                           take_profit_pct=0.0, buy_fee_rate=0.0,
                           sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    tr = res.trades.iloc[0]
    assert tr["exit_reason"] == "stop_loss"
    assert tr["exit_price"] == pytest.approx(9.2)   # 非跳空，成交在止损位
    assert tr["exit_date"] == dates[3]


def test_stop_loss_gap_through_open():
    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    close = pd.DataFrame({"A": [10, 10, 10, 8.5, 8.5, 8.5]}, index=dates)
    open_ = pd.DataFrame({"A": [10, 10, 10, 8.8, 8.5, 8.5]}, index=dates)  # 跳空开在止损位下方
    high = open_.copy()
    low = pd.DataFrame({"A": [10, 10, 10, 8.3, 8.5, 8.5]}, index=dates)
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.08,
                           take_profit_pct=0.0, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    assert res.trades.iloc[0]["exit_price"] == pytest.approx(8.8)  # 跳空→open成交
```

**Step 2: 跑测试确认失败。**

**Step 3: 实现** — 出场 A 段加止损分支（在末日强平之前、每日对每个 Lot 判定）：
- `sl_price = entry_price × (1 - stop_loss_pct)`（`stop_loss_pct>0` 时，建仓时算好存进 Lot）。
- 若 `stop_loss_pct>0` 且 `low[t] ≤ sl_price`：触发。`fill = open[t] if open[t] ≤ sl_price else sl_price`。
- 卖出：`卖出有效价 = fill × (1 - slippage[t])`；`cash += qty × 卖出有效价 × (1 - sell_fee_rate)`；写 trade（reason=`stop_loss`，`hold_days = 交易日数从建仓次日起`）+ order。
- T+1 约束：`t > entry_index` 才可卖（Task 7 强化，这里先保证不在建仓当日触发——建仓在 t+1，出场从 t+1 之后判定；写实现时确保建仓当日不进出场判定）。

**Step 4/5: 通过 + 提交** — `feat(signal-bt): 止损（盘中低价触发+跳空open成交）`。

---

### Task 4：止盈（当日 high 触发 + min_hold 门控 + 跳空 open）

**Step 1: 写失败测试**

```python
def test_take_profit_intraday_high():
    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    close = pd.DataFrame({"A": [10, 10, 11, 11.5, 11.5, 11.5]}, index=dates)
    open_ = close.copy()
    high = pd.DataFrame({"A": [10, 10, 12.5, 11.5, 11.5, 11.5]}, index=dates)  # 第2日冲到12.5
    low = close.copy()
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.0,
                           take_profit_pct=0.20, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    tr = res.trades.iloc[0]
    assert tr["exit_reason"] == "take_profit"
    assert tr["exit_price"] == pytest.approx(12.0)  # 止盈位10*1.2=12，非跳空成交在12


def test_min_hold_delays_take_profit():
    dates = pd.date_range("2026-01-05", periods=7, freq="B")
    # 建仓在dates[1]（第0日信号）。min_hold=3 → 前3个交易日内不许止盈
    close = pd.DataFrame({"A": [10, 10, 10, 10, 10, 10, 10]}, index=dates)
    high = pd.DataFrame({"A": [10, 10, 20, 20, 20, 20, 20]}, index=dates)  # 建仓后立刻可止盈
    open_ = close.copy(); low = close.copy()
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.0,
                           take_profit_pct=0.20, min_hold_days=3,
                           buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    tr = res.trades.iloc[0]
    # 建仓 dates[1]，min_hold=3 → 最早 dates[1]+3交易日=dates[4] 才允许止盈
    assert tr["exit_date"] == dates[4]
    assert tr["hold_days"] >= 3
```

**Step 2/3/4:** 出场 A 段加止盈分支（优先级**低于**止损，见 Task 5）：`tp_price = entry_price × (1 + take_profit_pct)`；`hold_days = t_index - entry_index`（交易日数）；仅当 `hold_days ≥ min_hold_days` 且 `high[t] ≥ tp_price` 触发；`fill = open[t] if open[t] ≥ tp_price else tp_price`。

**Step 5: 提交** — `feat(signal-bt): 止盈（盘中高价触发+min_hold门控+跳空open）`。

---

### Task 5：同日双触发 → 止损优先

**Step 1: 写失败测试**

```python
def test_same_day_both_hit_stop_wins():
    dates = pd.date_range("2026-01-05", periods=4, freq="B")
    # 第2日 high=13(触止盈12) 且 low=9(触止损9.2) 同日双触发 → 止损优先
    close = pd.DataFrame({"A": [10, 10, 10, 10]}, index=dates)
    open_ = close.copy()
    high = pd.DataFrame({"A": [10, 10, 13, 10]}, index=dates)
    low = pd.DataFrame({"A": [10, 10, 9, 10]}, index=dates)
    signal = pd.DataFrame({"A": [1, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.08,
                           take_profit_pct=0.20, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    assert res.trades.iloc[0]["exit_reason"] == "stop_loss"
```

**Step 2/3/4:** 确保出场判定顺序为**先止损后止盈**（止损命中即 `continue`，不再判止盈）。若 Task 3/4 已按此顺序实现，此测试可能直接通过——仍保留测试锁定行为。

**Step 5: 提交** — `test(signal-bt): 锁定同日双触发止损优先`。

---

### Task 6：最大持仓天数强平

**Step 1: 写失败测试**

```python
def test_max_hold_days_force_exit():
    dates = pd.date_range("2026-01-05", periods=8, freq="B")
    close = pd.DataFrame({"A": [10]*8}, index=dates)
    open_ = close.copy(); high = close.copy(); low = close.copy()
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.0,
                           take_profit_pct=0.0, max_hold_days=3,
                           buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    tr = res.trades.iloc[0]
    assert tr["exit_reason"] == "max_hold"
    # 建仓 dates[1]，持有3个交易日 → dates[4] 强平（走 exec_price=当日open）
    assert tr["exit_date"] == dates[4]
```

**Step 2/3/4:** 出场 A 段加第3优先级：`max_hold_days>0` 且 `hold_days ≥ max_hold_days` → 按 `exec_price[t]×(1-slip)` 卖出，reason=`max_hold`。优先级：止损 > 止盈 > 到期。

**Step 5: 提交** — `feat(signal-bt): 最大持仓天数到期强平`。

---

### Task 7：A股 T+1（建仓当日不可卖）

**Step 1: 写失败测试** — 建仓当日即使 low 跌破止损位也不卖，顺延到次日。

```python
def test_t1_no_same_day_sell():
    dates = pd.date_range("2026-01-05", periods=5, freq="B")
    # dates[0]信号 → dates[1]建仓。dates[1]当日 low=8(破止损) 但T+1不许卖 → dates[2]才卖
    close = pd.DataFrame({"A": [10, 9, 9, 9, 9]}, index=dates)
    open_ = pd.DataFrame({"A": [10, 10, 9, 9, 9]}, index=dates)
    high = close.copy()
    low = pd.DataFrame({"A": [10, 8, 8, 9, 9]}, index=dates)
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.08,
                           take_profit_pct=0.0, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    assert res.trades.iloc[0]["exit_date"] == dates[2]  # 非 dates[1]
```

**Step 2/3/4:** 出场判定加守卫 `t_index > entry_index`（建仓当日 `t_index == entry_index` 跳过所有出场）。`hold_days = t_index - entry_index`。

**Step 5: 提交** — `feat(signal-bt): A股T+1建仓当日不可卖`。

---

### Task 8：per_lot vs avg_cost 两种止损模式

**Step 1: 写失败测试** — 同股两笔不同成本，验证两种模式触发差异。

```python
def test_avg_cost_mode_uses_blended_stop():
    dates = pd.date_range("2026-01-05", periods=8, freq="B")
    # dates[0]、dates[2] 两次信号 → dates[1]@10、dates[3]@12 两笔建仓
    close = pd.DataFrame({"A": [10, 10, 12, 12, 11.0, 11.0, 11.0, 11.0]}, index=dates)
    open_ = close.copy(); high = close.copy()
    low = pd.DataFrame({"A": [10, 10, 12, 12, 10.4, 11, 11, 11]}, index=dates)
    signal = pd.DataFrame({"A": [1, 0, 1, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    base = dict(cash_per_lot=1200, stop_loss_pct=0.08, take_profit_pct=0.0,
                allow_pyramiding=True, max_adds_per_symbol=1,
                max_concurrent_lots=5, buy_fee_rate=0.0, sell_fee_rate=0.0)
    # avg_cost：均价=(10*100+12*100)/200=11，止损位11*0.92=10.12；dates[4] low=10.4 未破→不触发
    res_avg = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
        slip, None, None, 2400.0, sbt.SignalConfig(stop_mode="avg_cost", **base))
    assert (res_avg.trades["exit_reason"] == "end_of_data").all()
    # per_lot：首笔止损位10*0.92=9.2（不破），第二笔12*0.92=11.04；dates[4] low=10.4<11.04→第二笔止损
    res_lot = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
        slip, None, None, 2400.0, sbt.SignalConfig(stop_mode="per_lot", **base))
    reasons = set(res_lot.trades["exit_reason"])
    assert "stop_loss" in reasons
```

**Step 2/3/4:** 实现：
- `per_lot`：每 Lot 独立 sl/tp，独立判定与平仓（Task 3-7 已是此路径）。
- `avg_cost`：同股所有 Lot 视为合并仓——维护该股的加权均价 `avg = Σ(entry_price×qty)/Σqty`，`sl=avg×(1-sl_pct)`、`tp=avg×(1+tp_pct)`；触发时**整只清仓**（该股所有 Lot 一起卖，各自写一条 trade，exit_price/reason 相同）。每次加仓后重算 `avg`。`min_hold/max_hold` 在 avg_cost 模式下以**最早一笔**的 entry 计。

**Step 5: 提交** — `feat(signal-bt): per_lot/avg_cost 两种止损模式`。

---

### Task 9：加仓（等额、max_adds 上限、资金不足跳过）

**Step 1: 写失败测试**

```python
def test_pyramiding_equal_add_and_cap():
    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    close = pd.DataFrame({"A": [10, 10, 10, 10, 10, 10]}, index=dates)
    open_ = close.copy(); high = close.copy(); low = close.copy()
    # 连续3次信号，但 max_adds_per_symbol=1 → 最多首仓+1加仓=2笔
    signal = pd.DataFrame({"A": [1, 1, 1, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, allow_pyramiding=True,
                           max_adds_per_symbol=1, max_concurrent_lots=10,
                           stop_loss_pct=0.0, take_profit_pct=0.0,
                           buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 5000.0, cfg)
    buys = res.orders[res.orders["side"] == "buy"]
    assert len(buys) == 2                       # 首仓 + 1次加仓
    assert set(res.trades["add_seq"]) == {0, 1}


def test_pyramiding_disabled_skips_adds():
    dates = pd.date_range("2026-01-05", periods=5, freq="B")
    close = pd.DataFrame({"A": [10]*5}, index=dates)
    open_=close.copy(); high=close.copy(); low=close.copy()
    signal = pd.DataFrame({"A": [1, 1, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, allow_pyramiding=False,
                           stop_loss_pct=0.0, take_profit_pct=0.0,
                           buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 5000.0, cfg)
    assert len(res.orders[res.orders["side"]=="buy"]) == 1
    assert len(res.skipped) >= 1                # 第二次信号被跳过并记录
```

**Step 2/3/4:** 入场 B 段区分未持仓/已持仓：已持仓且 `allow_pyramiding` 且该股 `add_seq` 计数 `< max_adds_per_symbol` 且现金够 → 加一笔等额 Lot（`add_seq = 现有笔数`）。`allow_pyramiding=False` 或达上限 → 记 `skipped`（reason=`pyramiding_disabled`/`max_adds_reached`）。

**Step 5: 提交** — `feat(signal-bt): 等额加仓+max_adds上限+跳过记录`。

---

### Task 10：并发上限 + 资金不足跳过

**Step 1: 写失败测试** — 多股同日信号，`max_concurrent_lots`/现金不足时按列序建仓、其余记 skipped。

```python
def test_max_concurrent_and_cash_cap():
    dates = pd.date_range("2026-01-05", periods=4, freq="B")
    close = pd.DataFrame({"A": [10]*4, "B": [10]*4, "C": [10]*4}, index=dates)
    open_=close.copy(); high=close.copy(); low=close.copy()
    signal = pd.DataFrame({"A":[1,0,0,0],"B":[1,0,0,0],"C":[1,0,0,0]},
                          index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A","B","C"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, max_concurrent_lots=2,
                           stop_loss_pct=0.0, take_profit_pct=0.0,
                           buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 10000.0, cfg)
    assert len(res.orders[res.orders["side"]=="buy"]) == 2   # 只建2笔（A、B）
    assert (res.skipped["symbol"] == "C").any()              # C 因并发上限跳过
```

**Step 2/3/4:** 入场时校验 `当前总 Lot 数 < max_concurrent_lots` 且 `cash ≥ 建仓所需现金`；不满足记 `skipped`（reason=`max_concurrent`/`insufficient_cash`）。同日多信号按 `close.columns` 顺序处理，保证可复现。

**Step 5: 提交** — `feat(signal-bt): 并发笔数与现金上限约束`。

---

### Task 11：费用/滑点接入（买卖不对称）

**Step 1: 写失败测试** — 非零费率+滑点，验证现金流与成交价方向正确。

```python
def test_fees_and_slippage_asymmetric():
    dates = pd.date_range("2026-01-05", periods=4, freq="B")
    close = pd.DataFrame({"A": [10, 10, 10, 10]}, index=dates)
    open_ = close.copy(); high = close.copy(); low = close.copy()
    signal = pd.DataFrame({"A": [1, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.01, index=dates, columns=["A"])   # 1% 滑点
    cfg = sbt.SignalConfig(cash_per_lot=100000, stop_loss_pct=0.0,
                           take_profit_pct=0.0, buy_fee_rate=0.001,
                           sell_fee_rate=0.002)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 100000.0, cfg)
    buy = res.orders[res.orders["side"]=="buy"].iloc[0]
    # 买入有效价 = 10*(1+0.01)=10.1；qty=floor(100000/10.1/100)*100
    assert buy["price"] == pytest.approx(10.1)
    expected_qty = (100000 // (10.1) // 100) * 100
    assert buy["qty"] == expected_qty
    sell = res.orders[res.orders["side"]=="sell"].iloc[0]
    assert sell["price"] == pytest.approx(10 * (1 - 0.01))   # 卖出有效价含反向滑点
```

**Step 2/3/4:** 建仓 qty 用买入有效价（含滑点）计算并整手；现金流出乘 `(1+buy_fee_rate)`；卖出（含止损/止盈/到期）成交价乘 `(1-slippage)`、现金流入乘 `(1-sell_fee_rate)`。止损/止盈的 `fill` 先定（stop_price 或 open），再乘反向滑点。`pnl = 卖出净现金 - 买入净现金`；`return_pct = pnl / 买入净现金`。orders 的 `price` 记**含滑点的有效成交价**。

**Step 5: 提交** — `feat(signal-bt): 买卖不对称费用与滑点接入`。

---

### Task 12：涨跌停锁定 + 停牌不可交易

**Step 1: 写失败测试**

```python
def test_limit_and_suspension_block_trades():
    dates = pd.date_range("2026-01-05", periods=5, freq="B")
    close = pd.DataFrame({"A": [10, 10, 9, 9, 9]}, index=dates)
    open_ = close.copy(); high = close.copy()
    low = pd.DataFrame({"A": [10, 10, 8, 9, 9]}, index=dates)  # dates[2] 破止损
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    # dates[2] 封跌停 → 卖不出，顺延到 dates[3]
    ld = pd.DataFrame({"A": [False, False, True, False, False]}, index=dates)
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.08,
                           take_profit_pct=0.0, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, ld, 1000.0, cfg)
    assert res.trades.iloc[0]["exit_date"] == dates[3]  # 跌停日卖不出，顺延
```

**Step 2/3/4:** 出场时若 `limit_down_mask[t]` 为真 → 该股当日卖不出，跳过（下一交易日再判）。入场时若 `limit_up_mask[t]` 为真或 `exec_price/close` 为 NaN（停牌）→ 不建仓，记 skipped（reason=`limit_up`/`suspended`）。出场时 `close/low` 为 NaN（停牌）→ 不触发。

**Step 5: 提交** — `feat(signal-bt): 涨跌停锁定与停牌不可交易`。

---

### Task 13：指标汇总 + 无未来函数校验

**Step 1: 写失败测试**

```python
def test_summary_metrics():
    res = _run_simple_two_trades()   # 构造1胜1负两笔的辅助（本任务内联）
    m = sbt.summarize(res)
    assert 0.0 <= m["win_rate"] <= 1.0
    assert "profit_factor" in m
    assert "avg_hold_days" in m
    assert "exit_reason_dist" in m


def test_no_lookahead_truncation():
    dates = pd.date_range("2026-01-05", periods=30, freq="B")
    rng = np.random.default_rng(3)
    px = 10 + np.cumsum(rng.normal(0, 0.2, 30))
    close = pd.DataFrame({"A": px}, index=dates)
    open_=close.copy()
    high=close*1.02; low=close*0.98
    signal = pd.DataFrame({"A": (px < np.roll(px,1)).astype(float)}, index=dates)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.05,
                           take_profit_pct=0.10, buy_fee_rate=0.0, sell_fee_rate=0.0)
    def run(upto):
        sl = slice(None, upto)
        return sbt.simulate_signal_book(signal.loc[sl], open_.loc[sl], high.loc[sl],
            low.loc[sl], close.loc[sl], open_.loc[sl], slip.loc[sl], None, None,
            1000.0, cfg)
    full = run(dates[-1]); trunc = run(dates[20])
    # 截断点前已平仓的 trade 应与全量完全一致
    a = full.trades[full.trades["exit_date"] <= dates[20]].reset_index(drop=True)
    b = trunc.trades[trunc.trades["exit_reason"] != "end_of_data"].reset_index(drop=True)
    assert len(a) == len(b)
    for col in ["symbol","entry_date","exit_date","exit_price","exit_reason"]:
        assert list(a[col]) == list(b[col])
```

**Step 2/3/4:** 实现 `summarize(res) -> dict`：`win_rate`（pnl>0 笔占比）、`profit_factor`（Σ盈利/│Σ亏损│）、`avg_hold_days`、`exit_reason_dist`（各 reason 计数）、`total_trades`、`skipped_count`。无未来函数由事件循环的因果性天然保证——测试锁定。

**Step 5: 提交** — `feat(signal-bt): 指标汇总+无未来函数截断校验`。

---

## Phase 2：编排与接线

### Task 14：抽出共享的价格/成本 prepare

**Files:** Modify `backend/services/backtest_service.py`

**Step 1: 写测试**（`backend/tests/test_signal_backtest_wiring.py`）——调用新 `_prepare_price_cost(body)` 返回含 `open/high/low/close/exec_price/slippage/limit masks/daily_amount` 的结构，且对已有 quantile 路径无行为改变（跑 `test_run_backtest_smoke.py` 保绿）。

**Step 2/3/4:** 把 `_prepare_backtest_inputs` 中"因子加载 + `_aligned` + exec_price + slippage + 涨跌停 masks"部分抽成 `_prepare_price_cost(body) -> PriceCostBundle`（dataclass）。`_prepare_backtest_inputs`（quantile）改为调用它再拼 `size` 权重。**关键：跑 `test_run_backtest_smoke.py` 和 `test_backtest_lookahead.py` 确认 quantile 路径不回归。**

**Step 5: 提交** — `refactor(backtest): 抽出共享价格/成本 prepare`。

---

### Task 15：`run_signal_backtest` 编排

**Files:** Modify `backend/services/signal_backtest.py`（加 `run_signal_backtest`）

**Step 1: 写测试** — mock DB 层（参照 `test_run_backtest_smoke.py` 的 fixture）跑一次 signal 回测，断言产出 equity/trades parquet + metrics payload 结构。

**Step 2/3/4:** 实现 `run_signal_backtest(run_id, body)`：复用 `_update_status`/`check_abort`/`_prepare_price_cost`；把 body 的信号配置读进 `SignalConfig`（`buy_fee_rate/sell_fee_rate` 由 bps 换算）；因子宽表 → `signal` 面板；调 `simulate_signal_book` + `summarize`；落 `equity/trades/orders.parquet`；写 `fr_backtest_metrics`（total_return 从 equity 首末算、win_rate/trade_count 从 summarize）+ 3 条 `fr_backtest_artifacts`；`payload_json` 放 summarize 全量 + 基准对比。**复用 `_benchmark_metrics`、`_nan_to_none`、parquet 写法。**

**Step 5: 提交** — `feat(signal-bt): run_signal_backtest 编排与落盘`。

---

### Task 16：`mode` 分发

**Files:** Modify `backend/services/backtest_service.py:604`（`run_backtest` 顶部）

**Step 1: 写测试** — `body={"mode":"signal",...}` 时断言路由到 `run_signal_backtest`（monkeypatch 打桩计数）。

**Step 2/3/4:** `run_backtest` 开头：`if str(body.get("mode","quantile")) == "signal": from backend.services.signal_backtest import run_signal_backtest; return run_signal_backtest(run_id, body)`。保持 `backtest_entry` 不变（分发在 service 内）。

**Step 5: 提交** — `feat(backtest): 按 mode 分发 quantile/signal 引擎`。

---

### Task 17：`CreateBacktestIn` schema 字段

**Files:** Modify `backend/api/schemas.py:61`

**Step 1: 写测试** — 构造带 signal 字段的 `CreateBacktestIn`，断言默认值与类型；不传时向后兼容（`mode` 默认 `quantile`）。

**Step 2/3/4:** 加字段（全部带默认）：`mode:str="quantile"`、`signal_threshold`、`cash_per_lot`、`max_concurrent_lots`、`allow_pyramiding`、`max_adds_per_symbol`、`stop_loss_pct`、`take_profit_pct`、`stop_mode`、`min_hold_days`、`max_hold_days`，`Field` 加合理 `ge/le`。

**Step 5: 提交** — `feat(api): 回测请求体新增 signal 模式字段`。

---

## Phase 3：前端

### Task 18：API 类型字段

**Files:** Modify `frontend/src/api/backtests.ts`

**Step 1-4:** 在 create 请求体 TS 类型里加 §3 的可选字段（`mode?` 及各 signal 字段），与后端 schema 对齐。若有 create 调用点，透传新字段。

**Step 5: 提交** — `feat(fe): 回测 API 类型加 signal 模式字段`。

---

### Task 19：`BacktestCreate.vue` 模式分支

**Files:** Modify `frontend/src/pages/backtests/BacktestCreate.vue`

**Step 1-4:**
- 顶部加 `n-radio-group` "回测模式"：`quantile`（分位换仓，默认）/ `signal`（信号驱动）。ref `mode`。
- `mode==='quantile'` 时显示现有：分组数/调仓周期/持仓方式/权重方法（用 `v-if="mode==='quantile'"` 包裹）。
- `mode==='signal'` 时显示（`v-if="mode==='signal'"`）：止盈%、止损%、止损模式(`n-select` per_lot|avg_cost)、每笔金额、最大并发笔数、允许加仓(`n-switch`)、每股最大加仓笔数(仅 allow_pyramiding 时)、最小持仓天数、最大持仓天数、信号阈值。用 `n-input-number`/`n-select`/`n-switch`，样式对齐现有 `n-form-item`。
- 执行/成本项（成交价/费率/滑点/涨跌停/初始资金）两模式共用。
- 提交时把 `mode` 与对应字段并入请求体。

**Step 5: 提交** — `feat(fe): 建回测表单加信号回测模式分支`。

---

### Task 20：`BacktestDetail.vue` 按笔展示（按需）

**Files:** Modify `frontend/src/pages/backtests/BacktestDetail.vue`

**Step 1-4:** 若详情页 trades 表是通用列渲染，确认新列（`exit_reason/hold_days/add_seq`）能显示；指标区展示 `win_rate/profit_factor/avg_hold_days/exit_reason_dist`（signal 模式）。若详情页强耦合 vectorbt 字段，做最小适配：signal 模式下读 payload_json 的 summarize 字段渲染。**先看代码再决定改动面，能复用就不新增组件。**

**Step 5: 提交** — `feat(fe): 回测详情按笔成交与信号指标展示`。

---

## 收尾

- 全量跑 `backend/.venv/bin/pytest backend/tests/ -q` 确认无回归。
- 前端 `cd frontend && npm run build`（或 typecheck）确认编译通过。
- 用 `oversold_crash_bottom_reversal` 因子跑一次 signal 回测端到端冒烟（真实股票池），核对净值/按笔 trades 合理。

## 明确不做（YAGNI，见设计文档 §11）

跟踪止损、ATR 止损、卖出/做空信号、条件加仓、金字塔倍数加仓——本轮不实现，预留字段不加。
