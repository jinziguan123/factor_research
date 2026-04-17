"""从 ``stock_bar_1m`` 聚合日频 K 线到 ``stock_bar_1d``（ClickHouse）。

聚合规则（见 design §3.2）：
- ``open``  = ``argMin(open, minute_slot)`` —— 当日第一根分钟 K 线的开盘价
- ``high``  = ``max(high)``
- ``low``   = ``min(low)``
- ``close`` = ``argMax(close, minute_slot)`` —— 当日最后一根分钟 K 线的收盘价
- ``volume``   = ``sum(volume)``  → UInt64（日累加可能超过 UInt32 上限）
- ``amount_k`` = ``sum(amount_k)``（千元单位）→ UInt32 足够
- ``version``  = ``toUnixTimestamp(now())`` —— 保证每次聚合 version 单调递增，
  ReplacingMergeTree 会以更大的 version 覆盖旧行；幂等由 (symbol_id, trade_date) 主键 + FINAL 保证

工作模式：
- ``full``：按 ``--start`` / ``--end`` 指定窗口全量聚合
- ``incremental``：从 ``max(trade_date)+1`` 到今天（或 ``--end``）增量聚合；
  **空库** 情形回退为"什么也不做"（或按用户传入的 start），由 CLI 侧决定

安全：开头调 ``_safety_check()``，拒绝在生产库上执行聚合。聚合虽然不修改原始分钟数据，
但会覆盖日线（大版本覆盖小版本），在生产库误跑可能产生未预期的日线刷新。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 让 `python backend/scripts/aggregate_bar_1d.py` 从项目根直接跑时也能找到 backend 包
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.config import settings
from backend.scripts.run_init import _safety_check
from backend.storage.clickhouse_client import ch_client

logger = logging.getLogger(__name__)

# 聚合 SQL：GROUP BY (symbol_id, trade_date) 后插入 stock_bar_1d。
# 读取侧加 FINAL，确保分钟表最近的 DELETE / 重复版本不会污染聚合。
_AGG_SQL = """
INSERT INTO quant_data.stock_bar_1d
    (symbol_id, trade_date, open, high, low, close, volume, amount_k, version, updated_at)
SELECT
    symbol_id,
    trade_date,
    argMin(open, minute_slot)        AS open,
    max(high)                        AS high,
    min(low)                         AS low,
    argMax(close, minute_slot)       AS close,
    toUInt64(sum(volume))            AS volume,
    toUInt32(sum(amount_k))          AS amount_k,
    toUInt64(toUnixTimestamp(now())) AS version,
    now()                            AS updated_at
FROM quant_data.stock_bar_1m FINAL
WHERE trade_date >= %(s)s AND trade_date <= %(e)s
GROUP BY symbol_id, trade_date
"""


def get_latest_aggregated_date() -> date | None:
    """返回 ``stock_bar_1d`` 中已聚合的最大 ``trade_date``，空表返回 ``None``。

    必须加 FINAL：ReplacingMergeTree 未合并时 MAX 也可能读到旧版本，但 trade_date
    作为主键的一部分在版本间是相同的，理论上不会漂；保守起见仍走 FINAL 防御。
    """
    with ch_client() as ch:
        rows = ch.execute(
            "SELECT max(trade_date) FROM quant_data.stock_bar_1d FINAL"
        )
    if not rows or rows[0][0] is None:
        return None
    v = rows[0][0]
    # clickhouse-driver 可能返回 date 或 datetime；统一为 date。
    return v if isinstance(v, date) and not isinstance(v, datetime) else v.date()


def aggregate(start: date, end: date) -> int:
    """执行 ``[start, end]`` 闭区间的聚合；返回窗口内已写入日数（symbol×date 粒度）。

    实现上分两步：
    1. 执行 INSERT ... SELECT，一次 ClickHouse 内部批处理完成。
    2. 执行 ``SELECT count() FROM stock_bar_1d FINAL WHERE ...``，返回窗口内行数，
       便于 CLI 打印 / 调用方做完整性校验。

    开头调用 ``_safety_check()``：和 ``import_qfq.run_import`` 保持一致，避免他处
    ``from aggregate_bar_1d import aggregate`` 时绕过 CLI 的生产库护栏。
    """
    _safety_check()

    if start > end:
        raise ValueError(f"invalid window: start={start} > end={end}")

    params = {"s": start, "e": end}
    logger.info("aggregate_bar_1d: window=[%s, %s]", start, end)
    with ch_client() as ch:
        ch.execute(_AGG_SQL, params)
        # 读侧 FINAL：ReplacingMergeTree 后台合并异步，FINAL 保证看到最新版本。
        rows = ch.execute(
            "SELECT count() FROM quant_data.stock_bar_1d FINAL "
            "WHERE trade_date >= %(s)s AND trade_date <= %(e)s",
            params,
        )
    count = int(rows[0][0]) if rows else 0
    logger.info("aggregate_bar_1d: wrote %d rows in window", count)
    return count


def _parse_date(value: str) -> date:
    """argparse 用的 date 解析器（ISO 格式 YYYY-MM-DD）。"""
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        description="Aggregate stock_bar_1m into stock_bar_1d (ClickHouse)."
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_full = sub.add_parser("full", help="按 --start/--end 全量聚合")
    p_full.add_argument("--start", type=_parse_date, required=True)
    p_full.add_argument("--end", type=_parse_date, required=True)

    p_inc = sub.add_parser("incremental", help="从 max(trade_date)+1 聚合到 --end（默认今天）")
    p_inc.add_argument(
        "--end",
        type=_parse_date,
        default=date.today(),
        help="增量窗口右端点，默认今天。",
    )
    p_inc.add_argument(
        "--start",
        type=_parse_date,
        default=None,
        help="增量模式下，当 stock_bar_1d 为空时使用的 start；默认为 end 当天。",
    )

    args = parser.parse_args()
    # 这里也显式调一次：``incremental`` 模式在进入 ``aggregate()`` 之前还会先查
    # ``stock_bar_1d``（get_latest_aggregated_date），该查询同样不应在生产库上发生。
    _safety_check()

    if args.mode == "full":
        start, end = args.start, args.end
    else:
        last = get_latest_aggregated_date()
        if last is not None:
            start = last + timedelta(days=1)
        elif args.start is not None:
            start = args.start
        else:
            # 空库且未显式指定 start：退化为只跑 end 当天，避免"一口气聚合十年"的意外。
            start = args.end
        end = args.end
        if start > end:
            logger.info(
                "aggregate_bar_1d: nothing to do (start=%s > end=%s)", start, end
            )
            return

    count = aggregate(start, end)
    print(f"aggregated {count} rows in [{start}, {end}]")


if __name__ == "__main__":
    main()
