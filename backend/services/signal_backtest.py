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

    def _sell(lot: Lot, sym: str, jj: int, t: int,
              fill_price: float, reason: str) -> None:
        """在 t 日以 fill_price 卖出并平掉 lot：计算有效卖价（含滑点）、
        现金流入、pnl，记录 trade + sell order，并从 book[sym] 移除。
        供止损/止盈/到期/末日强平等所有出场分支复用。"""
        nonlocal cash
        eff_sell = fill_price * (1.0 - slip_np[t, jj])
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
            "exit_reason": reason,
            "add_seq": lot.add_seq,
            "lot_id": lot.lot_id,
        })
        orders.append({"date": dates[t], "symbol": sym, "side": "sell",
                       "price": eff_sell, "qty": lot.qty})
        book[sym].remove(lot)

    for t in range(n):
        # 1) 先消化 pending：用当日 exec_price[t] 成交建仓
        next_pending: list[int] = []
        for j in pending_entries:
            sym = symbols[j]
            # 加仓门控：已持仓该股时，本次信号视为加仓意向。
            #   - 首仓（book[sym] 为空）永远允许（并发/资金上限由后续任务处理）。
            #   - 已持仓且 not allow_pyramiding → 跳过，记 pyramiding_disabled。
            #   - 已加仓次数达上限（已有 len-1 笔加仓 >= max_adds）→ 跳过，
            #     记 max_adds_reached。
            n_lots = len(book[sym])
            add_seq = n_lots  # 首仓0、第1次加仓1、以此类推
            if n_lots > 0:
                if not cfg.allow_pyramiding:
                    skipped.append({"date": dates[t], "symbol": sym,
                                    "reason": "pyramiding_disabled"})
                    continue
                if n_lots - 1 >= cfg.max_adds_per_symbol:
                    skipped.append({"date": dates[t], "symbol": sym,
                                    "reason": "max_adds_reached"})
                    continue
            # 并发上限：当前总持仓笔数（含本日已建的首仓/加仓）达上限则跳过。
            # 循环内实时重算，保证同日多信号按列序建仓、超限者被拒。
            total_lots = sum(len(v) for v in book.values())
            if total_lots >= cfg.max_concurrent_lots:
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "max_concurrent"})
                continue
            eff_buy = exec_np[t, j] * (1.0 + slip_np[t, j])
            if not np.isfinite(eff_buy) or eff_buy <= 0:
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "insufficient_cash"})
                continue
            qty = np.floor(cfg.cash_per_lot / eff_buy / 100.0) * 100.0
            cost = qty * eff_buy * (1.0 + cfg.buy_fee_rate)
            if qty <= 0 or cost > cash:
                # 数量为 0 或现金不足：记 skipped 便于复盘"没吃到"的信号
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "insufficient_cash"})
                continue
            cash -= cost
            lot_counter += 1
            sl_price = (eff_buy * (1.0 - cfg.stop_loss_pct)
                        if cfg.stop_loss_pct > 0 else None)
            tp_price = (eff_buy * (1.0 + cfg.take_profit_pct)
                        if cfg.take_profit_pct > 0 else None)
            lot = Lot(symbol=sym, entry_date=dates[t], entry_price=eff_buy,
                      qty=qty, sl_price=sl_price, tp_price=tp_price,
                      lot_id=lot_counter, add_seq=add_seq)
            book[sym].append(lot)
            entry_index[lot_counter] = t
            orders.append({"date": dates[t], "symbol": sym, "side": "buy",
                           "price": eff_buy, "qty": qty})
        pending_entries = next_pending

        # 2) 出场：优先级 止损 > 止盈 > 到期 > 末日强平。
        #    stop_mode 决定判定粒度：
        #    - per_lot：每笔独立 sl/tp，独立判定与平仓；
        #    - avg_cost：同 symbol 全部未平仓 Lot 视为合并仓，用持仓加权
        #      均价算统一 sl/tp，触发时整只清仓（各写一条 trade）。
        if cfg.stop_mode == "avg_cost":
            for jj, sym in enumerate(symbols):
                lots = book[sym]
                if not lots:
                    continue
                # T+1 守卫：用最早一笔建仓行号；t<=该值则整只跳过出场
                earliest_ei = min(entry_index[lot.lot_id] for lot in lots)
                if t <= earliest_ei:
                    continue
                # 合并仓持仓加权均价
                tot_qty = sum(lot.qty for lot in lots)
                avg = sum(lot.entry_price * lot.qty for lot in lots) / tot_qty
                sl = avg * (1.0 - cfg.stop_loss_pct) if cfg.stop_loss_pct > 0 else None
                tp = avg * (1.0 + cfg.take_profit_pct) if cfg.take_profit_pct > 0 else None
                hold_days = t - earliest_ei
                fill = None
                reason = None
                # 止损（风控优先，不受 min_hold 约束）
                if sl is not None and low_np[t, jj] <= sl:
                    fill = open_np[t, jj] if open_np[t, jj] <= sl else sl
                    reason = "stop_loss"
                # 止盈（受 min_hold 约束）
                elif (tp is not None and hold_days >= cfg.min_hold_days
                        and high_np[t, jj] >= tp):
                    fill = open_np[t, jj] if open_np[t, jj] >= tp else tp
                    reason = "take_profit"
                # 到期
                elif cfg.max_hold_days > 0 and hold_days >= cfg.max_hold_days:
                    fill = exec_np[t, jj]
                    reason = "max_hold"
                if reason is not None:
                    # 整只清仓：_sell 改 book[sym]，用快照遍历
                    for lot in list(lots):
                        _sell(lot, sym, jj, t, fill, reason)
        else:
            for jj, sym in enumerate(symbols):
                if not book[sym]:
                    continue
                # 复制一份遍历，_sell 会从 book[sym] 移除，避免边遍历边删
                for lot in list(book[sym]):
                    # T+1 守卫：建仓当日不可卖，只对 t>建仓行号 的 Lot 判出场
                    if t <= entry_index[lot.lot_id]:
                        continue
                    # 止损：当日 low 触及止损位；跳空穿越（open<=sl）则以 open 成交
                    # 止损优先级最高，不受 min_hold 约束（风控优先）
                    if lot.sl_price is not None and low_np[t, jj] <= lot.sl_price:
                        fill = (open_np[t, jj] if open_np[t, jj] <= lot.sl_price
                                else lot.sl_price)
                        _sell(lot, sym, jj, t, fill, "stop_loss")
                        continue  # 已平仓，不再判止盈（同 lot 同日只出场一次）
                    # 止盈：当日 high 触及止盈位，且满足 min_hold；
                    # 跳空高开（open>=tp）则以 open 成交，否则以止盈位成交
                    hold_days = t - entry_index[lot.lot_id]
                    if (lot.tp_price is not None
                            and hold_days >= cfg.min_hold_days
                            and high_np[t, jj] >= lot.tp_price):
                        fill = (open_np[t, jj] if open_np[t, jj] >= lot.tp_price
                                else lot.tp_price)
                        _sell(lot, sym, jj, t, fill, "take_profit")
                        continue  # 已平仓，不再判到期
                    # 到期强平：持有天数达到上限（计划内平仓，用 exec_price 成交）。
                    # 不受 min_hold 约束（到期是上限，优先级低于止损/止盈）。
                    if (cfg.max_hold_days > 0
                            and hold_days >= cfg.max_hold_days):
                        _sell(lot, sym, jj, t, exec_np[t, jj], "max_hold")

        # 末日强平仍未平仓的 Lot（止损已平的不重复处理）
        if t == n - 1:
            for jj, sym in enumerate(symbols):
                for lot in list(book[sym]):
                    _sell(lot, sym, jj, t, close_np[t, jj], "end_of_data")

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
