"""Backfill historical PB and market cap using akshare stock_a_lg_indicator.

Usage: python -m backend.scripts.backfill_historical_indicator --start 2015-01-01 --end 2026-05-01

Uses ak.stock_a_lg_indicator(symbol="000001") which returns per-stock historical
data with columns: trade_date, pe, pb, ps, dv_ratio, total_mv.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _extract_6digit(symbol: str) -> str | None:
    """Extract 6-digit code from symbol like '000001.SZ' -> '000001'."""
    m = re.match(r"^(\d{6})\.", symbol)
    return m.group(1) if m else None


def backfill(start: date, end: date) -> None:
    import akshare as ak
    from backend.storage.mysql_client import mysql_conn
    from backend.storage.symbol_resolver import SymbolResolver

    resolver = SymbolResolver()

    # Get all symbols from stock_symbol table
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT symbol_id, symbol FROM stock_symbol")
            all_symbols = cur.fetchall()

    log.info("Total symbols in stock_symbol: %d", len(all_symbols))

    # Build list of (symbol_id, 6digit_code)
    symbol_list: list[tuple[int, str]] = []
    for row in all_symbols:
        sid = int(row["symbol_id"])
        sym = row["symbol"]
        code6 = _extract_6digit(sym)
        if code6:
            symbol_list.append((sid, code6))

    log.info("Symbols with valid 6-digit code: %d", len(symbol_list))

    success_count = 0
    fail_count = 0

    for idx, (sid, code6) in enumerate(symbol_list):
        if idx % 100 == 0:
            log.info("Progress: %d/%d processed (success=%d, fail=%d)",
                     idx, len(symbol_list), success_count, fail_count)

        try:
            df = ak.stock_a_lg_indicator(symbol=code6)
        except Exception as e:
            log.warning("Failed to fetch %s (sid=%d): %s", code6, sid, e)
            fail_count += 1
            time.sleep(0.5)
            continue

        if df is None or df.empty:
            fail_count += 1
            continue

        # Filter to date range
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        if df.empty:
            success_count += 1
            continue

        # Prepare batch inserts
        mv_rows = []
        pb_rows = []
        for _, row in df.iterrows():
            td = row["trade_date"]
            total_mv = _safe_float(row.get("total_mv"))
            pb_val = _safe_float(row.get("pb"))

            if total_mv is not None:
                mv_rows.append({
                    "symbol_id": sid,
                    "trade_date": td,
                    "total_mv": total_mv,
                    "float_mv": None,  # stock_a_lg_indicator doesn't have float_mv
                })
            if pb_val is not None:
                pb_rows.append({
                    "symbol_id": sid,
                    "trade_date": td,
                    "pb": pb_val,
                })

        # Batch write
        if mv_rows or pb_rows:
            try:
                with mysql_conn() as c:
                    with c.cursor() as cur:
                        if mv_rows:
                            cur.executemany(
                                "REPLACE INTO fr_daily_market_cap "
                                "(symbol_id, trade_date, total_mv, float_mv) "
                                "VALUES (%(symbol_id)s, %(trade_date)s, %(total_mv)s, %(float_mv)s)",
                                mv_rows,
                            )
                        if pb_rows:
                            cur.executemany(
                                "REPLACE INTO fr_daily_pb "
                                "(symbol_id, trade_date, pb) "
                                "VALUES (%(symbol_id)s, %(trade_date)s, %(pb)s)",
                                pb_rows,
                            )
                    c.commit()
            except Exception as e:
                log.error("DB write failed for sid=%d: %s", sid, e)
                fail_count += 1
                continue

        success_count += 1

        # Rate limit: small delay to avoid hitting akshare limits
        if idx % 50 == 0 and idx > 0:
            time.sleep(1)

    log.info("Backfill complete: success=%d, fail=%d, total=%d",
             success_count, fail_count, len(symbol_list))


def _safe_float(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return None
    try:
        f = float(val)
        return f if not (np.isnan(f) or np.isinf(f)) else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()
    backfill(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
