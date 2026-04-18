"""把前复权因子宽表 parquet 导入 MySQL ``fr_qfq_factor``。

parquet 约定：
- 行索引：交易日（datetime / Timestamp）
- 每一列：股票代码（如 ``000001.SZ``）
- 值：前复权因子（float）

设计要点：
- **不写 ``stock_symbol``**：未知 symbol 仅记 WARN 并跳过。stock_symbol 由 timing_driven
  维护，本脚本只读；如因子 parquet 含 stock_symbol 里没有的代码，说明上游同步不齐，
  应在生产侧解决，而不是让研究端静悄悄补录一条可能错误的映射。
- **按列分批读**：parquet 宽表可能有 5000+ 只股票，一次 ``read_parquet`` 容易 OOM；
  这里用 ``pyarrow.parquet.read_schema`` 仅拿列名，再分 chunk 调 ``pd.read_parquet(columns=...)``。
- **幂等**：``INSERT ... ON DUPLICATE KEY UPDATE factor, source_file_mtime``；主键
  ``(symbol_id, trade_date)``，重复跑同一文件行数不增长。
- **source_file_mtime**：记录 parquet 文件 mtime（``int(os.path.getmtime)``），方便后续
  增量判断是否需要重跑。
- **安全**：开头调 ``_safety_check()``，避免误连生产库。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterator

# 让 `python backend/scripts/importers/qfq.py` 从项目根直接跑时也能找到 backend 包
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pandas as pd
import pyarrow.parquet as pq

from backend.config import settings
from backend.scripts.run_init import _safety_check
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver

logger = logging.getLogger(__name__)


def _list_symbol_columns(file_path: str | Path) -> list[str]:
    """从 parquet schema 里拿除索引外的所有列名（即股票代码列）。

    pandas 在写 parquet 时会把 ``index.name`` 当作物理列落盘，同时在
    ``schema.metadata[b"pandas"]`` 的 JSON 中用 ``index_columns`` 字段记录哪些是索引。
    这里读元信息，**不**把索引列当作 symbol，避免把 ``trade_date`` 误当 symbol 去 resolve。
    """
    schema = pq.read_schema(str(file_path))
    all_cols = list(schema.names)

    index_cols: set[str] = set()
    meta = schema.metadata or {}
    pandas_meta_raw = meta.get(b"pandas")
    if pandas_meta_raw:
        try:
            pandas_meta = json.loads(pandas_meta_raw)
            for entry in pandas_meta.get("index_columns", []):
                # index_columns 元素可能是 str（有名索引）或 dict（RangeIndex），
                # 只有 str 形式才对应物理列。
                if isinstance(entry, str):
                    index_cols.add(entry)
        except (ValueError, TypeError):
            # 元数据损坏时退化为"把所有列都当 symbol"，让下游 resolver 自行过滤。
            logger.warning("parquet pandas metadata parse failed: %s", file_path)

    return [c for c in all_cols if c not in index_cols]


def _iter_column_chunks(
    file_path: str | Path,
    symbols: list[str],
    chunk_size: int,
) -> Iterator[tuple[list[str], pd.DataFrame]]:
    """按 ``chunk_size`` 只股票一批读 parquet，yield ``(batch_symbols, frame)``。

    ``pd.read_parquet(columns=...)`` 只读请求的列，内存随 chunk_size 线性增长，
    避免一次性把全部股票 × 全部日期读进内存。
    """
    for i in range(0, len(symbols), chunk_size):
        batch = symbols[i : i + chunk_size]
        frame = pd.read_parquet(str(file_path), columns=batch)
        yield batch, frame


def _upsert_qfq_rows(conn, rows: list[tuple]) -> None:
    """批量 upsert 到 ``fr_qfq_factor``。

    ``rows`` 元素：``(symbol_id, trade_date, factor, source_file_mtime)``。
    幂等策略：主键冲突时用新值覆盖 ``factor`` 与 ``source_file_mtime``，
    ``created_at`` 保留首次写入时间（不在 UPDATE 列表里）。
    """
    if not rows:
        return
    sql = (
        "INSERT INTO fr_qfq_factor "
        "(symbol_id, trade_date, factor, source_file_mtime) "
        "VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "factor=VALUES(factor), source_file_mtime=VALUES(source_file_mtime)"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def run_import(
    file_path: str | Path | None = None,
    chunk_size: int = 500,
) -> dict:
    """执行一次完整的 parquet → MySQL 导入。

    返回 dict：

    - ``file_path``：实际使用的文件路径字符串
    - ``symbol_count``：成功解析并写入的股票数
    - ``row_count``：写入的 (symbol, date) 行数（剔除 NaN 前）
    - ``skipped_count``：stock_symbol 中找不到的股票数
    - ``source_file_mtime``：parquet 文件 mtime（int 秒）
    """
    _safety_check()

    path = Path(file_path) if file_path is not None else Path(settings.qfq_factor_path)
    if not path.exists():
        raise FileNotFoundError(f"QFQ parquet not found: {path}")

    source_mtime = int(os.path.getmtime(path))
    logger.info("import_qfq: file=%s mtime=%s", path, source_mtime)

    all_symbols = _list_symbol_columns(path)
    resolver = SymbolResolver()

    # 一次性批量解析，避免在 chunk 循环内对每个 symbol 触发单点 SELECT（N+1）。
    # resolve_many 内部走 `WHERE symbol IN (...)` 一次往返；未知 symbol 会被过滤掉。
    mapping = resolver.resolve_many(all_symbols)
    skipped_count = len(all_symbols) - len(mapping)
    if skipped_count > 0:
        missing = [s for s in all_symbols if s not in mapping]
        logger.warning(
            "import_qfq: %d symbols not in stock_symbol, will skip; first 10: %s",
            skipped_count,
            missing[:10],
        )

    symbol_count = 0
    row_count = 0

    with mysql_conn() as conn:
        for batch, frame in _iter_column_chunks(path, all_symbols, chunk_size):
            # frame.index 可能是 DatetimeIndex 或 ObjectIndex，统一转 date。
            # 兼容：1) DatetimeIndex.date 返回 numpy object；2) 已经是 date/str 的情况。
            idx = pd.to_datetime(frame.index)
            dates = [d.date() for d in idx]

            rows: list[tuple] = []
            for symbol in batch:
                sid = mapping.get(symbol)
                if sid is None:
                    # 未知 symbol 已在前置 WARN 一次性提示并计入 skipped_count，这里静默跳过。
                    continue

                col = frame[symbol]
                symbol_count += 1
                for dt, val in zip(dates, col.tolist()):
                    # 丢弃 NaN：未上市/停牌日无有效因子，写 0 会在复权时产生错误价格。
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        continue
                    rows.append((int(sid), dt, float(val), source_mtime))
                    row_count += 1

            _upsert_qfq_rows(conn, rows)
            # 每个 chunk commit 一次：大文件导入若中途失败，已完成的 chunk 不回滚，
            # 再次运行会自然跳过已写入行（ON DUPLICATE KEY），保持增量友好。
            conn.commit()

    logger.info(
        "import_qfq done: symbols=%d rows=%d skipped=%d",
        symbol_count,
        row_count,
        skipped_count,
    )
    return {
        "file_path": str(path),
        "symbol_count": symbol_count,
        "row_count": row_count,
        "skipped_count": skipped_count,
        "source_file_mtime": source_mtime,
    }


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        description="Import qfq factor parquet into MySQL fr_qfq_factor."
    )
    parser.add_argument(
        "--file-path",
        default=None,
        help="parquet 文件路径；缺省使用 settings.qfq_factor_path。",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="按列分批的 chunk 大小（股票数），默认 500。",
    )
    args = parser.parse_args()

    result = run_import(file_path=args.file_path, chunk_size=args.chunk_size)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
