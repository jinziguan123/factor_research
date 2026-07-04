"""信号回测引擎核心纯函数。

面向择时/事件型因子（因子值即买入信号），采用事件驱动、按笔(lot)管理的
回测方式，与现有 vectorbt 分位回测并存。本模块提供配置(SignalConfig)、
持仓笔(Lot)、结果(SignalResult)等数据结构，以及回测核心函数
simulate_signal_book（逻辑由后续任务逐步实现）。
"""
from __future__ import annotations
import json
import logging
import math
import traceback
from dataclasses import dataclass
import numpy as np
import pandas as pd

# abort_check 无循环依赖，可安全在模块顶层 import（backtest_service 走延迟 import）。
from backend.services.abort_check import AbortedError

log = logging.getLogger(__name__)


@dataclass
class SignalConfig:
    signal_threshold: float = 0.0        # 因子值 > 阈值算买入信号
    cash_per_lot: float = 1_000_000.0
    max_concurrent_lots: int = 10
    allow_pyramiding: bool = False
    max_adds_per_symbol: int = 0
    stop_loss_pct: float = 0.08          # 0=关闭；固定百分比止损距离
    take_profit_pct: float = 0.20        # 0=关闭
    stop_mode: str = "per_lot"           # per_lot | avg_cost
    min_hold_days: int = 0
    max_hold_days: int = 0               # 0=不限
    buy_fee_rate: float = 0.00026
    sell_fee_rate: float = 0.00076
    # —— 增强（正交模型）——
    # 止损"距离"来源：atr_stop_multiplier>0 时 = 倍数×ATR(atr_window)（随波动自适应），
    #   否则 = 成本价×stop_loss_pct（固定百分比）。
    # 跟踪止损 trailing_stop：开→止损位=持仓期最高价−距离（棘轮上移）；关→=成本价−距离。
    atr_stop_multiplier: float = 0.0     # 0=关闭 ATR 止损，用固定百分比
    atr_window: int = 14                 # ATR 窗口（交易日）
    trailing_stop: bool = False          # 跟踪止损开关
    # 条件加仓：>0 时仅当持仓浮盈≥该比例（相对均价）才允许加仓（顺势加盈利仓）。
    pyramid_min_profit_pct: float = 0.0


