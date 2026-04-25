"""从 Baostock 同步**当前**行业归属到 ``fr_industry_current``。

⚠️ 重要 caveat：``query_stock_industry`` 只返回当前快照——全市场所有 row 的
``updateDate`` 都是同一个值（接口数据刷新日），不是行业归属变更日。所以本模块
**没有历史回溯能力**，只能维护"今天的行业归属是什么"。

历史回溯能力延后到 Phase 2.5：接 Akshare 申万 / 中信行业（带历史 effective 区间）。
本表仅用于：
- 因子中性化时取行业哑变量（用当前归属，承认存在轻微未来偏差）；
- 风险模型行业暴露监控；
- 因子手册里"该因子在哪些行业表现更好"的当前切片视图。

实现细节：
- ``query_stock_industry()`` 不传 code → 全市场；
- 一次性写完，**不分批**（单股票一行，5500 行级别在 InnoDB 上一把 upsert 没压力）。
- 行业字段为空（如刚退市或新上市）的 row 也照样落，``industry_l1=''``——上层做
  中性化时按 ``industry_l1!=''`` 过滤即可，不在 adapter 层丢数据。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable, Iterator

from backend.adapters.base import normalize_symbol
from backend.adapters.baostock.client import check_rs
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def _iter_rows(rs) -> Iterator[dict]:
    fields = rs.fields
    while rs.next():
        yield dict(zip(fields, rs.get_row_data()))


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def fetch_industry() -> Iterable[dict]:
    """yield ``{symbol, industry_l1, industry_classification, snapshot_date}``。

    snapshot_date 取自接口的 ``updateDate``；同一批次该值理论上全部相同，但仍逐行存
    以保留接口原样数据。
    """
    import baostock as bs  # noqa: PLC0415

    rs = bs.query_stock_industry()
    check_rs(rs, "query_stock_industry(all)")

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

        snapshot = _parse_date(row.get("updateDate"))
        if snapshot is None:
            # 没 updateDate 的行业 row 不应出现，但出现就回退到今天，留 log
            log.warning("industry row without updateDate: %r", row)
            snapshot = date.today()

        yield {
            "symbol": symbol,
            "industry_l1": (row.get("industry") or "").strip(),
            "industry_classification": (
                row.get("industryClassification") or ""
            ).strip(),
            "snapshot_date": snapshot,
        }


def upsert_industry(rows: Iterable[dict]) -> dict[str, int]:
    """批量 upsert 到 ``fr_industry_current``。"""
    sql = (
        "INSERT INTO fr_industry_current "
        "(symbol, industry_l1, industry_classification, snapshot_date, data_source) "
        "VALUES (%s, %s, %s, %s, 'baostock') "
        "ON DUPLICATE KEY UPDATE "
        " industry_l1=VALUES(industry_l1), "
        " industry_classification=VALUES(industry_classification), "
        " snapshot_date=VALUES(snapshot_date), "
        " data_source=VALUES(data_source)"
    )

    batch: list[tuple] = []
    total = 0
    for row in rows:
        total += 1
        batch.append(
            (
                row["symbol"],
                row["industry_l1"],
                row["industry_classification"],
                row["snapshot_date"],
            )
        )

    if not batch:
        log.warning("upsert_industry: no rows to write")
        return {"inserted_or_updated": 0, "total": 0}

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.executemany(sql, batch)
        c.commit()
    log.info("fr_industry_current upsert done: total=%d", total)
    return {"inserted_or_updated": total, "total": total}


def sync_industry() -> dict[str, int]:
    """入口：拉全市场当前行业归属并 upsert。需在 ``baostock_session()`` 内调用。"""
    return upsert_industry(fetch_industry())
