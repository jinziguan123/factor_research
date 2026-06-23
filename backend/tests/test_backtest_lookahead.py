"""回测执行语义的集成测试：直接驱动 VectorBT 验证改造后的三条核心假设 +
T+1 平移端到端消除前视。

不依赖 DataService / DB——手工构造 close/size/price/fees/slippage 喂 vbt，
验证我们对 ``Portfolio.from_orders`` 行为的假设是否成立（这是整个改造正确性的地基）。

    uv run pytest backend/tests/test_backtest_lookahead.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

vbt = pytest.importorskip("vectorbt")

from backend.services import execution as ex  # noqa: E402


def _run(close, size, price, fees, slippage, init_cash=1e6):
    return vbt.Portfolio.from_orders(
        close=close,
        size=size,
        price=price,
        size_type="targetamount",
        fees=fees,
        slippage=slippage,
        freq="1D",
        init_cash=init_cash,
        cash_sharing=True,
        group_by=True,
    )


def _idx(n):
    return pd.to_datetime(
        ["2024-01-%02d" % d for d in range(2, 2 + n)]
    )


def test_price_param_executes_at_exec_price_not_close():
    """price=exec_price → 成交价取 exec_price，而非 close（T+1 成交的基础）。"""
    idx = _idx(2)
    close = pd.DataFrame({"A": [10.0, 20.0]}, index=idx)
    price = pd.DataFrame({"A": [10.0, 100.0]}, index=idx)  # T1 成交价=100
    size = pd.DataFrame({"A": [0.0, 10.0]}, index=idx)     # T1 目标 10 股
    fees = np.zeros((2, 1))
    slip = np.zeros((2, 1))
    rec = _run(close, size, price, fees, slip).orders.records
    assert len(rec) == 1
    assert float(rec["price"][0]) == pytest.approx(100.0)  # 用 price 而非 close=20


def test_directional_fees_array_sell_costs_more():
    """方向相关 fees 数组：卖出行扣费率更高（印花税效应）。"""
    idx = _idx(3)
    close = pd.DataFrame({"A": [10.0, 10.0, 10.0]}, index=idx)
    price = close.copy()
    size = pd.DataFrame({"A": [0.0, 100.0, 0.0]}, index=idx)  # T1 买100, T2 卖100
    buy_fee, sell_fee = 0.001, 0.005
    fees = np.array([[buy_fee], [buy_fee], [sell_fee]])
    slip = np.zeros((3, 1))
    rec = _run(close, size, price, fees, slip).orders.records
    paid = {int(i): float(f) for i, f in zip(rec["idx"], rec["fees"])}
    # 成交额都 = 100 * 10 = 1000；买扣 1.0，卖扣 5.0
    assert paid[1] == pytest.approx(1000 * buy_fee)
    assert paid[2] == pytest.approx(1000 * sell_fee)


def test_slippage_array_raises_buy_price():
    """slippage 数组按方向施加：买入成交价上浮 price*(1+slip)。"""
    idx = _idx(2)
    close = pd.DataFrame({"A": [10.0, 10.0]}, index=idx)
    price = close.copy()
    size = pd.DataFrame({"A": [0.0, 10.0]}, index=idx)
    fees = np.zeros((2, 1))
    slip = np.array([[0.0], [0.01]])  # T1 滑点 1%
    rec = _run(close, size, price, fees, slip).orders.records
    assert float(rec["price"][0]) == pytest.approx(10.1)  # 10 * 1.01


def test_t1_shift_executes_next_day_not_signal_day():
    """端到端：shift_for_t1 让 T 日信号在 T+1 成交，建仓不发生在信号日。"""
    idx = _idx(3)
    # 因子在 T0 选中 A（权重 1），持有到 T1，T2 清仓。
    W = pd.DataFrame({"A": [1.0, 1.0, 0.0]}, index=idx)
    w_exec = ex.shift_for_t1(W)  # [0, 1, 1] → T1 才建仓
    open_ = pd.DataFrame({"A": [10.0, 5.0, 5.0]}, index=idx)  # T1 开盘暴跌
    close = open_.copy()
    init_cash = 1e6
    exec_price = ex.build_exec_price(open_, close, close, close, "open")
    size = (w_exec * init_cash / exec_price).fillna(0.0)
    amount = pd.DataFrame({"A": [1e12, 1e12, 1e12]}, index=idx)
    fees = ex.build_fee_array(w_exec, 0.0, 0.0, 0.0)
    slip = ex.build_slippage_array(w_exec, init_cash, amount, exec_price, 0.0, 0.0)
    rec = _run(close, size, exec_price, fees, slip, init_cash).orders.records
    buys = rec[rec["side"] == 0]
    assert len(buys) >= 1
    # 第一笔买入发生在 idx=1（T1），证明信号日 T0 不成交 → 无前视。
    assert int(buys["idx"][0]) == 1