@dataclass
class Lot:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float                   # 成交价（不含费用）
    qty: float
    stop_distance: float | None          # 止损"距离"（绝对价格差）；None=无止损
    tp_price: float | None
    lot_id: int
    add_seq: int                         # 0=首仓
    peak: float = 0.0                    # 持仓期最高价（跟踪止损棘轮参照），init=成本价

    @property
    def sl_price(self) -> float | None:
        """固定止损位（参照成本价）= 成本价 − 距离。跟踪止损的动态止损位在
        出场判定处按 peak 现算，不走这里；本属性保留是为读性与非跟踪场景取值。"""
        if self.stop_distance is None:
            return None
        return self.entry_price - self.stop_distance


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
    # 估值/末日强平用前向填充的 close：持仓期若停牌（close 为 NaN），用最近一个
    # 有效收盘价计价，避免当日净值变 NaN 污染整条净值曲线与夏普/回撤。触发判定
    # （止损/止盈）仍用原始 close_np/low/high —— NaN 参与比较返回 False，停牌日
    # 天然不触发，语义正确。
    close_val_np = close.ffill().to_numpy(float)
    exec_np = exec_price.to_numpy(float)
    slip_np = slippage.to_numpy(float)

    # 涨跌停锁定掩码：None 视为全 False（无锁定）。行/列已与 close 对齐。
    lu_np = (limit_up_mask.to_numpy(bool) if limit_up_mask is not None
             else np.zeros_like(close_np, dtype=bool))
    ld_np = (limit_down_mask.to_numpy(bool) if limit_down_mask is not None
             else np.zeros_like(close_np, dtype=bool))

    # ATR 预计算（仅在 ATR 止损开启时）：TR=max(H-L,|H-Cprev|,|L-Cprev|)，
    # ATR=TR 的 atr_window 日简单滚动均值。窗口未就绪处为 NaN，建仓时回退固定百分比。
    atr_np = None
    if cfg.atr_stop_multiplier > 0:
        prev_close = np.vstack([np.full((1, close_np.shape[1]), np.nan),
                                close_np[:-1]])         # close 上移一行
        tr = np.maximum.reduce([
            high_np - low_np,
            np.abs(high_np - prev_close),
            np.abs(low_np - prev_close),
        ])
        atr_np = (pd.DataFrame(tr)
                  .rolling(cfg.atr_window, min_periods=cfg.atr_window)
                  .mean().to_numpy(float))

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
                # 条件加仓：仅当持仓浮盈≥阈值（用本次成交价 vs 现有持仓均价）才加，
                # 即"只加盈利仓/顺势加仓"。阈值=0 时不设限。
                if cfg.pyramid_min_profit_pct > 0 and np.isfinite(exec_np[t, j]):
                    lots0 = book[sym]
                    avg0 = (sum(l.entry_price * l.qty for l in lots0)
                            / sum(l.qty for l in lots0))
                    add_px = exec_np[t, j] * (1.0 + slip_np[t, j])
                    if add_px < avg0 * (1.0 + cfg.pyramid_min_profit_pct):
                        skipped.append({"date": dates[t], "symbol": sym,
                                        "reason": "pyramid_profit_gate"})
                        continue
            # 并发上限：当前总持仓笔数（含本日已建的首仓/加仓）达上限则跳过。
            # 循环内实时重算，保证同日多信号按列序建仓、超限者被拒。
            total_lots = sum(len(v) for v in book.values())
            if total_lots >= cfg.max_concurrent_lots:
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "max_concurrent"})
                continue
            # 停牌：成交价无效（NaN/非正）→ 无法成交，记 suspended。
            if not np.isfinite(exec_np[t, j]) or exec_np[t, j] <= 0:
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "suspended"})
                continue
            # 封涨停：买不进，记 limit_up。
            if lu_np[t, j]:
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "limit_up"})
                continue
            eff_buy = exec_np[t, j] * (1.0 + slip_np[t, j])
            qty = np.floor(cfg.cash_per_lot / eff_buy / 100.0) * 100.0
            cost = qty * eff_buy * (1.0 + cfg.buy_fee_rate)
            if qty <= 0 or cost > cash:
                # 数量为 0 或现金不足：记 skipped 便于复盘"没吃到"的信号
                skipped.append({"date": dates[t], "symbol": sym,
                                "reason": "insufficient_cash"})
                continue
            cash -= cost
            lot_counter += 1
            # 止损距离：ATR 止损开启且当日 ATR 就绪 → 倍数×ATR；否则回退固定百分比。
            stop_distance = None
            if (cfg.atr_stop_multiplier > 0 and atr_np is not None
                    and np.isfinite(atr_np[t, j]) and atr_np[t, j] > 0):
                stop_distance = cfg.atr_stop_multiplier * atr_np[t, j]
            elif cfg.stop_loss_pct > 0:
                stop_distance = eff_buy * cfg.stop_loss_pct
            tp_price = (eff_buy * (1.0 + cfg.take_profit_pct)
                        if cfg.take_profit_pct > 0 else None)
            lot = Lot(symbol=sym, entry_date=dates[t], entry_price=eff_buy,
                      qty=qty, stop_distance=stop_distance, tp_price=tp_price,
                      lot_id=lot_counter, add_seq=add_seq, peak=eff_buy)
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
                # 更新各笔 peak（合并仓 peak = 各笔 peak 的 max，即自最早建仓以来最高价）
                if np.isfinite(high_np[t, jj]):
                    for lot in lots:
                        lot.peak = max(lot.peak, high_np[t, jj])
                # 止损距离：ATR（当日就绪）优先，否则均价×固定百分比
                if (cfg.atr_stop_multiplier > 0 and atr_np is not None
                        and np.isfinite(atr_np[t, jj]) and atr_np[t, jj] > 0):
                    dist = cfg.atr_stop_multiplier * atr_np[t, jj]
                elif cfg.stop_loss_pct > 0:
                    dist = avg * cfg.stop_loss_pct
                else:
                    dist = None
                if dist is not None:
                    ref = (max(lot.peak for lot in lots) if cfg.trailing_stop
                           else avg)
                    sl = ref - dist
                else:
                    sl = None
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
                # 到期：停牌（exec 无效）则不以 NaN 成交，顺延到下一交易日
                elif (cfg.max_hold_days > 0 and hold_days >= cfg.max_hold_days
                        and np.isfinite(exec_np[t, jj])):
                    fill = exec_np[t, jj]
                    reason = "max_hold"
                # 封跌停：卖不出，本日不清仓，顺延（下一交易日仍触发再卖）
                if reason is not None and ld_np[t, jj]:
                    continue
                if reason is not None:
                    # 整只清仓：_sell 改 book[sym]，用快照遍历
                    for lot in list(lots):
                        _sell(lot, sym, jj, t, fill, reason)
        else:
            for jj, sym in enumerate(symbols):
                if not book[sym]:
                    continue
                # 复制一份遍历，_sell 会从 book[sym] 移除，避免边遍历边删
                # 封跌停：本日整只卖不出，跳过所有出场判定，顺延到下一交易日
                if ld_np[t, jj]:
                    continue
                for lot in list(book[sym]):
                    # T+1 守卫：建仓当日不可卖，只对 t>建仓行号 的 Lot 判出场
                    if t <= entry_index[lot.lot_id]:
                        continue
                    # 更新持仓期最高价（跟踪止损棘轮参照），用当日 high（与盘中触发口径一致）
                    if np.isfinite(high_np[t, jj]):
                        lot.peak = max(lot.peak, high_np[t, jj])
                    # 止损：有效止损位 = 参照点 − 距离。跟踪→参照持仓期最高价（棘轮上移）；
                    # 固定→参照成本价。当日 low 触及即触发；跳空穿越（open<=sl）以 open 成交。
                    # 止损优先级最高，不受 min_hold 约束（风控优先）。
                    if lot.stop_distance is not None:
                        ref = lot.peak if cfg.trailing_stop else lot.entry_price
                        eff_sl = ref - lot.stop_distance
                        if low_np[t, jj] <= eff_sl:
                            fill = (open_np[t, jj] if open_np[t, jj] <= eff_sl
                                    else eff_sl)
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
                    # 停牌（exec 无效）则不以 NaN 成交，顺延到下一交易日
                    if (cfg.max_hold_days > 0
                            and hold_days >= cfg.max_hold_days
                            and np.isfinite(exec_np[t, jj])):
                        _sell(lot, sym, jj, t, exec_np[t, jj], "max_hold")

        # 末日强平仍未平仓的 Lot（止损已平的不重复处理）。
        # 用前向填充 close：末日恰好停牌时以最近有效价平仓，避免 NaN 成交价。
        if t == n - 1:
            for jj, sym in enumerate(symbols):
                for lot in list(book[sym]):
                    _sell(lot, sym, jj, t, close_val_np[t, jj], "end_of_data")

        # 3) 产生今日信号的 pending（下一日消化）
        if t + 1 < n:
            for j in range(len(symbols)):
                if sig_np[t, j] > cfg.signal_threshold:
                    pending_entries.append(j)

        # 4) 估值：现金 + 所有持仓按当日 close 计价（停牌日用前向填充价，见上）
        holdings_val = 0.0
        for j, sym in enumerate(symbols):
            for lot in book[sym]:
                holdings_val += lot.qty * close_val_np[t, j]
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


