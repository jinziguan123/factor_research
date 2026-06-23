"""执行层抽象：统一的 Broker 下单接口 + 订单/成交/持仓数据类。

让回测信号联调与实盘下单走同一套抽象——策略只依赖 ``Broker`` 接口，底层可替换为
``SimulatedBroker``（内存撮合，联调用）或实盘适配器（QMT / CTP，需外部 SDK + 账户）。

这是"信号 → 订单"最后一公里的接口定义；本文件只有契约，无外部依赖。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class Order:
    """一笔委托。``filled_qty`` < ``qty`` 表示部分成交。"""

    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0


@dataclass
class Fill:
    """一笔成交回报。"""

    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    fee: float = 0.0


@dataclass
class Position:
    """持仓：数量 + 持仓均价。"""

    symbol: str
    qty: float
    avg_price: float = 0.0


@dataclass
class Account:
    """账户资金快照。``market_value`` 需结合实时行情估算，默认 0。"""

    cash: float
    market_value: float = 0.0

    @property
    def total(self) -> float:
        return self.cash + self.market_value


class Broker(ABC):
    """券商/撮合接口。所有实现（模拟盘、QMT、CTP）遵循同一契约。"""

    @abstractmethod
    def submit_order(
        self, symbol: str, side: OrderSide | str, qty: float, price: float
    ) -> Order:
        """提交委托，返回带成交状态的 ``Order``。"""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单，返回是否成功。"""

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """当前持仓 ``{symbol: Position}``。"""

    @abstractmethod
    def get_account(self) -> Account:
        """账户资金快照。"""

    @abstractmethod
    def get_fills(self) -> list[Fill]:
        """历史成交回报列表。"""
