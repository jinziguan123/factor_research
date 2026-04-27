"""A 股交易日历查询 + 盘中阶段判定（纯函数）。

设计要点：
- ``is_trading_day(date)`` 查 MySQL ``fr_trade_calendar``：
  - 周六 / 周日直接 False（无需查库）；
  - 工作日查表，**无记录 = False**（保守 fail-safe，避免节假日空跑触发限流）。
- ``determine_phase(now, today_is_trading_day)`` 纯函数：
  - 'idle'         非交易日 / 开盘前 / 午休 / 收盘归档后；
  - 'spot'         9:25-11:30 + 13:00-15:00；
  - 'eod_archive'  15:00-15:30（仅当配置开启 1m K 归档时 worker 才会真的执行）。
- 让"是否交易日"和"是否在某时段"分开两个函数，便于单测分别覆盖。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Literal

from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

Phase = Literal["idle", "spot", "eod_archive"]

# A 股标准交易时段（含 9:25-9:30 集合竞价）
_SPOT_MORNING_START = time(9, 25)
_SPOT_MORNING_END = time(11, 30)
_SPOT_AFTERNOON_START = time(13, 0)
_SPOT_AFTERNOON_END = time(15, 0)
_EOD_ARCHIVE_END = time(15, 30)


def is_trading_day(d: date) -> bool:
    """A 股 d 是否为交易日。

    周末直接 False（节省一次 DB 查询）；工作日查 ``fr_trade_calendar``，
    无记录视为 False（fail-safe：日历未同步时 worker 不空跑）。
    """
    # weekday(): Monday=0 ... Sunday=6
    if d.weekday() >= 5:
        return False
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT is_open FROM fr_trade_calendar "
                "WHERE market='CN' AND trade_date=%s",
                (d,),
            )
            row = cur.fetchone()
    if row is None:
        log.warning(
            "fr_trade_calendar 缺 %s 的记录；保守视为非交易日，"
            "请先同步交易日历（admin /api/admin/sync-calendar）",
            d,
        )
        return False
    return int(row["is_open"]) == 1


def determine_phase(now: datetime, today_is_trading_day: bool) -> Phase:
    """根据当前时刻 + 是否交易日判定 phase（纯函数）。

    Args:
        now: 当前时刻（精确到秒）。
        today_is_trading_day: 当日是否为 A 股交易日（由 ``is_trading_day`` 算好传入）。

    Returns:
        - ``'idle'``         非交易日 / 9:25 前 / 11:30~13:00 午休 / 15:30 后
        - ``'spot'``         9:25-11:30 + 13:00-15:00
        - ``'eod_archive'``  15:00-15:30（worker 内再判 archive_1m.enabled，
          若关闭则等价于 idle）
    """
    if not today_is_trading_day:
        return "idle"
    t = now.time()
    if _SPOT_MORNING_START <= t <= _SPOT_MORNING_END:
        return "spot"
    if _SPOT_AFTERNOON_START <= t <= _SPOT_AFTERNOON_END:
        return "spot"
    if _SPOT_AFTERNOON_END < t <= _EOD_ARCHIVE_END:
        return "eod_archive"
    return "idle"