def summarize(res: SignalResult) -> dict:
    """把 SignalResult 汇总成一组标量指标，供 Phase 2 落到 metrics payload。

    产出：胜率、盈亏比、平均持有天数、出场原因分布、总笔数、跳过数。
    所有数值均转成 Python float/int，避免 numpy 类型影响后续 json 序列化。
    """
    trades = res.trades
    total = int(len(trades))
    skipped_count = int(len(res.skipped))

    if total == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_hold_days": 0.0,
            "exit_reason_dist": {},
            "skipped_count": skipped_count,
        }

    pnl = trades["pnl"].to_numpy(float)
    wins = pnl > 0
    win_rate = float(wins.sum()) / total

    gross_profit = float(pnl[wins].sum())
    gross_loss = float(-pnl[pnl < 0].sum())   # 亏损绝对值之和（>=0）
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        # 无亏损：有盈利则视为无穷大盈亏比，无盈利（全为 0）则 0.0
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    avg_hold_days = float(trades["hold_days"].mean())

    exit_reason_dist = {
        str(k): int(v)
        for k, v in trades["exit_reason"].value_counts().to_dict().items()
    }

    return {
        "total_trades": total,
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "avg_hold_days": avg_hold_days,
        "exit_reason_dist": exit_reason_dist,
        "skipped_count": skipped_count,
    }


