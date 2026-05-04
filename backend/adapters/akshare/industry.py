"""Fetch industry classification from akshare, write to MySQL."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import pandas as pd

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def fetch_and_save_industry(
    snapshot_date: date | None = None,
    fetcher: Callable[[], pd.DataFrame] | None = None,
) -> int:
    """Pull akshare Shenwan industry classification, write changed rows to
    fr_industry_history. Returns number of new/changed rows written.

    Only writes rows when industry_l1 has changed or the symbol is new,
    creating a natural historical snapshot.
    """
    if fetcher is None:
        import akshare as ak  # noqa: PLC0415
        fetcher = ak.stock_board_industry_name_em

    raw = fetcher()
    if raw.empty:
        log.warning("akshare industry returned empty")
        return 0

    if snapshot_date is None:
        snapshot_date = date.today()

    rows: list[dict] = []
    for _, r in raw.iterrows():
        code = str(r.get("代码", ""))
        try:
            symbol = normalize_symbol(code)
        except (ValueError, TypeError):
            continue
        rows.append({
            "symbol": symbol,
            "snapshot_date": snapshot_date,
            "industry_l1": str(r.get("板块名称", "")).strip() or None,
            "industry_l2": str(r.get("板块名称", "")).strip() or None,
            "classification": "sw",
        })

    if not rows:
        return 0

    with mysql_conn() as c:
        with c.cursor() as cur:
            written = 0
            for row in rows:
                cur.execute(
                    "SELECT industry_l1 FROM fr_industry_history "
                    "WHERE symbol=%s ORDER BY snapshot_date DESC LIMIT 1",
                    (row["symbol"],),
                )
                prev = cur.fetchone()
                if prev is None or prev.get("industry_l1") != row["industry_l1"]:
                    cur.execute(
                        "INSERT INTO fr_industry_history "
                        "(symbol, snapshot_date, industry_l1, industry_l2, classification) "
                        "VALUES (%(symbol)s, %(snapshot_date)s, %(industry_l1)s, "
                        "%(industry_l2)s, %(classification)s)",
                        row,
                    )
                    written += 1
        c.commit()

    log.info("Industry: %d new/changed rows for %s", written, snapshot_date)
    return written
