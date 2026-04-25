"""从 Baostock 同步 query_profit_data 到 fr_fundamental_profit。

# 用途

Phase 2.b 财务 PIT 链路。每行带 pubDate（公告日）和 statDate（报告期）双时间戳，
用于回测时严格按 "截至当日已公告的最新财报" 取用，避免前视偏差。

# 范围抽象

接受三种 universe：
- ``"hs300_history"``：fr_index_constituent 中曾经入过 HS300 的全部 symbol（约 600 个）
- ``"all_in_db"``：fr_instrument 全表（含退市，约 5500 个；耗时 4 小时级）
- ``list[str]``：显式指定 symbol 列表

时间窗：(start_year, start_quarter) 到今天，逐季 × 逐 symbol 循环。

# 性能与可重跑

单 symbol × 单季度一次 query_profit_data 调用；HS300 历史成员 × 7 年 28 季度
≈ 17000 次调用。Baostock 单线程吞吐 ~2-5 qps（视网络）→ 1-3 小时级。

幂等：ON DUPLICATE KEY UPDATE on (symbol, report_date)；同一 (year, quarter)
重跑会覆盖最新值（处理财报修订）。

# 失败容忍

每个 (symbol, year, quarter) 独立 try/except，单点失败 log + 继续；不中断整体进度。
进度每 200 个 (symbol, quarter) 写一条 log，方便长跑时观察。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from backend.adapters.base import normalize_symbol, to_baostock_symbol
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


_UPSERT_SQL = (
    "INSERT INTO fr_fundamental_profit "
    "(symbol, report_date, announcement_date, roe_avg, np_margin, gp_margin, "
    " net_profit, eps_ttm, mb_revenue, total_share, liqa_share, data_source) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'baostock') "
    "ON DUPLICATE KEY UPDATE "
    " announcement_date=VALUES(announcement_date), "
    " roe_avg=VALUES(roe_avg), np_margin=VALUES(np_margin), "
    " gp_margin=VALUES(gp_margin), net_profit=VALUES(net_profit), "
    " eps_ttm=VALUES(eps_ttm), mb_revenue=VALUES(mb_revenue), "
    " total_share=VALUES(total_share), liqa_share=VALUES(liqa_share), "
    " data_source=VALUES(data_source)"
)


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


def _parse_decimal(s: str | None) -> Decimal | None:
    """Baostock 数值字符串 → Decimal，空串 / "" / 非法 → None。

    比率字段（如 roeAvg）是 6 位小数 string，用 Decimal 避免 float 精度损耗。
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _resolve_universe(universe: str | list[str]) -> list[str]:
    """把 universe 参数解析成 QMT-format symbol 列表。"""
    if isinstance(universe, list):
        return universe

    if universe == "hs300_history":
        sql = (
            "SELECT DISTINCT symbol FROM fr_index_constituent "
            "WHERE index_code='000300.SH'"
        )
    elif universe == "all_in_db":
        # fr_instrument 是真相表（含退市）；只取股票，跳过指数 / ETF
        sql = "SELECT symbol FROM fr_instrument WHERE asset_type='stock'"
    else:
        raise ValueError(f"unknown universe: {universe!r}")

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql)
            return [r["symbol"] for r in cur.fetchall()]


def _iter_quarters(
    start_year: int, start_quarter: int, end: date
) -> Iterable[tuple[int, int]]:
    """yield (year, quarter) 从 start_year/start_quarter 到 end 之前。

    注意：财报有公告滞后；query_profit_data(year=Y, quarter=Q) 在公告前会返回空。
    我们一律生成到 ``end`` 当前季度（含），把公告还没出的季度也尝试一遍——空结果
    自然 skip，下次重跑时新公告就能补上。
    """
    y, q = start_year, start_quarter
    end_y = end.year
    end_q = (end.month - 1) // 3 + 1
    while (y, q) <= (end_y, end_q):
        yield y, q
        q += 1
        if q > 4:
            q = 1
            y += 1


