"""从 Baostock 同步全量标的（含退市）到 ``fr_instrument``。

Baostock 相关接口：
- ``query_all_stock(day=...)``：返回**某一日**存在（在市）的标的列表。仅靠这个接口
  拿不到历史退市，需要逐日（或按季度）滚一遍取并集；
- ``query_stock_basic(code=..., code_name=...)``：按代码 / 名称查**上市/退市日期 +
  状态**。这是本模块的主力接口，能直接拿到 ``listStatus`` / ``ipoDate`` / ``outDate``。

同步策略：
1. 先用 ``query_all_stock(day=today)`` 拿到"当前在市"的完整列表作为起点；
2. 然后调 ``query_stock_basic(code_name="")``（空串 = 全市场），补齐退市标的。
   该接口会返回所有曾经上市过的标的。

写入策略：
- ``INSERT ... ON DUPLICATE KEY UPDATE``，主键 ``symbol``；重复跑行数不增长；
- ``data_source='baostock'``；未来如果用 QMT 覆盖同一条，可以通过 data_source 审计。

**Phase 1 验收点**：运行完后 ``SELECT COUNT(*) FROM fr_instrument WHERE status='delisted'``
应该 > 0（Baostock 历史退市数应在 500+）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable, Iterator

from backend.adapters.base import infer_exchange, normalize_symbol
from backend.adapters.baostock.client import check_rs
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def _iter_rows(rs) -> Iterator[dict]:
    """把 baostock ``ResultData`` 迭代成 ``dict``（field → value）。"""
    fields = rs.fields
    while rs.next():
        row = rs.get_row_data()
        yield dict(zip(fields, row))


def _parse_date(s: str | None) -> date | None:
    """Baostock 日期字符串 ``"2019-05-28"`` → ``date``；空串 / None → None。"""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        log.warning("cannot parse date: %r", s)
        return None


def fetch_instruments() -> Iterable[dict]:
    """调用 Baostock 拉全量标的（含退市），yield 规范化后的行。

    每行 dict 的字段已对齐 ``fr_instrument`` 列：
    ``symbol / market / exchange / name / asset_type / list_date / delist_date /
      status / is_st / data_source``。
    """
    import baostock as bs  # noqa: PLC0415

    # query_stock_basic 空 code_name 时返回全市场（含退市）。接口 doc:
    # http://baostock.com/baostock/index.php/%E8%AF%81%E5%88%B8%E5%9F%BA%E6%9C%AC%E8%B5%84%E6%96%99
    # type: 1=股票 2=指数 3=其他 4=可转债 5=ETF
    # status: 1=上市 0=退市
    rs = bs.query_stock_basic(code="", code_name="")
    check_rs(rs, "query_stock_basic(all)")

    seen: set[str] = set()
    for row in _iter_rows(rs):
        raw_code = row.get("code", "")
        try:
            symbol = normalize_symbol(raw_code)
        except ValueError:
            log.warning("skip unknown code from baostock: %r", raw_code)
            continue

        if symbol in seen:
            continue
        seen.add(symbol)

        type_code = row.get("type", "")
        if type_code == "1":
            asset_type = "stock"
        elif type_code == "2":
            asset_type = "index"
        elif type_code == "5":
            asset_type = "etf"
        else:
            # 其它类型（可转债等）先不落，避免污染 fr_instrument 语义
            continue

        status_code = row.get("status", "")
        status = "active" if status_code == "1" else "delisted"

        # baostock 不直接给 is_st 标记；后续用 Akshare / 专门接口补。这里默认 0，
        # 只有股票名含 "ST" / "*ST" 时标一下，作为快速兜底——不完全准确，但
        # "改名过 ST 又改回来"的历史通常通过专门表处理，不放这里。
        name = (row.get("code_name") or "").strip()
        is_st = 1 if ("ST" in name.upper()) else 0

        yield {
            "symbol": symbol,
            "market": "CN",
            "exchange": infer_exchange(symbol),
            "name": name,
            "asset_type": asset_type,
            "list_date": _parse_date(row.get("ipoDate")),
            "delist_date": _parse_date(row.get("outDate")),
            "status": status,
            "is_st": is_st,
            "data_source": "baostock",
        }


def upsert_instruments(rows: Iterable[dict]) -> dict[str, int]:
    """把规范化后的行批量 upsert 到 ``fr_instrument``。

    返回 ``{"inserted_or_updated": N, "total": M}``；N 是 upsert 受影响行数，M 是
    迭代到的输入总数。上层据此记 log / 判 assert。
    """
    sql = (
        "INSERT INTO fr_instrument "
        "(symbol, market, exchange, name, asset_type, list_date, delist_date, "
        " status, is_st, data_source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        " market=VALUES(market), exchange=VALUES(exchange), name=VALUES(name), "
        " asset_type=VALUES(asset_type), list_date=VALUES(list_date), "
        " delist_date=VALUES(delist_date), status=VALUES(status), "
        " is_st=VALUES(is_st), data_source=VALUES(data_source)"
    )

    total = 0
    batch: list[tuple] = []
    affected = 0

    def _flush() -> int:
        nonlocal batch
        if not batch:
            return 0
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.executemany(sql, batch)
            c.commit()
        n = len(batch)
        batch = []
        return n

    for row in rows:
        total += 1
        batch.append(
            (
                row["symbol"],
                row["market"],
                row["exchange"],
                row["name"],
                row["asset_type"],
                row["list_date"],
                row["delist_date"],
                row["status"],
                row["is_st"],
                row["data_source"],
            )
        )
        if len(batch) >= 500:
            affected += _flush()

    affected += _flush()
    log.info("fr_instrument upsert done: total=%d affected=%d", total, affected)
    return {"inserted_or_updated": affected, "total": total}


def sync_instruments() -> dict[str, int]:
    """入口：从 Baostock 拉全量标的 → upsert 到 ``fr_instrument``。

    调用方应包在 ``baostock_session()`` 里。失败时抛 ``BaostockError`` 或 DB 异常，
    由上层（admin router）捕获后记 log。
    """
    return upsert_instruments(fetch_instruments())
