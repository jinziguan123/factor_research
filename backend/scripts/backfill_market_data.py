"""One-time historical backfill for market cap, PB, and industry data.

Usage: python -m backend.scripts.backfill_market_data --start 2015-01-01 --end 2026-05-01
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def backfill(start: date, end: date) -> None:
    from backend.adapters.akshare.market_data import fetch_and_save_market_data
    from backend.adapters.akshare.industry import fetch_and_save_industry

    # Pull current industry classification once
    log.info("Fetching current industry classification...")
    n = fetch_and_save_industry(date.today())
    log.info("Industry: %d rows written", n)

    # Backfill market cap + PB day by day
    cursor_date = start
    while cursor_date <= end:
        try:
            n = fetch_and_save_market_data(cursor_date)
            log.info("Market data for %s: %d rows", cursor_date, n)
        except Exception as e:
            log.error("Failed for %s: %s", cursor_date, e)
        cursor_date += timedelta(days=1)

    log.info("Backfill complete: %s -> %s", start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    backfill(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )
