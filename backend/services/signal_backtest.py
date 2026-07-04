"""信号回测引擎核心纯函数。

面向择时/事件型因子（因子值即买入信号），采用事件驱动、按笔(lot)管理的
回测方式，与现有 vectorbt 分位回测并存。本模块提供配置(SignalConfig)、
持仓笔(Lot)、结果(SignalResult)等数据结构，以及回测核心函数
simulate_signal_book（逻辑由后续任务逐步实现）。
"""
from __future__ import annotations
from dataclasses import dataclass
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
    entry_price: float                   # 成交价（不含费用）
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


def simulate_signal_book(
    signal: pd.DataFrame, open_: pd.DataFrame, high: pd.DataFrame,
    low: pd.DataFrame, close: pd.DataFrame, exec_price: pd.DataFrame,
    slippage: pd.DataFrame, limit_up_mask: pd.DataFrame | None,
    limit_down_mask: pd.DataFrame | None, init_cash: float, cfg: SignalConfig,
) -> SignalResult:
    """事件驱动、按笔管理的信号回测核心（后续任务逐步实现）。

    本阶段实现事件循环骨架：出信号→T+1 建仓→持有到数据末尾按 close 强平
    （exit_reason="end_of_data"），逐日 close 估值出净值。用 pending 队列
    保证 T+1（t 日信号只能在 t+1 日用 exec_price 成交），天然无未来函数。
    """
    symbols = list(close.columns)
    dates = close.index
    n = len(dates)

    # 预转 numpy：列序=symbols，行序=dates
    sig_np = signal.to_numpy(float)
    open_np = open_.to_numpy(float)
    high_np = high.to_numpy(float)
    low_np = low.to_numpy(float)
    close_np = close.to_numpy(float)
    exec_np = exec_price.to_numpy(float)
    slip_np = slippage.to_numpy(float)

    # 状态
    book: dict[str, list[Lot]] = {sym: [] for sym in symbols}
    cash: float = float(init_cash)
    equity: list[float] = []
    orders: list[dict] = []
    trades: list[dict] = []
    skipped: list[dict] = []
    lot_counter: int = 0
    entry_index: dict[int, int] = {}   # lot_id -> 建仓行号

    # pending 建仓意向：上一日产生、待今日成交，元素为列索引 j
    pending_entries: list[int] = []

    for t in range(n):
        # 1) 先消化 pending：用当日 exec_price[t] 成交建仓
        next_pending: list[int] = []
        for j in pending_entries:
            sym = symbols[j]
            eff_buy = exec_np[t, j] * (1.0 + slip_np[t, j])
            if not np.isfinite(eff_buy) or eff_buy <= 0:
                continue
            qty = np.floor(cfg.cash_per_lot / eff_buy / 100.0) * 100.0
            cost = qty * eff_buy * (1.0 + cfg.buy_fee_rate)
            if qty <= 0 or cost > cash:
                # 本任务先简单丢弃该意向（skipped 明细留给后续任务完善）
                continue
            cash -= cost
            lot_counter += 1
            lot = Lot(symbol=sym, entry_date=dates[t], entry_price=eff_buy,
                      qty=qty, sl_price=None, tp_price=None,
                      lot_id=lot_counter, add_seq=0)
            book[sym].append(lot)
            entry_index[lot_counter] = t
            orders.append({"date": dates[t], "symbol": sym, "side": "buy",
                           "price": eff_buy, "qty": qty})
        pending_entries = next_pending

        # 2) 出场：本任务只在末日按 close 强平所有未平仓 Lot
        if t == n - 1:
            for jj, sym in enumerate(symbols):
                if not book[sym]:
                    continue
                for lot in book[sym]:
                    eff_sell = close_np[t, jj] * (1.0 - slip_np[t, jj])
                    proceeds = lot.qty * eff_sell * (1.0 - cfg.sell_fee_rate)
                    cost_in = lot.qty * lot.entry_price * (1.0 + cfg.buy_fee_rate)
                    pnl = proceeds - cost_in
                    ei = entry_index[lot.lot_id]
                    cash += proceeds
                    trades.append({
                        "symbol": sym,
                        "entry_date": lot.entry_date,
                        "entry_price": lot.entry_price,
                        "exit_date": dates[t],
                        "exit_price": eff_sell,
                        "qty": lot.qty,
                        "pnl": pnl,
                        "return_pct": pnl / cost_in if cost_in else 0.0,
                        "hold_days": t - ei,
                        "exit_reason": "end_of_data",
                        "add_seq": lot.add_seq,
                        "lot_id": lot.lot_id,
                    })
                    orders.append({"date": dates[t], "symbol": sym,
                                   "side": "sell", "price": eff_sell,
                                   "qty": lot.qty})
                book[sym] = []

        # 3) 产生今日信号的 pending（下一日消化）
        if t + 1 < n:
            for j in range(len(symbols)):
                if sig_np[t, j] > cfg.signal_threshold:
                    pending_entries.append(j)

        # 4) 估值：现金 + 所有持仓按当日 close 计价
        holdings_val = 0.0
        for j, sym in enumerate(symbols):
            for lot in book[sym]:
                holdings_val += lot.qty * close_np[t, j]
        equity.append(cash + holdings_val)

    equity_s = pd.Series(equity, index=dates)

    trade_cols = ["symbol", "entry_date", "entry_price", "exit_date",
                  "exit_price", "qty", "pnl", "return_pct", "hold_days",
                  "exit_reason", "add_seq", "lot_id"]
    order_cols = ["date", "symbol", "side", "price", "qty"]
    skip_cols = ["date", "symbol", "reason"]

    trades_df = pd.DataFrame(trades, columns=trade_cols) if trades \
        else pd.DataFrame(columns=trade_cols)
    orders_df = pd.DataFrame(orders, columns=order_cols) if orders \
        else pd.DataFrame(columns=order_cols)
    skipped_df = pd.DataFrame(skipped, columns=skip_cols) if skipped \
        else pd.DataFrame(columns=skip_cols)

    return SignalResult(equity=equity_s, trades=trades_df,
                        orders=orders_df, skipped=skipped_df)
