"""QMT ``.DAT`` → ClickHouse ``stock_bar_1m`` 导入器。

对外唯一入口是 :func:`run_import`，被 ``backend.api.routers.admin`` 的
BackgroundTask 调用，也可以通过命令行直跑。

三种调用姿势：

1. 全量单股：``run_import(mode="full", symbol="000001.SZ")``
2. 全量扫目录：``run_import(mode="full")``（递归 ``base_dir/<市场>/60/*.DAT``）
3. 增量：``run_import(mode="incremental")``，起点 = ClickHouse ``MAX(trade_date)``
   再回退 ``rewind_trading_days`` 个自然日作为安全窗，确保边界 K 线不漏。

并发：通过 ``workers`` 参数控制线程数（默认 1 = 串行）。每个 worker
独立创建 MySQL / ClickHouse 连接，互不干扰。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# 让 `python backend/scripts/importers/stock_1m.py` 直接跑时也能找到 backend 包。
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.config import settings
from backend.storage.clickhouse_client import ch_client
from backend.storage.mysql_client import mysql_conn
from backend.scripts.importers import _state
from backend.scripts.importers._bar_rows import normalize_symbol_bar_frame
from backend.scripts.importers._qmt_mmap import (
    DEFAULT_LOCAL_DATA_DIR,
    get_dat_file_path,
    read_iquant_mmap,
)

logger = logging.getLogger(__name__)

# 增量模式默认回退的交易日数；实际按自然日回退（更保守）。
_DEFAULT_REWIND_DAYS = 3

# 单次 INSERT 的切批规则。`stock_bar_1m` 按 toYYYYMM(trade_date) 分区，CH 默认
# `max_partitions_per_insert_block=100`；一只股票全量 10+ 年会撞上限。
#
# 需要两个维度一起管：
# - 行数上限：控制单次 INSERT 的内存 / 网络包大小；
# - **月分区上限**：活跃股每天 240 根，50k 行 ≈ 10 个月够用；但 B 股（200xxx.SZ）
#   每天实际落盘常 <10 根，50k 行可能横跨 20+ 个月甚至更多，仍会爆 100 上限。
#   所以必须显式限制"单批覆盖的唯一 (year, month) 数"。
#
# 50 个月阈值：明显低于 100 硬限，留足 50% 缓冲；对极限 B 股也足够切分到安全范围。
_CH_INSERT_BATCH_ROWS = 50_000
_CH_INSERT_BATCH_PARTITIONS = 50

# ClickHouse 插入时的列顺序（与 stock_bar_1m 物理表对齐）；version 用 time_ns。
_CH_COLUMNS = (
    "symbol_id",
    "trade_date",
    "minute_slot",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount_k",
    "version",
)


def _list_symbols_in_dir(base_dir: str) -> list[str]:
    """扫目录下所有 ``<market>/60/*.DAT``，返回 ``[CODE.MARKET, ...]``。

    只走三个已知市场子目录（SH/SZ/BJ），避免把不相关目录（temp 之类）混进去。
    """
    symbols: list[str] = []
    for market in ("SH", "SZ", "BJ"):
        minute_dir = Path(base_dir) / market / "60"
        if not minute_dir.is_dir():
            continue
        for p in minute_dir.glob("*.DAT"):
            code = p.stem
            if len(code) == 6 and code.isdigit():
                symbols.append(f"{code}.{market}")
    symbols.sort()
    return symbols


def _query_ch_last_trade_date(symbol_id: int) -> date | None:
    """从 ClickHouse 读单只股票已入库的最大 trade_date；空库返回 None。

    clickhouse-driver 在 ``use_numpy=True`` 模式下把 Date / DateTime 列返
    回成 ``numpy.datetime64``——它和 Python ``timedelta`` 直接相减会抛
    ``UFuncBinaryResolutionError``（dtype('<M8[D]') vs dtype('O')）。
    用 ``pd.Timestamp(v).date()`` 兜底，覆盖三种可能的返回类型：
    - ``datetime.date``：本就是目标
    - ``datetime.datetime``：取 .date()
    - ``numpy.datetime64``：经 pd.Timestamp 转 Python date
    """
    with ch_client() as ch:
        rows = ch.execute(
            "SELECT max(trade_date) FROM quant_data.stock_bar_1m FINAL "
            "WHERE symbol_id = %(sid)s",
            {"sid": int(symbol_id)},
        )
    if not rows or rows[0][0] is None:
        return None
    v = rows[0][0]
    try:
        return pd.Timestamp(v).date()
    except Exception:  # noqa: BLE001
        # 极端边角（非常老版 driver 把日期当字符串等）；记 log 让上层降级
        # 到 None（增量退化为全量），不阻塞后续因子。
        logger.warning(
            "_query_ch_last_trade_date: 不识别的返回类型 type=%s value=%r",
            type(v).__name__, v,
        )
        return None


def _incremental_start_ts(last_trade_date: date | None, rewind_days: int) -> int | None:
    """增量模式下的 ``start_ts`` 计算。

    :func:`read_iquant_mmap` 接受的 ``start_ts`` 是文件 ``time`` 字段（UTC 秒），
    而 last_trade_date 是北京日期。要把 "北京 (D-rewind) 00:00" 转成 UTC Unix 秒：
    ``pd.Timestamp(D).timestamp()`` 把 naive date 当作 UTC 秒算出的 epoch，
    再减 28800 就是北京零点对应的 UTC 秒——不依赖机器本地时区，跨平台一致。
    """
    if last_trade_date is None:
        return None
    start_day = last_trade_date - timedelta(days=max(1, int(rewind_days)))
    return int(pd.Timestamp(start_day).timestamp()) - 28800


def _normalize_frame_rows(
    symbol_id: int,
    symbol: str,
    frame: pd.DataFrame,
) -> list[tuple]:
    if frame.empty:
        return []
    try:
        return normalize_symbol_bar_frame(symbol_id=symbol_id, frame=frame)
    except ValueError as exc:
        logger.warning("normalize failed for %s: %s", symbol, exc)
        return []


def _compute_batch_boundaries(rows: Sequence[tuple]) -> list[tuple[int, int]]:
    """把 ``rows`` 切成 ``[(start, end), ...]``，保证每批同时满足：

    - ``end - start <= _CH_INSERT_BATCH_ROWS``（控内存 / 包大小）；
    - 批内唯一 ``(year, month)`` 数 ``<= _CH_INSERT_BATCH_PARTITIONS``（防爆 CH 100 硬限）。

    假设 ``rows`` 中 ``trade_date`` 单调不减——这是 ``normalize_symbol_bar_frame``
    的输出约定（mmap 按 time 升序读，后续 filter 不改序）。违反时退化为纯按行切，
    仍合法、只是月数约束不再强保。
    """
    if not rows:
        return []
    boundaries: list[tuple[int, int]] = []
    start = 0
    months: set[tuple[int, int]] = set()
    for i, r in enumerate(rows):
        trade_date = r[0]
        ym = (trade_date.year, trade_date.month)
        is_new_month = ym not in months
        over_rows = (i - start) >= _CH_INSERT_BATCH_ROWS
        over_months = is_new_month and len(months) >= _CH_INSERT_BATCH_PARTITIONS
        if i > start and (over_rows or over_months):
            boundaries.append((start, i))
            start = i
            months = set()
        months.add(ym)
    if start < len(rows):
        boundaries.append((start, len(rows)))
    return boundaries


_CH_INSERT_MAX_RETRIES = 3
_CH_INSERT_RETRY_BASE_DELAY = 2.0


def _insert_ch_rows(rows: Sequence[tuple], *, base_version: int) -> int:
    """把 (trade_date, minute_slot, symbol_id, o, h, l, c, volume, amount_k)
    批量写入 ClickHouse ``stock_bar_1m``。

    每个批次失败时会重试（新建连接），最多 ``_CH_INSERT_MAX_RETRIES`` 次。
    """
    if not rows:
        return 0
    inserted = 0
    for start, end in _compute_batch_boundaries(rows):
        batch = rows[start:end]
        batch_n = len(batch)
        cols = list(zip(*batch))
        versions = np.arange(batch_n, dtype=np.int64) + int(base_version) + start
        columns_np = [
            np.asarray(cols[2], dtype=np.uint32),           # symbol_id
            np.asarray(cols[0], dtype=object),              # trade_date (datetime.date)
            np.asarray(cols[1], dtype=np.uint16),           # minute_slot
            np.asarray(cols[3], dtype=np.float32),          # open
            np.asarray(cols[4], dtype=np.float32),          # high
            np.asarray(cols[5], dtype=np.float32),          # low
            np.asarray(cols[6], dtype=np.float32),          # close
            np.asarray(cols[7], dtype=np.uint32),           # volume
            np.asarray(cols[8], dtype=np.uint32),           # amount_k
            versions.astype(np.uint64),                     # version
        ]
        sql = (
            "INSERT INTO quant_data.stock_bar_1m "
            "(symbol_id, trade_date, minute_slot, open, high, low, close, "
            "volume, amount_k, version) VALUES"
        )
        for attempt in range(_CH_INSERT_MAX_RETRIES):
            try:
                with ch_client() as ch:
                    ch.execute(sql, columns_np, columnar=True)
                break
            except (ConnectionError, OSError) as exc:
                if attempt == _CH_INSERT_MAX_RETRIES - 1:
                    raise
                delay = _CH_INSERT_RETRY_BASE_DELAY * (attempt + 1)
                logger.warning(
                    "CH insert retry %d/%d (rows %d-%d): %s, wait %.1fs",
                    attempt + 1, _CH_INSERT_MAX_RETRIES, start, end, exc, delay,
                )
                time.sleep(delay)
        inserted += batch_n
    return inserted


def _import_one_symbol(
    *,
    conn,
    symbol: str,
    dat_path: str,
    incremental: bool,
    rewind_days: int,
    base_version: int,
) -> int:
    """处理单只股票：upsert_symbol → read DAT → normalize → CH insert。
    返回写入行数；出错会抛异常（由外层 run_import 统计失败数）。
    """
    if not os.path.exists(dat_path):
        logger.info("skip %s: dat not found at %s", symbol, dat_path)
        return 0

    symbol_id = _state.upsert_symbol(
        conn, symbol, name=None, dat_path=dat_path, is_active=1
    )

    start_ts: int | None = None
    if incremental:
        last = _query_ch_last_trade_date(symbol_id)
        start_ts = _incremental_start_ts(last, rewind_days)
        if last is not None:
            logger.info(
                "incremental %s: last=%s start_ts=%s", symbol, last, start_ts
            )

    frame = read_iquant_mmap(dat_path, start_ts=start_ts)
    rows = _normalize_frame_rows(symbol_id, symbol, frame)
    affected = _insert_ch_rows(rows, base_version=base_version)
    if affected:
        logger.info("imported %s: %d rows", symbol, affected)
    return affected


def _worker(
    symbol: str,
    idx: int,
    *,
    effective_base: str,
    incremental: bool,
    rewind_days: int,
    base_version: int,
) -> tuple[str, int, str | None]:
    """线程池 worker：处理一只股票。返回 (symbol, rows, error_msg)。

    MySQL 只在 upsert_symbol 时短暂持有，避免 CH 长写入期间连接空闲超时。
    """
    dat_path = get_dat_file_path(symbol, period="1m", base_dir=effective_base)
    try:
        if not os.path.exists(dat_path):
            logger.info("skip %s: dat not found at %s", symbol, dat_path)
            return symbol, 0, None

        # 短连接：拿 symbol_id 后立即释放
        with mysql_conn() as conn:
            symbol_id = _state.upsert_symbol(
                conn, symbol, name=None, dat_path=dat_path, is_active=1
            )
            conn.commit()

        bv = base_version + idx * 10_000_000

        start_ts: int | None = None
        if incremental:
            last = _query_ch_last_trade_date(symbol_id)
            start_ts = _incremental_start_ts(last, rewind_days)

        frame = read_iquant_mmap(dat_path, start_ts=start_ts)
        rows = _normalize_frame_rows(symbol_id, symbol, frame)
        affected = _insert_ch_rows(rows, base_version=bv)
        if affected:
            logger.info("imported %s: %d rows", symbol, affected)
        return symbol, affected, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("import failed for %s", symbol)
        return symbol, 0, str(exc)[:200]


def run_import(
    *,
    mode: str = "incremental",
    symbol: str | None = None,
    base_dir: str | None = None,
    rewind_days: int = _DEFAULT_REWIND_DAYS,
    limit: int | None = None,
    workers: int = 1,
) -> dict:
    """统一入口。

    参数
    ----
    mode : "full" | "incremental"
        全量会从 start_ts=None 读整个 .DAT；增量会查 CH MAX(trade_date) 反推起点。
    symbol : 指定单只股票（如 ``000001.SZ``）。None 表示扫整个 ``base_dir``。
    base_dir : QMT 数据根目录。None 则用环境变量 ``IQUANT_LOCAL_DATA_DIR``。
    rewind_days : 增量模式回退的自然日数，默认 3（留足除权/周末安全窗）。
    limit : 调试用，只处理前 N 只（None=不限制）。
    workers : 并发线程数，默认 1（串行）。全量导入建议 4~8。
    """
    if mode not in {"full", "incremental"}:
        raise ValueError(f"mode must be 'full' or 'incremental', got {mode!r}")

    incremental = mode == "incremental"
    effective_base = base_dir or DEFAULT_LOCAL_DATA_DIR

    if symbol:
        symbols: list[str] = [symbol.strip().upper()]
    else:
        symbols = _list_symbols_in_dir(effective_base)
        if limit is not None:
            symbols = symbols[: int(limit)]

    total = len(symbols)
    if total == 0:
        logger.warning(
            "run_import: no symbols to process (base_dir=%s, symbol=%s)",
            effective_base,
            symbol,
        )
        with mysql_conn() as conn:
            job_id = _state.create_job(
                conn,
                job_type=_state.JOB_TYPE_INCREMENTAL if incremental else _state.JOB_TYPE_FULL,
                symbol_count=0,
                note=f"no symbols; base_dir={effective_base}",
            )
            _state.update_job(
                conn,
                job_id,
                status=_state.JOB_STATUS_SUCCESS,
                finished_at=datetime.now().replace(microsecond=0),
            )
            conn.commit()
        return {
            "job_id": job_id,
            "symbol_count": 0,
            "success_symbol_count": 0,
            "failed_symbol_count": 0,
            "inserted_rows": 0,
            "mode": mode,
        }

    started = time.time()
    base_version = time.time_ns()
    success = 0
    failed = 0
    inserted_rows = 0
    failed_samples: list[tuple[str, str]] = []

    with mysql_conn() as conn:
        job_id = _state.create_job(
            conn,
            job_type=_state.JOB_TYPE_INCREMENTAL if incremental else _state.JOB_TYPE_FULL,
            symbol_count=total,
            note=f"factor_research.bar_1m base_dir={effective_base} workers={workers}",
        )
        conn.commit()

    effective_workers = max(1, min(workers, total))

    if effective_workers == 1:
        # 串行快路径：复用单连接，与原逻辑一致
        with mysql_conn() as conn:
            for idx, sym in enumerate(symbols):
                dat_path = get_dat_file_path(sym, period="1m", base_dir=effective_base)
                try:
                    n = _import_one_symbol(
                        conn=conn,
                        symbol=sym,
                        dat_path=dat_path,
                        incremental=incremental,
                        rewind_days=rewind_days,
                        base_version=base_version + idx * 10_000_000,
                    )
                    inserted_rows += n
                    success += 1
                    conn.commit()
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    failed += 1
                    failed_samples.append((sym, str(exc)[:200]))
                    logger.exception("import failed for %s", sym)
    else:
        # 并发路径：每个 worker 独立 MySQL/CH 连接
        logger.info("run_import: using %d workers for %d symbols", effective_workers, total)
        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {
                pool.submit(
                    _worker,
                    sym,
                    idx,
                    effective_base=effective_base,
                    incremental=incremental,
                    rewind_days=rewind_days,
                    base_version=base_version,
                ): sym
                for idx, sym in enumerate(symbols)
            }
            done_count = 0
            for fut in as_completed(futures):
                sym, n, err = fut.result()
                done_count += 1
                if err is None:
                    inserted_rows += n
                    success += 1
                else:
                    failed += 1
                    failed_samples.append((sym, err))
                if done_count % 200 == 0:
                    logger.info(
                        "progress: %d/%d done (%d ok, %d fail)",
                        done_count, total, success, failed,
                    )

    # 终态写回 job 表
    if failed == 0:
        status = _state.JOB_STATUS_SUCCESS
    elif success > 0:
        status = _state.JOB_STATUS_PARTIAL
    else:
        status = _state.JOB_STATUS_FAILED
    with mysql_conn() as conn:
        _state.update_job(
            conn,
            job_id,
            status=status,
            success_symbol_count=success,
            failed_symbol_count=failed,
            inserted_rows=inserted_rows,
            finished_at=datetime.now().replace(microsecond=0),
        )
        conn.commit()

    elapsed = time.time() - started
    logger.info(
        "run_import done: mode=%s workers=%d symbols=%d success=%d failed=%d rows=%d elapsed=%.1fs",
        mode,
        effective_workers,
        total,
        success,
        failed,
        inserted_rows,
        elapsed,
    )
    if failed_samples:
        preview = "; ".join(f"{s}:{e}" for s, e in failed_samples[:5])
        logger.warning("failed samples: %s", preview)

    return {
        "job_id": job_id,
        "symbol_count": total,
        "success_symbol_count": success,
        "failed_symbol_count": failed,
        "inserted_rows": inserted_rows,
        "mode": mode,
        "elapsed_seconds": round(elapsed, 3),
    }


# ---------------------------- CLI ----------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Import QMT .DAT minute bars into ClickHouse.")
    p.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="incremental",
        help="全量 or 增量；增量起点由 ClickHouse MAX(trade_date) 决定。",
    )
    p.add_argument(
        "--symbol",
        default=None,
        help="单股导入（如 000001.SZ）；省略则扫 base_dir 下全部 .DAT。",
    )
    p.add_argument(
        "--base-dir",
        default=None,
        help="QMT 数据根目录；省略则用环境变量 IQUANT_LOCAL_DATA_DIR。",
    )
    p.add_argument(
        "--rewind-days",
        type=int,
        default=_DEFAULT_REWIND_DAYS,
        help="增量模式回退自然日数，默认 3。",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只处理前 N 只股票（调试用）。",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发线程数，默认 1（串行）。全量导入建议 4~8。",
    )
    return p


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    result = run_import(
        mode=args.mode,
        symbol=args.symbol,
        base_dir=args.base_dir,
        rewind_days=args.rewind_days,
        limit=args.limit,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
