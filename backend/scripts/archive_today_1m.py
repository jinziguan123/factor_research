"""手动归档当日 1m K 线到 ClickHouse ``stock_bar_1m``。

用途：在 live_market_worker 守护进程（S2）实现之前，提供一次性手动归档入口。
盘后 15:30 之后跑一次即可把当日所有票的 1m K 落库（akshare 累积快照语义保证完整）。

用法：
    python -m backend.scripts.archive_today_1m              # 默认全 A
    python -m backend.scripts.archive_today_1m --pool 5     # 仅 pool_id=5
    python -m backend.scripts.archive_today_1m --workers 30 # 提高并发数

依赖：
- akshare（生产环境装在 backend venv 里）
- ClickHouse stock_bar_1m 表（migration 008 已建）
- MySQL stock_symbol（symbol → symbol_id 映射）

幂等：ClickHouse ReplacingMergeTree 自动去重，重复跑无副作用。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# 让 `python -m backend.scripts.archive_today_1m` 从项目根能找到 backend 包
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.adapters.akshare_live import fetch_1m_bars_batch
from backend.storage.data_service import DataService
from backend.storage.realtime_dao import write_1m_bars

logger = logging.getLogger(__name__)


def archive_today(pool_id: int | None = None, max_workers: int = 20) -> dict:
    """拉取当日全部 1m K 并写入 ``stock_bar_1m``。

    Args:
        pool_id: 限定股票池；None → 用 stock_symbol 全表（约 5000+ 只）。
        max_workers: ThreadPoolExecutor 并发数；默认 20，超过 30 易触发 IP 频控。

    Returns:
        dict 统计：``{n_symbols, n_bars_written, n_errors, errors_sample}``
    """
    data = DataService()

    if pool_id is not None:
        symbols = data.resolve_pool(pool_id)
        logger.info("pool_id=%s 含 %d 只票", pool_id, len(symbols))
    else:
        # 全 A：从 stock_symbol 取所有 status='listed' 的票
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

    # 分批拉（避免一次提交 5000 任务到 ThreadPoolExecutor）
    batch_size = 500
    total_written = 0
    all_errors: list[tuple[str, str]] = []

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        logger.info(
            "[batch %d/%d] 拉 %d 只票...",
            i // batch_size + 1,
            (len(symbols) + batch_size - 1) // batch_size,
            len(batch),
        )
        bars_df, errors = fetch_1m_bars_batch(batch, max_workers=max_workers)
        if not bars_df.empty:
            n = write_1m_bars(bars_df)
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
    parser = argparse.ArgumentParser(description="手动归档当日 1m K 到 stock_bar_1m")
    parser.add_argument(
        "--pool", type=int, default=None,
        help="限定股票池 ID；不指定 = 全 A（stock_symbol 表所有 listed 票）",
    )
    parser.add_argument(
        "--workers", type=int, default=20,
        help="ThreadPoolExecutor 并发数（默认 20，30+ 易被 akshare 限流）",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    started = datetime.now()
    logger.info("=== archive_today_1m: pool=%s, workers=%d ===", args.pool, args.workers)
    stats = archive_today(pool_id=args.pool, max_workers=args.workers)
    elapsed = (datetime.now() - started).total_seconds()

    logger.info(
        "=== 完成：symbols=%d, written=%d, errors=%d, elapsed=%.1fs ===",
        stats["n_symbols"], stats["n_bars_written"], stats["n_errors"], elapsed,
    )
    if stats["errors_sample"]:
        logger.info("失败样本（前 10 个）：")
        for sym, err in stats["errors_sample"]:
            logger.info("  %s: %s", sym, err)

    return 0


if __name__ == "__main__":
    sys.exit(main())
