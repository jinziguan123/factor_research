"""内存撮合的模拟盘 Broker：即时按下单价成交，带 A 股不对称费用与资金/持仓约束。

用于策略联调与执行链路测试，行为与回测的成本口径一致（佣金双边 + 印花税仅卖出 +
过户费双边）。无外部依赖、可单测。撮合假设：下单即以给定价全额/部分成交（不模拟
排队、不模拟盘口深度——那是更高保真的实盘适配器的职责）。
"""
from __future__ import annotations

import math

from backend.execution_layer.base import (
    Account,
    Broker,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)


class SimulatedBroker(Broker):
    """内存模拟盘。

    Args:
        init_cash: 初始资金（元）。
        commission_bps / stamp_tax_bps / transfer_fee_bps: A 股费率（bp），
            印花税仅卖出，与回测 execution 口径一致。
        lot_size: 最小交易单位（A 股 100 股/手），买入按手取整。
        allow_partial: 资金不足时是否部分成交（False 则整单拒绝）。
    """

    def __init__(
        self,
        init_cash: float,
        commission_bps: float = 2.5,
        stamp_tax_bps: float = 5.0,
        transfer_fee_bps: float = 0.1,
        lot_size: int = 100,
        allow_partial: bool = True,
    ) -> None:
        self.cash = float(init_cash)
        self.commission_bps = commission_bps
        self.stamp_tax_bps = stamp_tax_bps
        self.transfer_fee_bps = transfer_fee_bps
        self.lot_size = lot_size
        self.allow_partial = allow_partial
        self._pos: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._orders: list[Order] = []
        self._oid = 0

    def _next_id(self) -> str:
        self._oid += 1
        return f"sim-{self._oid}"

    def _fee(self, side: OrderSide, amount: float) -> float:
        bps = self.commission_bps + self.transfer_fee_bps
        if side == OrderSide.SELL:
            bps += self.stamp_tax_bps
        return amount * bps / 1e4

    def _reject(self, oid, symbol, side, qty, price) -> Order:
        o = Order(oid, symbol, side, qty, price, OrderStatus.REJECTED, 0.0)
        self._orders.append(o)
        return o

    def submit_order(
        self, symbol: str, side: OrderSide | str, qty: float, price: float
    ) -> Order:
        side = OrderSide(side)
        oid = self._next_id()
        qty = float(qty)
        price = float(price)
        if qty <= 0 or price <= 0:
            return self._reject(oid, symbol, side, qty, price)

        if side == OrderSide.BUY:
            buy_bps = (self.commission_bps + self.transfer_fee_bps) / 1e4
            affordable = self.cash / (price * (1.0 + buy_bps))
            fill_qty = qty
            if fill_qty > affordable:
                if not self.allow_partial:
                    return self._reject(oid, symbol, side, qty, price)
                fill_qty = math.floor(affordable / self.lot_size) * self.lot_size
            if fill_qty <= 0:
                return self._reject(oid, symbol, side, qty, price)
            amount = fill_qty * price
            fee = self._fee(side, amount)
            self.cash -= amount + fee
            pos = self._pos.get(symbol, Position(symbol, 0.0, 0.0))
            new_qty = pos.qty + fill_qty
            pos.avg_price = (
                (pos.avg_price * pos.qty + amount) / new_qty if new_qty > 0 else 0.0
            )
            pos.qty = new_qty
            self._pos[symbol] = pos
        else:  # SELL
            held = self._pos.get(symbol, Position(symbol, 0.0)).qty
            fill_qty = min(qty, held)  # 不能卖超过持仓
            if fill_qty <= 0:
                return self._reject(oid, symbol, side, qty, price)
            amount = fill_qty * price
            fee = self._fee(side, amount)
            self.cash += amount - fee
            pos = self._pos[symbol]
            pos.qty = held - fill_qty
            if pos.qty <= 1e-9:
                del self._pos[symbol]
            else:
                self._pos[symbol] = pos

        status = OrderStatus.FILLED if fill_qty == qty else OrderStatus.PARTIAL
        self._fills.append(Fill(oid, symbol, side, fill_qty, price, fee))
        o = Order(oid, symbol, side, qty, price, status, fill_qty)
        self._orders.append(o)
        return o

    def cancel_order(self, order_id: str) -> bool:
        # 模拟盘即时成交，没有可撤的挂单。
        return False

    def get_positions(self) -> dict[str, Position]:
        return {s: Position(p.symbol, p.qty, p.avg_price) for s, p in self._pos.items()}

    def get_account(self) -> Account:
        return Account(cash=self.cash)

    def get_fills(self) -> list[Fill]:
        return list(self._fills)
