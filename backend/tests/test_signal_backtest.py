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


def test_fees_and_slippage_asymmetric():
    dates = pd.date_range("2026-01-05", periods=4, freq="B")
    close = pd.DataFrame({"A": [10, 10, 10, 10]}, index=dates)
    open_ = close.copy(); high = close.copy(); low = close.copy()
    signal = pd.DataFrame({"A": [1, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.01, index=dates, columns=["A"])   # 1% 滑点
    cfg = sbt.SignalConfig(cash_per_lot=100000, stop_loss_pct=0.0,
                           take_profit_pct=0.0, buy_fee_rate=0.001,
                           sell_fee_rate=0.002)
    # init_cash 留手续费余量（qty 按 cash_per_lot/eff_buy 定，含买费后 cost 略超 cash_per_lot）
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 200000.0, cfg)
    buy = res.orders[res.orders["side"]=="buy"].iloc[0]
    # 买入有效价 = 10*(1+0.01)=10.1；qty=floor(100000/10.1/100)*100
    assert buy["price"] == pytest.approx(10.1)
    expected_qty = (100000 // (10.1) // 100) * 100
    assert buy["qty"] == expected_qty
    sell = res.orders[res.orders["side"]=="sell"].iloc[0]
    assert sell["price"] == pytest.approx(10 * (1 - 0.01))   # 卖出有效价含反向滑点


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


def test_summary_metrics():
    # 构造 1 胜 1 负两笔：A 止盈、B 止损
    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    close = pd.DataFrame({"A": [10, 10, 13, 13, 13, 13],
                          "B": [10, 10, 9, 9, 9, 9]}, index=dates)
    open_ = close.copy()
    high = pd.DataFrame({"A": [10, 10, 13, 13, 13, 13],
                         "B": [10, 10, 9, 9, 9, 9]}, index=dates)
    low = pd.DataFrame({"A": [10, 10, 13, 13, 13, 13],
                        "B": [10, 10, 8, 9, 9, 9]}, index=dates)
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0],
                           "B": [1, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A", "B"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.08,
                           take_profit_pct=0.20, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 2000.0, cfg)
    m = sbt.summarize(res)
    assert 0.0 <= m["win_rate"] <= 1.0
    assert "profit_factor" in m
    assert "avg_hold_days" in m
    assert "exit_reason_dist" in m
    assert m["total_trades"] == len(res.trades)


def test_no_lookahead_truncation():
    dates = pd.date_range("2026-01-05", periods=30, freq="B")
    rng = np.random.default_rng(3)
    px = 10 + np.cumsum(rng.normal(0, 0.2, 30))
    close = pd.DataFrame({"A": px}, index=dates)
    open_ = close.copy()
    high = close * 1.02
    low = close * 0.98
    signal = pd.DataFrame({"A": (px < np.roll(px, 1)).astype(float)}, index=dates)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.05,
                           take_profit_pct=0.10, buy_fee_rate=0.0, sell_fee_rate=0.0)
    def run(upto):
        sl = slice(None, upto)
        return sbt.simulate_signal_book(signal.loc[sl], open_.loc[sl], high.loc[sl],
            low.loc[sl], close.loc[sl], open_.loc[sl], slip.loc[sl], None, None,
            1000.0, cfg)
    full = run(dates[-1]); trunc = run(dates[20])
    # 截断点前已平仓（非 end_of_data）的 trade 应与全量完全一致
    a = full.trades[(full.trades["exit_date"] <= dates[20]) &
                    (full.trades["exit_reason"] != "end_of_data")].reset_index(drop=True)
    b = trunc.trades[trunc.trades["exit_reason"] != "end_of_data"].reset_index(drop=True)
    assert len(a) == len(b)
    for col in ["symbol", "entry_date", "exit_date", "exit_price", "exit_reason"]:
        assert list(a[col]) == list(b[col])


def test_suspension_during_hold_valuation_not_nan():
    # 持仓期停牌（dates[2] close 为 NaN）：当日净值应以最近有效价计价，不能是 NaN
    dates = pd.date_range("2026-01-05", periods=6, freq="B")
    close = pd.DataFrame({"A": [10, 10, np.nan, 11, 11, 11]}, index=dates)
    open_ = close.copy(); high = close.copy(); low = close.copy()
    signal = pd.DataFrame({"A": [1, 0, 0, 0, 0, 0]}, index=dates).astype(float)
    slip = pd.DataFrame(0.0, index=dates, columns=["A"])
    cfg = sbt.SignalConfig(cash_per_lot=1000, stop_loss_pct=0.0,
                           take_profit_pct=0.0, buy_fee_rate=0.0, sell_fee_rate=0.0)
    res = sbt.simulate_signal_book(signal, open_, high, low, close, open_,
                                   slip, None, None, 1000.0, cfg)
    assert res.equity.notna().all()                      # 整条净值无 NaN
    assert res.equity.iloc[2] == pytest.approx(1000.0)   # 停牌日用停牌前 close=10 计价