# ---------------------------- 编排层：接入系统回测流程 ----------------------------
#
# 以下把纯引擎（simulate_signal_book + summarize）接到系统回测的编排层：
# 状态机（running/success/aborted/failed）、价格成本准备（复用分位路径的
# _prepare_price_cost）、parquet 产物落盘、指标计算、写 fr_backtest_metrics /
# fr_backtest_artifacts。产物结构（equity/orders/trades 三个 parquet）与分位
# 回测对齐，便于前端详情页复用。
#
# 循环 import 说明：backtest_service 目前不 import 本模块，但 Task 16 会让
# run_backtest 按 body["mode"] 分发到这里（大概率用延迟 import）。为稳妥，本层
# 对 backtest_service 一律采用「函数内延迟 import」——既避免潜在的循环 import，
# 又能让测试对 bs.<name> 的 monkeypatch 生效（引擎通过 bs 模块命名空间引用它们）。


def _signal_config_from_body(body: dict, bundle) -> SignalConfig:
    """从 body 的信号配置字段 + bundle 的成本标量构造 SignalConfig。

    费率换算（bps → 小数）：
    - 买入 = 佣金 + 过户费；
    - 卖出 = 佣金 + 过户费 + 印花税（印花税仅卖出单边征收）。
    """
    buy_fee = (bundle.commission_bps + bundle.transfer_fee_bps) / 1e4
    sell_fee = (
        bundle.commission_bps + bundle.transfer_fee_bps + bundle.stamp_tax_bps
    ) / 1e4
    return SignalConfig(
        signal_threshold=float(body.get("signal_threshold", 0.0)),
        cash_per_lot=float(body.get("cash_per_lot", 1_000_000.0)),
        max_concurrent_lots=int(body.get("max_concurrent_lots", 10)),
        allow_pyramiding=bool(body.get("allow_pyramiding", False)),
        max_adds_per_symbol=int(body.get("max_adds_per_symbol", 0)),
        stop_loss_pct=float(body.get("stop_loss_pct", 0.08)),
        take_profit_pct=float(body.get("take_profit_pct", 0.20)),
        stop_mode=str(body.get("stop_mode", "per_lot")),
        min_hold_days=int(body.get("min_hold_days", 0)),
        max_hold_days=int(body.get("max_hold_days", 0)),
        atr_stop_multiplier=float(body.get("atr_stop_multiplier", 0.0)),
        atr_window=int(body.get("atr_window", 14)),
        trailing_stop=bool(body.get("trailing_stop", False)),
        pyramid_min_profit_pct=float(body.get("pyramid_min_profit_pct", 0.0)),
        buy_fee_rate=buy_fee,
        sell_fee_rate=sell_fee,
    )


