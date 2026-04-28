"""手动从 akshare 补 stock_bar_1d 缺口（旁路 QMT 数据链路）。

用途：当 QMT / 1m 聚合链路因故落后时（比如 worker 没跑、行情下载没启动），
旁路用 akshare ``stock_zh_a_hist`` 拉历史日线写入 ClickHouse ``stock_bar_1d``，
让 signal_service / 评估 / 回测能正常跑。

不取代 QMT 链路：QMT 拿到的是分钟级原始数据，更精确；本脚本仅作"应急补口"。
建议补完后让 QMT 重新跑一遍以替换为更精确版本（ReplacingMergeTree 自动取最新）。

用法：
    # 补 04-21 ~ 04-27 的全 A 日线
    python -m backend.scripts.backfill_daily_bars --start 2026-04-21 --end 2026-04-27

    # 只补某股票池
    python -m backend.scripts.backfill_daily_bars --start 2026-04-21 --end 2026-04-27 --pool 5

    # 提高并发
    python -m backend.scripts.backfill_daily_bars --start ... --end ... --workers 30

幂等：ClickHouse ReplacingMergeTree 自动去重，重复跑无副作用。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 让 `python -m backend.scripts.backfill_daily_bars` 能找到项目根
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.adapters.akshare_live import fetch_daily_bars_batch
from backend.storage.clickhouse_client import ch_client
from backend.storage.data_service import DataService
from backend.storage.symbol_resolver import SymbolResolver

logger = logging.getLogger(__name__)


def _write_bars_1d(df: pd.DataFrame, resolver: SymbolResolver) -> int:
    """把 daily K 长表批量写入 stock_bar_1d。

    字段映射：
    - symbol → symbol_id（resolver 映射，未知丢弃）
    - amount(元) → amount_k(千元) （除 1000）
    - 其它直转

    Returns:
        实际写入行数。
    """
    if df is None or df.empty:
        return 0

    sym_map = resolver.resolve_many(df["symbol"].unique().tolist())
    df = df[df["symbol"].isin(sym_map)].copy()
    if df.empty:
        logger.warning("backfill: 所有 symbol 都无法 resolve；跳过写入")
        return 0

    n = len(df)
    df["symbol_id"] = df["symbol"].map(sym_map).astype("uint32")
    # amount 是元 → amount_k 是千元；UInt32 上限 ~42 亿，4.2 万亿元够 A 股大票
    df["amount_k"] = (df["amount"].fillna(0) / 1000).round().clip(upper=2**32 - 1).astype("uint32")
    version = time.time_ns()

    columns_np = [
        df["symbol_id"].to_numpy(dtype=np.uint32),
        np.asarray(df["trade_date"].to_numpy(), dtype=object),
        df["open"].fillna(0.0).to_numpy(dtype=np.float32),
        df["high"].fillna(0.0).to_numpy(dtype=np.float32),
        df["low"].fillna(0.0).to_numpy(dtype=np.float32),
        df["close"].fillna(0.0).to_numpy(dtype=np.float32),
        df["volume"].fillna(0).to_numpy(dtype=np.uint64),
        df["amount_k"].to_numpy(dtype=np.uint32),
        np.array([version] * n, dtype=np.uint64),
    ]

    sql = (
        "INSERT INTO quant_data.stock_bar_1d "
        "(symbol_id, trade_date, open, high, low, close, volume, amount_k, version) VALUES"
    )
    with ch_client() as c:
        c.execute(sql, columns_np, columnar=True)
    return n


def backfill(
    start: date,
    end: date,
    pool_id: int | None = None,
    max_workers: int = 20,
    batch_size: int = 500,
) -> dict:
    """拉指定区间日线并写库。

    Args:
        start / end: 补的日期闭区间。
        pool_id: 限定股票池；None → 全 A（stock_symbol 表所有 listed 票）。
        max_workers: 线程并发数；> 30 易触发 akshare IP 限流。
        batch_size: 每批多少只票（避免一次提交几千 task 到 ThreadPoolExecutor）。

    Returns:
        ``{n_symbols, n_bars_written, n_errors, errors_sample}``。
    """
    data = DataService()
    resolver = SymbolResolver()

    if pool_id is not None:
        symbols = data.resolve_pool(pool_id)
        logger.info("pool_id=%s 含 %d 只票", pool_id, len(symbols))
    else:
        from backend.storage.mysql_client import mysql_conn

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT symbol FROM stock_symbol "
                    "WHERE asset_type='stock' AND (status='listed' OR status IS NULL)"
                )
                symbols = [r["symbol"] for r in (cur.fetchall() or [])]
        logger.info("全 A 池含 %d 只票", len(symbols))

    if not symbols:
        return {"n_symbols": 0, "n_bars_written": 0, "n_errors": 0, "errors_sample": []}

    # akshare stock_zh_a_hist 要 YYYYMMDD 格式
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    total_written = 0
    all_errors: list[tuple[str, str]] = []

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        logger.info(
            "[batch %d/%d] 拉 %d 只票 [%s, %s]...",
            i // batch_size + 1,
            (len(symbols) + batch_size - 1) // batch_size,
            len(batch), start_str, end_str,
        )
        bars_df, errors = fetch_daily_bars_batch(
            batch, start_str, end_str, max_workers=max_workers,
        )
        if not bars_df.empty:
            n = _write_bars_1d(bars_df, resolver)
            total_written += n
            logger.info("  → 写入 %d 行", n)
        if errors:
            all_errors.extend(errors)
            logger.warning("  → 失败 %d 只", len(errors))

    return {
        "n_symbols": len(symbols),
        "n_bars_written": total_written,
        "n_errors": len(all_errors),
        "errors_sample": all_errors[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 akshare 补 stock_bar_1d 日线（旁路 QMT 链路）"
    )
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD（含）")
    parser.add_argument("--pool", type=int, default=None, help="股票池 ID；不传 = 全 A")
    parser.add_argument("--workers", type=int, default=20, help="ThreadPoolExecutor 并发数")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if start > end:
        parser.error("start 必须 <= end")

    started = datetime.now()
    logger.info(
        "=== backfill_daily_bars: [%s, %s] pool=%s workers=%d ===",
        start, end, args.pool, args.workers,
    )
    stats = backfill(start, end, pool_id=args.pool, max_workers=args.workers)
    elapsed = (datetime.now() - started).total_seconds()

    logger.info(
        "=== 完成：symbols=%d, written=%d, errors=%d, elapsed=%.1fs ===",
        stats["n_symbols"], stats["n_bars_written"],
        stats["n_errors"], elapsed,
    )
    if stats["errors_sample"]:
        logger.info("失败样本（前 10 个）：")
        for sym, err in stats["errors_sample"]:
            logger.info("  %s: %s", sym, err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
