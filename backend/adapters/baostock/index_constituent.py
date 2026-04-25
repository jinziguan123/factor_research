"""从 Baostock 同步 HS300 / ZZ500 / ZZ1000 历史成分到 ``fr_index_constituent``。

# 算法（关键设计）

Baostock 的 ``query_hs300_stocks(date=...)`` / ``query_zz500_stocks(date=...)`` 接口
对**任意历史日期**返回该日期之前最近一次成分调整后的完整成分列表，且每行的
``updateDate`` 字段 = **该次成分调整的公告日**（HS300/ZZ500 通常每年 6 月底 +
12 月底各调整一次）。

利用这一点，我们不做"按月盲快照"，而是**按调整日翻篇**：

1. 选一组探测日期（每年 Jun 30 / Dec 31，从 ``start`` 到今天）；
2. 对每个 index_code，按时间顺序查 baostock，把 (updateDate, symbols) 收集起来；
3. 按 updateDate 去重（多个探测日可能落到同一次调整）；
4. 按时间顺序 diff：
   - 新进者（new ∉ prev_active）→ INSERT effective_date=updateDate, end_date=NULL；
   - 离开者（prev_active ∉ new）→ UPDATE 现有行 SET end_date=updateDate；
   - 留下者：no-op。

这样得到的 ``effective_date`` / ``end_date`` 是 baostock 视角下的"真历史"调整日，
不是按月盲断点，比"每月快照"更精准（也更省查询次数）。

# 幂等

- 重复跑：第一次写入 N 个调整点；二次跑遇到已存在的 (index_code, symbol,
  effective_date) 主键 → 跳过（不重写已 close 的 end_date）；如果出现新的
  updateDate（比如新一轮调整），按上述 diff 流程增量处理。
- 第一次跑空库：所有探测到的最早 updateDate 的成员当作"基线进入"——这是个白盒
  约定，即 effective_date 不会早于探测窗口起点。建议 ``start`` 用 2015-01-01。

# 不灌的字段

- ``weight``：Baostock 该接口不返回权重，留 NULL。Phase 2.5 接 Akshare/Wind 时补。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Iterable

from backend.adapters.base import normalize_symbol
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


# 三大宽基指数：内部用 QMT 格式 "000300.SH" 等；baostock 接口名 → index_code 映射。
# Phase 2.a：HS300 + ZZ500 必上；ZZ1000 baostock 不一定都有，尝试性接入。
INDEX_CODES = {
    "000300.SH": "query_hs300_stocks",
    "000905.SH": "query_zz500_stocks",
    "000852.SH": "query_zz1000_stocks",
}


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


def _probe_dates(start: date, end: date) -> list[date]:
    """生成探测日期序列：每年 6/30 + 12/31，加上 ``end`` 自身。

    HS300/ZZ500 一般每年 6 月底 + 12 月底各调整一次，覆盖这两个时点足够把所有
    调整 updateDate 挖出来。``end`` 作为最后一次探测，把"距今最近的一次调整"
    也取到。
    """
    out: list[date] = []
    for y in range(start.year, end.year + 1):
        for m, d in ((6, 30), (12, 31)):
            probe = date(y, m, d)
            if start <= probe <= end:
                out.append(probe)
    if not out or out[-1] != end:
        out.append(end)
    return out


def fetch_constituents_at(
    index_code: str, query_date: date
) -> tuple[date | None, set[str]]:
    """在 ``query_date`` 时点查指数成分。

    返回 ``(updateDate, symbols)``：``updateDate`` 是该日期之前最近一次成分调整的
    公告日，``symbols`` 是规范化后的 QMT 代码集合。接口失败返回 ``(None, set())``。
    """
    import baostock as bs  # noqa: PLC0415

    fn_name = INDEX_CODES.get(index_code)
    if fn_name is None:
        raise ValueError(f"unsupported index_code: {index_code}")
    fn = getattr(bs, fn_name, None)
    if fn is None:
        # 该 baostock 版本不带这个接口（如 zz1000）→ skip 而不是抛
        log.warning("baostock has no %s; skipping index %s", fn_name, index_code)
        return None, set()

    rs = fn(date=query_date.strftime("%Y-%m-%d"))
    if rs.error_code != "0":
        log.warning(
            "%s(%s) failed: %s %s",
            fn_name,
            query_date,
            rs.error_code,
            rs.error_msg,
        )
        return None, set()

    symbols: set[str] = set()
    update_date: date | None = None
    fields = rs.fields
    while rs.next():
        row = dict(zip(fields, rs.get_row_data()))
        try:
            sym = normalize_symbol(row.get("code", ""))
        except ValueError:
            continue
        symbols.add(sym)
        if update_date is None:
            update_date = _parse_date(row.get("updateDate"))
    return update_date, symbols


def _apply_diff(
    index_code: str,
    update_date: date,
    new_symbols: set[str],
) -> dict[str, int]:
    """把 ``update_date`` 这一次调整的 diff 落库。

    具体语义：
    - prev_active = DB 中 ``effective_date < update_date`` 且 ``end_date IS NULL`` 的集合；
    - 离开 = prev_active - new_symbols → ``UPDATE ... SET end_date=update_date``；
    - 进入 = new_symbols - prev_active → ``INSERT ... ON DUPLICATE KEY ...``（防重跑）。
    - 严格 ``effective_date < update_date``（不取等）：避免重跑时 active = 当次新插入的
      行被错误地视为"prev"再去关 end_date。
    """
    sql_active = (
        "SELECT symbol FROM fr_index_constituent "
        "WHERE index_code=%s AND end_date IS NULL AND effective_date<%s"
    )
    sql_close = (
        "UPDATE fr_index_constituent SET end_date=%s "
        "WHERE index_code=%s AND symbol=%s AND end_date IS NULL "
        "  AND effective_date<%s"
    )
    sql_insert = (
        "INSERT INTO fr_index_constituent "
        "(index_code, symbol, effective_date, end_date, weight, data_source) "
        "VALUES (%s, %s, %s, NULL, NULL, 'baostock') "
        "ON DUPLICATE KEY UPDATE effective_date=VALUES(effective_date)"
    )

    inserted = 0
    closed = 0
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql_active, (index_code, update_date))
            # 使用 DictCursor，row 是 dict（key=列名）
            prev_active = {row["symbol"] for row in cur.fetchall()}

            departures = prev_active - new_symbols
            new_entrants = new_symbols - prev_active

            if departures:
                cur.executemany(
                    sql_close,
                    [
                        (update_date, index_code, sym, update_date)
                        for sym in departures
                    ],
                )
                closed = cur.rowcount

            if new_entrants:
                cur.executemany(
                    sql_insert,
                    [(index_code, sym, update_date) for sym in new_entrants],
                )
                inserted = cur.rowcount
        c.commit()

    log.info(
        "fr_index_constituent diff applied: index=%s updateDate=%s "
        "closed=%d inserted=%d (prev_active=%d new_total=%d)",
        index_code,
        update_date,
        closed,
        inserted,
        len(prev_active),
        len(new_symbols),
    )
    return {
        "closed": closed,
        "inserted": inserted,
        "prev_active": len(prev_active),
        "new_total": len(new_symbols),
    }


def sync_index_constituent(
    index_codes: Iterable[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """入口：按调整日翻篇同步指数成分。需在 ``baostock_session()`` 内调用。

    - ``index_codes``：默认全部三个；
    - ``start`` / ``end``：探测窗口；缺省 2015-01-01..今天。
    """
    if index_codes is None:
        index_codes = list(INDEX_CODES.keys())
    if start is None:
        start = date(2015, 1, 1)
    if end is None:
        end = date.today()

    probes = _probe_dates(start, end)
    summary: dict[str, dict] = {}

    for idx in index_codes:
        # 单次拉取（避免对每个 probe 重复网络调用），按 updateDate 去重 + 升序
        seen: set[date] = set()
        ordered_pairs: list[tuple[date, set[str]]] = []
        for q in probes:
            ud, syms = fetch_constituents_at(idx, q)
            if ud is None or not syms or ud in seen:
                continue
            seen.add(ud)
            ordered_pairs.append((ud, syms))
        ordered_pairs.sort(key=lambda x: x[0])

        per_idx: list[dict] = []
        for ud, syms in ordered_pairs:
            per_idx.append(_apply_diff(idx, ud, syms))

        summary[idx] = {
            "adjustments_processed": len(ordered_pairs),
            "details": per_idx,
        }

    log.info("sync_index_constituent done: %s", summary)
    return summary
