"""QMT ``.DAT`` → ClickHouse ``stock_bar_1m`` 导入器（单进程串行版）。

对外唯一入口是 :func:`run_import`，被 ``backend.api.routers.admin`` 的
BackgroundTask 调用，也可以通过命令行直跑。

三种调用姿势：

1. 全量单股：``run_import(mode="full", symbol="000001.SZ")``
2. 全量扫目录：``run_import(mode="full")``（递归 ``base_dir/<市场>/60/*.DAT``）
3. 增量：``run_import(mode="incremental")``，起点 = ClickHouse ``MAX(trade_date)``
   再回退 ``rewind_trading_days`` 个自然日作为安全窗，确保边界 K 线不漏。

为什么不做基于文件 mtime 的 skip（像 timing_driven 那样）？
- factor_research 没建 ``stock_bar_1m_import_state`` 表；
- 直接查 ClickHouse 的 MAX(trade_date) 是 **更可信** 的增量起点（看真实入库状态，
  不依赖"上次跑时记录了什么"这种外部快照），代价只是每股多一次索引扫描，
  在 ``(symbol_id, trade_date, minute_slot)`` 主键下非常廉价。

并发：**单进程串行**——前端触发一次跑几十秒到几分钟，对单用户研究场景够用。
如果未来真要跑全市场 5000+ 只，再加 ProcessPool（会新增 worker 参数，届时在
API 层加并发锁防止重复触发）。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
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
    """从 ClickHouse 读单只股票已入库的最大 trade_date；空库返回 None。"""
    with ch_client() as ch:
        rows = ch.execute(
            "SELECT max(trade_date) FROM quant_data.stock_bar_1m FINAL "
            "WHERE symbol_id = %(sid)s",
            {"sid": int(symbol_id)},
        )
    if not rows or rows[0][0] is None:
        return None
    v = rows[0][0]
    # clickhouse-driver 可能返回 date / datetime；统一为 date。
    if isinstance(v, datetime):
        return v.date()
    return v


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


def _insert_ch_rows(rows: Sequence[tuple], *, base_version: int) -> int:
    """把 (trade_date, minute_slot, symbol_id, o, h, l, c, volume, amount_k)
    批量写入 ClickHouse ``stock_bar_1m``。

    - 使用 clickhouse-driver 的 ``columnar=True`` 列式 INSERT，避免逐行转 tuple 的开销。
    - version = base_version + i，保证 ReplacingMergeTree 同一 PK 的新版本覆盖旧版本。
    """
    if not rows:
        return 0
    n = len(rows)
    versions = np.arange(n, dtype=np.int64) + int(base_version)
    # rows 是 normalize_symbol_bar_frame 的输出，列顺序：
    # (trade_date, minute_slot, symbol_id, open, high, low, close, volume, amount_k)
    cols = list(zip(*rows))
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
    with ch_client() as ch:
        ch.execute(
            "INSERT INTO quant_data.stock_bar_1m "
            "(symbol_id, trade_date, minute_slot, open, high, low, close, "
            "volume, amount_k, version) VALUES",
            columns_np,
            columnar=True,
        )
    return n


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


def run_import(
    *,
    mode: str = "incremental",
    symbol: str | None = None,
    base_dir: str | None = None,
    rewind_days: int = _DEFAULT_REWIND_DAYS,
    limit: int | None = None,
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

    返回 dict，字段含义与 ``stock_bar_import_job`` 对应列一致，便于上层前端展示。
    """
    if mode not in {"full", "incremental"}:
        raise ValueError(f"mode must be 'full' or 'incremental', got {mode!r}")

    incremental = mode == "incremental"
    effective_base = base_dir or DEFAULT_LOCAL_DATA_DIR

    # 任务样本清单：单股模式下就一条；全扫模式下遍历磁盘。
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
        # 仍然创建一条 job 记录，方便前端看到"这次触发什么也没干"。
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
            note=f"factor_research.bar_1m base_dir={effective_base}",
        )
        conn.commit()

        for idx, sym in enumerate(symbols):
            # 单股模式下 base_dir 只是个 hint：get_dat_file_path 本身兼容 None。
            dat_path = get_dat_file_path(sym, period="1m", base_dir=effective_base)
            try:
                n = _import_one_symbol(
                    conn=conn,
                    symbol=sym,
                    dat_path=dat_path,
                    incremental=incremental,
                    rewind_days=rewind_days,
                    # 每只股票一个递增 base_version，避免 ReplacingMergeTree 同分钟冲突。
                    base_version=base_version + idx * 1_000_000,
                )
                inserted_rows += n
                success += 1
                # 每只股票一次 commit，避免长事务占住 MySQL。
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                failed += 1
                failed_samples.append((sym, str(exc)[:200]))
                logger.exception("import failed for %s", sym)

        # 终态写回 job 表。
        if failed == 0:
            status = _state.JOB_STATUS_SUCCESS
        elif success > 0:
            status = _state.JOB_STATUS_PARTIAL
        else:
            status = _state.JOB_STATUS_FAILED
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
        "run_import done: mode=%s symbols=%d success=%d failed=%d rows=%d elapsed=%.1fs",
        mode,
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
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