def _build_signal_metrics_payload(
    res: SignalResult, summary: dict, init_cash: float,
    close: pd.DataFrame | None,
) -> dict:
    """把引擎结果 + summarize 汇总 + 收益指标组装成可 JSON 序列化的 payload。

    纯函数（无 DB / 无副作用），单独抽出便于无 DB 单测其结构与 json 安全性。

    关键：summarize 的 ``profit_factor`` 在「有盈利无亏损」时为 ``inf``，
    ``json.dumps(..., allow_nan=False)`` 遇到 inf/nan 会抛 ValueError，故落库前
    统一用 ``_nan_to_none`` 把所有非有限浮点兜底成 None。

    Args:
        res: 引擎结果（含 equity / trades）。
        summary: ``summarize(res)`` 的输出。
        init_cash: 初始资金，用于收益率兜底语义。
        close: qfq close 宽表，用于基准对比；None 时跳过基准。

    Returns:
        可被 ``json.dumps(payload, ensure_ascii=False, allow_nan=False)`` 序列化的 dict。
    """
    from backend.services import backtest_service as bs

    equity = res.equity
    # 收益指标：从净值序列直接算，与分位路径口径一致。
    if len(equity) >= 2 and equity.iloc[0] not in (0.0, None) \
            and math.isfinite(float(equity.iloc[0])):
        total_return = float(equity.iloc[-1]) / float(equity.iloc[0]) - 1.0
    else:
        total_return = 0.0
    # 最大回撤：净值相对历史峰值的最深跌幅（<=0）。
    if len(equity) >= 1:
        max_drawdown = float((equity / equity.cummax() - 1.0).min())
    else:
        max_drawdown = 0.0
    # 夏普：日收益 mean/std × sqrt(252)，std=0（净值恒定）兜底 0。
    daily_ret = equity.pct_change(fill_method=None).dropna()
    if len(daily_ret) >= 2:
        std = float(daily_ret.std(ddof=1))
        sharpe = (float(daily_ret.mean()) / std * math.sqrt(252)) \
            if std > 1e-12 else 0.0
    else:
        sharpe = 0.0
    # 年化：净值序列长度归一化，与 run_backtest 同款兜底（亏光→-100%）。
    n_bars = len(equity)
    years = max(n_bars / 252.0, 1e-9)
    base = 1.0 + total_return
    if n_bars == 0:
        annual_return = 0.0
    elif base <= 0:
        annual_return = -1.0
    else:
        annual_return = base ** (1.0 / years) - 1.0

    win_rate = float(summary.get("win_rate", 0.0))
    trade_count = int(summary.get("total_trades", 0))

    # payload：summarize 全部指标 + 上述收益指标。所有值经 _nan_to_none 兜底，
    # 确保 inf/nan（尤其 profit_factor=inf）转 None，json allow_nan=False 不抛。
    payload: dict = {}
    for k, v in summary.items():
        payload[k] = bs._nan_to_none(v)
    payload.update({
        "total_return": bs._nan_to_none(total_return),
        "annual_return": bs._nan_to_none(annual_return),
        "sharpe": bs._nan_to_none(sharpe),
        "max_drawdown": bs._nan_to_none(max_drawdown),
        "win_rate": bs._nan_to_none(win_rate),
        "trade_count": trade_count,
    })
    # 基准对比：等权市场组合（签名与分位路径一致）。close 缺省则跳过。
    if close is not None and not close.empty:
        payload["benchmark"] = bs._benchmark_metrics(equity, close)
    return payload


