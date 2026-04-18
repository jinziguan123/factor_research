"""把 QMT 读出来的分钟线 DataFrame 转成适合写入 ClickHouse 的行序列。

来源：从 timing_driven_backtest 的 ``mysql_bar_common.py`` 抽取并收窄到
本项目实际用到的两个函数：

- :func:`compress_amount_to_k` —— 千元换算；写入表的 ``amount_k`` 列；
- :func:`normalize_symbol_bar_frame` —— 对单只股票的分钟帧做字段校验 / 时间归一化
  / 只保留合法交易分钟槽位，返回可直接 ``executemany`` / CH ``insert`` 的 tuple 列表。

VALID_MINUTE_SLOTS 对应 A 股交易时段（分钟槽位 = 小时*60 + 分钟）：
    上午 9:31–11:30 → 571–690；下午 13:01–15:00 → 781–900。
这里把 9:30 开盘的那一分钟槽 570 排除掉（iQuant 的第一根 K 线对应 9:31），
沿袭 timing_driven 的口径——factor_research 已经在聚合、读取侧全链按这套槽位跑，
不能在导入侧放进 570 污染统计。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

# A 股合法交易分钟槽位：
# - 上午 9:31–11:30 → 571..690（含两端，共 120 根 K 线）
# - 下午 13:01–15:00 → 781..900（含两端，共 120 根 K 线）
VALID_MINUTE_SLOTS: frozenset[int] = frozenset(set(range(571, 691)) | set(range(781, 901)))


def compress_amount_to_k(amount: int | float) -> int:
    """成交额（元） → 千元。截断负数防御脏数据，四舍五入取整。"""
    return max(0, int(round(float(amount) / 1000.0)))


def _normalize_index(index: Iterable) -> pd.DatetimeIndex:
    dt_index = pd.DatetimeIndex(pd.to_datetime(index))
    if dt_index.tz is not None:
        dt_index = dt_index.tz_convert(None)
    # 切到分钟粒度：iQuant 数据本身就是分钟整点，这里只做防御。
    return dt_index.floor("min")


def normalize_symbol_bar_frame(
    symbol_id: int,
    frame: pd.DataFrame,
) -> list[tuple[date, int, int, float, float, float, float, int, int]]:
    """单股票分钟帧 → 9 元组列表 ``(trade_date, minute_slot, symbol_id, o, h, l, c, volume, amount_k)``。

    - 空帧 / 缺必需字段 → 抛 ``ValueError``；
    - 非法分钟槽（集合竞价外、午休）会被过滤；
    - 重复时间索引保留最后一条，与 mmap reader 的去重一致。
    """
    if frame.empty:
        return []

    required = ["open", "high", "low", "close", "volume", "amount"]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")

    working = frame.loc[:, required].copy()
    working.index = _normalize_index(working.index)
    working = working[~working.index.duplicated(keep="last")]

    working["trade_date"] = working.index.date
    working["minute_slot"] = working.index.hour * 60 + working.index.minute
    working = working[working["minute_slot"].isin(VALID_MINUTE_SLOTS)]

    if working.empty:
        return []

    volume = working["volume"].fillna(0).astype(int)
    amount_k = working["amount"].map(compress_amount_to_k).astype(int)

    return list(
        zip(
            working["trade_date"],
            working["minute_slot"].astype(int),
            [int(symbol_id)] * len(working),
            working["open"].astype(float),
            working["high"].astype(float),
            working["low"].astype(float),
            working["close"].astype(float),
            volume,
            amount_k,
        )
    )
