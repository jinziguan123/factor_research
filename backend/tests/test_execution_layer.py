"""执行层模拟盘单测 + 实盘骨架占位校验。

    uv run pytest backend/tests/test_execution_layer.py -v
"""
from __future__ import annotations

import pytest

from backend.execution_layer import OrderSide, OrderStatus, SimulatedBroker


def test_buy_deducts_cash_adds_position():
    b = SimulatedBroker(init_cash=1_000_000, commission_bps=2.5, transfer_fee_bps=0.1)
    o = b.submit_order("600000.SH", OrderSide.BUY, 1000, 10.0)
    assert o.status == OrderStatus.FILLED and o.filled_qty == 1000
    # 成交额 10000，买入费 = 10000 * (2.5+0.1)/1e4 = 2.6
    assert b.get_account().cash == pytest.approx(1_000_000 - 10000 - 2.6)
    assert b.get_positions()["600000.SH"].qty == 1000


def test_sell_adds_cash_with_stamp_tax():
    b = SimulatedBroker(1_000_000)
    b.submit_order("600000.SH", OrderSide.BUY, 1000, 10.0)
    cash_after_buy = b.get_account().cash
    b.submit_order("600000.SH", OrderSide.SELL, 1000, 10.0)
    # 卖出额 10000，卖出费 = 10000 * (2.5+5+0.1)/1e4 = 7.6
    assert b.get_account().cash == pytest.approx(cash_after_buy + 10000 - 7.6)
    assert "600000.SH" not in b.get_positions()


def test_sell_fee_higher_than_buy_fee():
    b = SimulatedBroker(1_000_000)
    b.submit_order("x", OrderSide.BUY, 1000, 10.0)
    b.submit_order("x", OrderSide.SELL, 1000, 10.0)
    fills = b.get_fills()
    assert fills[1].fee > fills[0].fee  # 卖出含印花税，更贵


def test_cannot_sell_more_than_held():
    b = SimulatedBroker(1_000_000)
    b.submit_order("x", OrderSide.BUY, 500, 10.0)
    o = b.submit_order("x", OrderSide.SELL, 1000, 10.0)  # 想卖 1000，只有 500
    assert o.filled_qty == 500
    assert "x" not in b.get_positions()


def test_insufficient_cash_partial_fill():
    b = SimulatedBroker(init_cash=10_000, lot_size=100)
    o = b.submit_order("x", OrderSide.BUY, 10_000, 10.0)  # 想买 10 万元，只有 1 万
    assert o.status == OrderStatus.PARTIAL
    assert 0 < o.filled_qty <= 1000
    assert b.get_account().cash >= 0  # 不会透支


def test_insufficient_cash_reject_when_no_partial():
    b = SimulatedBroker(init_cash=100, allow_partial=False)
    o = b.submit_order("x", OrderSide.BUY, 1000, 10.0)
    assert o.status == OrderStatus.REJECTED
    assert b.get_account().cash == 100  # 资金未动


def test_reject_nonpositive_order():
    b = SimulatedBroker(1000)
    assert b.submit_order("x", OrderSide.BUY, 0, 10).status == OrderStatus.REJECTED
    assert b.submit_order("x", OrderSide.BUY, 100, 0).status == OrderStatus.REJECTED


def test_average_price_tracking():
    b = SimulatedBroker(1_000_000)
    b.submit_order("x", OrderSide.BUY, 100, 10.0)
    b.submit_order("x", OrderSide.BUY, 100, 20.0)
    # 均价 = (100*10 + 100*20) / 200 = 15
    assert b.get_positions()["x"].avg_price == pytest.approx(15.0)
