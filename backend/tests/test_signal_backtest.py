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