def run_signal_backtest(run_id: str, body: dict) -> None:
    """执行一次信号（择时/事件型）回测，把纯引擎接到系统回测流程。

    编排：状态机 → _prepare_price_cost → 构造 SignalConfig → 固定滑点面板 →
    simulate_signal_book + summarize → 落 equity/orders/trades parquet →
    组装 metrics payload → 写 fr_backtest_metrics + 3 条 fr_backtest_artifacts。
    结构完全仿 run_backtest，产物对齐分位回测，前端详情页可复用。

    Args:
        run_id: ``fr_backtest_runs.run_id``，由 API 层生成并传入。
        body: 请求体 dict。除因子/股票池/窗口/成本字段外，信号配置字段：
            ``signal_threshold / cash_per_lot / stop_loss_pct / take_profit_pct /
            stop_mode / min_hold_days / max_hold_days / allow_pyramiding /
            max_adds_per_symbol / max_concurrent_lots``。

    副作用：更新 run 状态；成功写 metrics 一条 + artifacts 三条 +
    ``<ARTIFACT_DIR>/<run_id>/{equity,orders,trades}.parquet``。
    """
    # 延迟 import：见模块内「循环 import 说明」。通过 bs.<name> 引用，
    # 让测试对 bs 命名空间的 monkeypatch 生效。
    from backend.services import backtest_service as bs

    try:
        bs._update_status(run_id, status="running", started=True, progress=5)
        bs.check_abort("backtest", run_id)

        # 1) 价格 / 成本面板（复用分位路径的公共 prepare）
        bundle = bs._prepare_price_cost(body)
        init_cash = float(bundle.init_cash)

        bs._update_status(run_id, progress=40)
        bs.check_abort("backtest", run_id)

        # 2) 信号配置 + 面板。阈值比较在引擎里做（signal > threshold），
        # 故直接把因子宽表当 signal 传入。
        cfg = _signal_config_from_body(body, bundle)
        signal = bundle.factor
        close = bundle.close

        # 3) 固定滑点面板：值 = slippage_bps/1e4，与 close 同 shape。
        # 简化说明：信号路径不建模平方根市场冲击——冲击依赖动态订单规模，
        # 本轮信号模式只用固定滑点（分位路径才用容量相关的动态冲击）。
        slip_df = pd.DataFrame(
            bundle.slippage_bps / 1e4, index=close.index, columns=close.columns
        )

        # 4) 引擎 + 汇总
        res = simulate_signal_book(
            signal, bundle.open_, bundle.high, bundle.low, close,
            bundle.exec_price, slip_df, bundle.limit_up_mask,
            bundle.limit_down_mask, init_cash, cfg,
        )
        summary = summarize(res)

        bs._update_status(run_id, progress=85)
        bs.check_abort("backtest", run_id)  # 落盘前最后一次检查

        # 5) 产物落盘：equity/orders/trades 三个 parquet（结构对齐分位回测）
        run_dir = bs.ARTIFACT_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        equity_df = res.equity.to_frame(name="equity")
        equity_df.index.name = "trade_date"
        equity_path = run_dir / "equity.parquet"
        equity_df.to_parquet(equity_path)

        orders_path = run_dir / "orders.parquet"
        res.orders.to_parquet(orders_path)

        trades_path = run_dir / "trades.parquet"
        res.trades.to_parquet(trades_path)

        # 6) 指标 + JSON payload（profit_factor=inf 已在 helper 里兜底成 None）
        payload = _build_signal_metrics_payload(res, summary, init_cash, close)

        total_return = payload.get("total_return")
        annual_return = payload.get("annual_return")
        sharpe_ratio = payload.get("sharpe")
        max_drawdown = payload.get("max_drawdown")
        win_rate = payload.get("win_rate")
        trade_count = int(payload.get("trade_count", 0) or 0)

        with bs.mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    REPLACE INTO fr_backtest_metrics
                    (run_id, total_return, annual_return, sharpe_ratio,
                     max_drawdown, win_rate, trade_count, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        total_return if total_return is not None else 0.0,
                        annual_return if annual_return is not None else 0.0,
                        sharpe_ratio if sharpe_ratio is not None else 0.0,
                        max_drawdown if max_drawdown is not None else 0.0,
                        win_rate if win_rate is not None else 0.0,
                        trade_count,
                        json.dumps(
                            payload, ensure_ascii=False, allow_nan=False
                        ),
                    ),
                )
                for artifact_type, path in (
                    ("equity", equity_path),
                    ("orders", orders_path),
                    ("trades", trades_path),
                ):
                    cur.execute(
                        """
                        REPLACE INTO fr_backtest_artifacts
                        (run_id, artifact_type, artifact_path)
                        VALUES (%s, %s, %s)
                        """,
                        (run_id, artifact_type, str(path)),
                    )
            c.commit()

        bs._update_status(
            run_id, status="success", progress=100, finished=True
        )
    except AbortedError as exc:
        log.info("signal backtest aborted: run_id=%s reason=%s", run_id, exc)
        try:
            bs._update_status(run_id, status="aborted", finished=True)
        except Exception:
            log.exception("_update_status 落 aborted 失败: run_id=%s", run_id)
    except Exception:
        log.exception("signal backtest failed: run_id=%s", run_id)
        try:
            bs._update_status(
                run_id, status="failed",
                error=traceback.format_exc()[:4000], finished=True,
            )
        except Exception:
            log.exception(
                "_update_status 记录失败时自身也抛异常: run_id=%s", run_id
            )
