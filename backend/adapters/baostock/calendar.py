"""从 Baostock 同步 A 股交易日历到 ``fr_trade_calendar``。

接口：``bs.query_trade_dates(start_date, end_date)``
返回字段：``calendar_date`` / ``is_trading_day``（``"1"`` / ``"0"``）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable

from backend.adapters.baostock.client import check_rs
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def fetch_calendar(start: date, end: date) -> Iterable[dict]:
    """拉 [start, end] 区间的日历行，yield ``{"trade_date", "is_open"}``。"""
    import baostock as bs  # noqa: PLC0415

    rs = bs.query_trade_dates(
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )
    check_rs(rs, "query_trade_dates")

    fields = rs.fields
    while rs.next():
        row = dict(zip(fields, rs.get_row_data()))
        try:
            d = datetime.strptime(row["calendar_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            log.warning("skip bad calendar row: %r", row)
            continue
        is_open = 1 if row.get("is_trading_day") == "1" else 0
        yield {"trade_date": d, "is_open": is_open}


def upsert_calendar(rows: Iterable[dict], market: str = "CN") -> dict[str, int]:
    """批量 upsert 到 ``fr_trade_calendar``（按 ``market, trade_date`` 主键）。"""
    sql = (
        "INSERT INTO fr_trade_calendar (market, trade_date, is_open) "
        "VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE is_open=VALUES(is_open)"
    )

    total = 0
    batch: list[tuple] = []

    def _flush() -> int:
        nonlocal batch
        if not batch:
            return 0
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.executemany(sql, batch)
            c.commit()
        n = len(batch)
        batch = []
        return n

    affected = 0
    for row in rows:
        total += 1
        batch.append((market, row["trade_date"], row["is_open"]))
        if len(batch) >= 1000:
            affected += _flush()
    affected += _flush()
    log.info(
        "fr_trade_calendar upsert done: market=%s total=%d affected=%d",
        market,
        total,
        affected,
    )
    return {"inserted_or_updated": affected, "total": total}


def sync_calendar(start: date, end: date, market: str = "CN") -> dict[str, int]:
    """入口：拉 [start, end] 日历并写入。调用方需包在 ``baostock_session()`` 里。"""
    return upsert_calendar(fetch_calendar(start, end), market=market)
