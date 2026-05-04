"""Fetch market cap + PB from akshare spot snapshot, write to MySQL."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver

log = logging.getLogger(__name__)

# akshare spot_em columns we need (Chinese -> English)
_RENAME = {
    "代码": "_raw_code",
    "总市值": "total_mv",
    "流通市值": "float_mv",
    "市净率": "pb",
}


def fetch_and_save_market_data(
    trade_date: date | None = None,
    spot_fetcher: Callable[[], pd.DataFrame] | None = None,
) -> int:
    """Pull akshare spot snapshot, write market cap + PB to MySQL.

    Returns number of rows written.
    """
    if spot_fetcher is None:
        import akshare as ak  # noqa: PLC0415

        spot_fetcher = ak.stock_zh_a_spot_em

    raw = spot_fetcher()
    if raw.empty:
        log.warning("akshare spot snapshot returned empty")
        return 0

    df = raw[list(_RENAME.keys())].rename(columns=_RENAME).copy()
    df["symbol"] = df["_raw_code"].apply(_safe_normalize)
    df = df[df["symbol"].notna() & (df["symbol"] != "")]

    resolver = SymbolResolver()
    sid_map = resolver.resolve_many(df["symbol"].tolist())
    df["symbol_id"] = df["symbol"].map(sid_map)
    df = df[df["symbol_id"].notna()]

    if trade_date is None:
        trade_date = date.today()

    # market cap
    mv_rows = []
    pb_rows = []
    for _, row in df.iterrows():
        sid = int(row["symbol_id"])
        mv_rows.append({
            "symbol_id": sid,
            "trade_date": trade_date,
            "total_mv": _safe_decimal(row.get("total_mv")),
            "float_mv": _safe_decimal(row.get("float_mv")),
        })
        pb_rows.append({
            "symbol_id": sid,
            "trade_date": trade_date,
            "pb": _safe_decimal(row.get("pb")),
        })

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.executemany(
                "REPLACE INTO fr_daily_market_cap (symbol_id, trade_date, total_mv, float_mv) "
                "VALUES (%(symbol_id)s, %(trade_date)s, %(total_mv)s, %(float_mv)s)",
                mv_rows,
            )
            cur.executemany(
                "REPLACE INTO fr_daily_pb (symbol_id, trade_date, pb) "
                "VALUES (%(symbol_id)s, %(trade_date)s, %(pb)s)",
                pb_rows,
            )
        c.commit()

    log.info("Saved %d market cap + %d PB rows for %s", len(mv_rows), len(pb_rows), trade_date)
    return len(mv_rows)


def _safe_normalize(raw_code: str) -> str | None:
    try:
        return normalize_symbol(str(raw_code))
    except (ValueError, TypeError):
        return None


def _safe_decimal(val) -> float | None:
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
