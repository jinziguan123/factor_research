"""数据健康度只读 API：跨表 meta 视图，专为前端仪表盘服务。

# 用途

支撑 ``/data/health`` 页：一页看清所有同步表的"行数 / 时间范围 / 最新更新时间"
+ profit 数据按行业的缺失率 + profit 按季度的覆盖趋势 + 指数成分概览。

# 设计取舍

- **聚合在 SQL 层做**：行数 / 缺失率都是单 SQL 一把出，避免 Python 循环；
  fr_fundamental_profit 即使涨到 100 万行，按行业 group by 也是秒级。
- **Decimal → float 显式转换**：PyMySQL 返回 Decimal，FastAPI 默认序列化成字符串，
  前端 echarts 不能直接用。这里在出口处统一 float()。
- **错误容忍**：如果某张表还没建（比如 fr_fundamental_profit 在用户跑迁移之前），
  ProgrammingError 单独 catch 成空 row，不让整个仪表盘 500。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import pymysql
from fastapi import APIRouter

from backend.api.schemas import ok
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data_health", tags=["data_health"])


# ---- 仪表盘 1：所有同步表元信息（行数 / 时间范围 / 最新 updated_at） ----


# 描述每张要展示的表：(table_name, label, time_col, optional_extra_select)
# - time_col：用来取 min/max 时间范围；None = 不取
# - extra_sql：附加的 SELECT 表达式（如 distinct symbol 数）；None = 跳过
_TABLES: list[tuple[str, str, str | None, str | None]] = [
    ("fr_instrument", "标的全集", None, "COUNT(DISTINCT data_source) AS source_kinds"),
    ("fr_trade_calendar", "交易日历", "trade_date", None),
    ("fr_industry_current", "当前行业归属", "snapshot_date", None),
    ("fr_index_constituent", "指数成分历史", "effective_date",
     "COUNT(DISTINCT index_code) AS indices"),
    ("fr_fundamental_profit", "财报 PIT", "report_date",
     "COUNT(DISTINCT symbol) AS symbols"),
]


def _safe_table_meta(
    cur, table: str, label: str, time_col: str | None, extra_sql: str | None
) -> dict:
    """单张表的 meta 探测；表不存在 → 返回 missing=True。"""
    try:
        # 行数 + updated_at 最新值（updated_at 不存在则跳过）。
        # 别名用 cnt 而非 rows，避免命中 MySQL 保留字。
        cur.execute(f"SELECT COUNT(*) AS cnt FROM `{table}`")
        rows = cur.fetchone()["cnt"]

        latest_updated_at = None
        try:
            cur.execute(f"SELECT MAX(updated_at) AS m FROM `{table}`")
            latest_updated_at = cur.fetchone()["m"]
        except pymysql.err.OperationalError:
            # 表没 updated_at 列（如 fr_trade_calendar 可能不带），忽略
            pass

        time_min = None
        time_max = None
        if time_col:
            cur.execute(f"SELECT MIN(`{time_col}`) AS mn, MAX(`{time_col}`) AS mx FROM `{table}`")
            r = cur.fetchone()
            time_min, time_max = r["mn"], r["mx"]

        extra: dict[str, Any] = {}
        if extra_sql:
            cur.execute(f"SELECT {extra_sql} FROM `{table}`")
            extra = {k: int(v) if isinstance(v, Decimal) else v
                     for k, v in cur.fetchone().items()}

        return {
            "table": table,
            "label": label,
            "missing": False,
            "rows": int(rows),
            "latest_updated_at": str(latest_updated_at) if latest_updated_at else None,
            "time_min": str(time_min) if time_min else None,
            "time_max": str(time_max) if time_max else None,
            "extra": extra,
        }
    except pymysql.err.ProgrammingError as e:
        # 表不存在（1146 错误）→ 给前端一个 "missing" 占位卡片
        log.info("data_health: table %s missing (%s)", table, e)
        return {
            "table": table,
            "label": label,
            "missing": True,
            "rows": 0,
            "latest_updated_at": None,
            "time_min": None,
            "time_max": None,
            "extra": {},
        }


@router.get("/summary")
def get_summary() -> dict:
    """返回所有同步表的 meta + 指数成分概览。

    Response::

        {
          "tables": [
            {"table": "fr_instrument", "label": "标的全集", "rows": 5500, ...},
            ...
          ],
          "index_summary": [
            {"index_code": "000300.SH", "active": 300, "adjustments": 23, "last_adjustment": "..."},
            ...
          ]
        }
    """
    with mysql_conn() as c:
        with c.cursor() as cur:
            tables = [_safe_table_meta(cur, t, lbl, tc, ex) for t, lbl, tc, ex in _TABLES]

            # 指数成分概览（active 数 / 历次调整 / 最近一次调整）
            index_summary: list[dict] = []
            try:
                cur.execute(
                    "SELECT index_code, "
                    "  SUM(CASE WHEN end_date IS NULL THEN 1 ELSE 0 END) AS active, "
                    "  COUNT(DISTINCT effective_date) AS adjustments, "
                    "  MAX(effective_date) AS last_adjustment, "
                    "  MIN(effective_date) AS first_adjustment "
                    "FROM fr_index_constituent "
                    "GROUP BY index_code "
                    "ORDER BY index_code"
                )
                for r in cur.fetchall():
                    index_summary.append({
                        "index_code": r["index_code"],
                        "active": int(r["active"] or 0),
                        "adjustments": int(r["adjustments"] or 0),
                        "first_adjustment": str(r["first_adjustment"]) if r["first_adjustment"] else None,
                        "last_adjustment": str(r["last_adjustment"]) if r["last_adjustment"] else None,
                    })
            except pymysql.err.ProgrammingError:
                pass

    return ok({"tables": tables, "index_summary": index_summary})


# ---- 仪表盘 2：profit 字段缺失率（按行业分组） ----


# 数值字段列表：缺失率分析就看这几个；roe_avg / np_margin 通常齐全，
# gp_margin / mb_revenue 在金融股 / 季报不全的情况下会缺很多。
_PROFIT_NULLABLE_FIELDS = [
    "roe_avg", "np_margin", "gp_margin",
    "net_profit", "eps_ttm", "mb_revenue",
    "total_share", "liqa_share",
]


@router.get("/profit_coverage")
def get_profit_coverage() -> dict:
    """profit 表按行业分组的字段缺失率 + 按季度的记录数趋势。

    Response::

        {
          "by_industry": [
            {"industry_l1": "银行", "total": 56, "null_rates": {"gp_margin": 1.0, ...}},
            ...
          ],
          "by_quarter": [
            {"report_date": "2018-03-31", "symbols": 290},
            ...
          ],
          "fields": ["roe_avg", "np_margin", ...]
        }
    """
    null_count_sql = ", ".join(
        f"SUM(CASE WHEN p.{f} IS NULL THEN 1 ELSE 0 END) AS `{f}_null`"
        for f in _PROFIT_NULLABLE_FIELDS
    )

    with mysql_conn() as c:
        with c.cursor() as cur:
            # 按行业 join；industry 缺失（symbol 不在 fr_industry_current）→ "未分类"
            try:
                cur.execute(
                    f"SELECT COALESCE(NULLIF(i.industry_l1, ''), '未分类') AS industry_l1, "
                    f"  COUNT(*) AS total, {null_count_sql} "
                    f"FROM fr_fundamental_profit p "
                    f"LEFT JOIN fr_industry_current i ON p.symbol = i.symbol "
                    f"GROUP BY industry_l1 "
                    f"ORDER BY total DESC"
                )
                rows = cur.fetchall()
            except pymysql.err.ProgrammingError:
                rows = []

            by_industry: list[dict] = []
            for r in rows:
                total = int(r["total"])
                null_rates = {}
                for f in _PROFIT_NULLABLE_FIELDS:
                    nulls = int(r[f"{f}_null"])
                    null_rates[f] = round(nulls / total, 4) if total else 0.0
                by_industry.append({
                    "industry_l1": r["industry_l1"],
                    "total": total,
                    "null_rates": null_rates,
                })

            # 按季度看 distinct symbol 数（看是不是越早越稀疏 / 最新季报覆盖率）
            by_quarter: list[dict] = []
            try:
                cur.execute(
                    "SELECT report_date, COUNT(DISTINCT symbol) AS symbols "
                    "FROM fr_fundamental_profit "
                    "GROUP BY report_date "
                    "ORDER BY report_date"
                )
                for r in cur.fetchall():
                    by_quarter.append({
                        "report_date": str(r["report_date"]),
                        "symbols": int(r["symbols"]),
                    })
            except pymysql.err.ProgrammingError:
                pass

    return ok({
        "by_industry": by_industry,
        "by_quarter": by_quarter,
        "fields": _PROFIT_NULLABLE_FIELDS,
    })
