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
    """事件驱动、按笔管理的信号回测核心（后续任务逐步实现）。"""
    raise NotImplementedError