def _fetch_profit_one(
    symbol: str, year: int, quarter: int
) -> tuple[dict | None, str | None]:
    """单次 query_profit_data 调用，返回 (row_dict 或 None, error_msg 或 None)。

    - 接口正常但无数据（未上市等）→ (None, None)；
    - 接口失败 → (None, error_msg)；
    - 接口正常有数据 → (row_dict, None)。
    """
    import baostock as bs  # noqa: PLC0415

    bs_code = to_baostock_symbol(symbol)
    rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
    if rs.error_code != "0":
        return None, f"{rs.error_code} {rs.error_msg}"
    fields = rs.fields
    while rs.next():
        row = dict(zip(fields, rs.get_row_data()))
        # 通常单 (code, year, quarter) 只返回 1 行，取第一条即返回
        return row, None
    return None, None


def _row_to_tuple(symbol: str, row: dict) -> tuple | None:
    """把 baostock row 转成 INSERT 参数 tuple；statDate / pubDate 缺失则丢弃。"""
    stat = _parse_date(row.get("statDate"))
    pub = _parse_date(row.get("pubDate"))
    if stat is None or pub is None:
        return None
    return (
        symbol,
        stat,
        pub,
        _parse_decimal(row.get("roeAvg")),
        _parse_decimal(row.get("npMargin")),
        _parse_decimal(row.get("gpMargin")),
        _parse_decimal(row.get("netProfit")),
        _parse_decimal(row.get("epsTTM")),
        _parse_decimal(row.get("MBRevenue")),
        _parse_decimal(row.get("totalShare")),
        _parse_decimal(row.get("liqaShare")),
    )


def sync_profit(
    universe: str | list[str] = "hs300_history",
    start_year: int = 2018,
    start_quarter: int = 1,
    end: date | None = None,
    flush_every: int = 200,
) -> dict:
    """主入口：按 (universe × quarter) 拉 query_profit_data，批量 upsert。

    需在 ``baostock_session()`` 内调用。返回 dict 含 ``upserted / empty / errors / total``。
    """
    if end is None:
        end = date.today()

    symbols = _resolve_universe(universe)
    log.info(
        "sync_profit start: universe=%s symbols=%d window=%dQ%d..%s",
        universe if isinstance(universe, str) else f"explicit({len(symbols)})",
        len(symbols),
        start_year,
        start_quarter,
        end.isoformat(),
    )

    quarters = list(_iter_quarters(start_year, start_quarter, end))
    total_calls = len(symbols) * len(quarters)
    upserted = 0
    empty = 0
    errors = 0
    processed = 0

    batch: list[tuple] = []

    def _flush() -> int:
        nonlocal batch
        if not batch:
            return 0
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.executemany(_UPSERT_SQL, batch)
            c.commit()
        n = len(batch)
        batch = []
        return n

    for sym_idx, symbol in enumerate(symbols, start=1):
        try:
            sym = normalize_symbol(symbol)
        except ValueError:
            log.warning("skip non-normalizable symbol: %r", symbol)
            continue

        for year, quarter in quarters:
            processed += 1
            row, err = _fetch_profit_one(sym, year, quarter)
            if err:
                errors += 1
                log.warning(
                    "query_profit_data failed: %s %dQ%d %s", sym, year, quarter, err
                )
                continue
            if row is None:
                empty += 1
                continue
            t = _row_to_tuple(sym, row)
            if t is None:
                empty += 1
                continue
            batch.append(t)
            upserted += 1
            if len(batch) >= flush_every:
                _flush()

        # 每 50 个 symbol 记一次进度
        if sym_idx % 50 == 0:
            log.info(
                "sync_profit progress: %d/%d symbols, processed=%d/%d, "
                "upserted=%d empty=%d errors=%d",
                sym_idx,
                len(symbols),
                processed,
                total_calls,
                upserted,
                empty,
                errors,
            )

    _flush()

    summary = {
        "symbols": len(symbols),
        "quarters": len(quarters),
        "total_calls": total_calls,
        "upserted": upserted,
        "empty": empty,
        "errors": errors,
    }
    log.info("sync_profit done: %s", summary)
    return summary
