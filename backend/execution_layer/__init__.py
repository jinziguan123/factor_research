"""执行层：统一下单抽象 + 内存模拟盘。

信号 → 订单的最后一公里。策略只依赖 ``Broker`` 接口；联调用 ``SimulatedBroker``。
实盘对接（QMT / CTP 等）可按 ``Broker`` 接口扩展实现，需外部券商 SDK + 账户——
本仓库不内置实盘适配器。
"""
from backend.execution_layer.base import (
    Account,
    Broker,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)
from backend.execution_layer.simulated import SimulatedBroker

__all__ = [
    "Account",
    "Broker",
    "Fill",
    "Order",
    "OrderSide",
    "OrderStatus",
    "Position",
    "SimulatedBroker",
]
