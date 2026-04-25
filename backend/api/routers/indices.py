"""指数成分历史只读 API：服务于 ``/data/indices`` 浏览器页。

# 路由

- ``GET /api/indices/list`` — 所有指数概览（活跃数 / 调整次数 / 时间窗）
- ``GET /api/indices/current?index_code=000300.SH`` — 当前活跃成分（join 行业 + name）
- ``GET /api/indices/adjustments?index_code=000300.SH`` — 调整时间轴
  + 每次进/出明细
- ``GET /api/indices/symbol_membership?symbol=600519.SH`` — 单股在所有指数中的
  进出历史（用户在 current 表点一只票时查）

# 设计取舍

- **path 参数 vs query**：index_code 含点号 ``000300.SH``，path 参数虽支持但容易引
  歧义；统一走 query。
- **adjustments 一次返回全部**：HS300 11 年也就 ~23 次调整，每次 entries/departures
  数量 < 30，total payload < 50KB；前端拿到一次性渲染时间轴比分页友好。
- **行业 join 用 LEFT JOIN**：fr_industry_current 可能尚未同步全集，缺失 → NULL；
  前端用 "未分类" 兜底。
"""
from __future__ import annotations

import logging
import re

import pymysql
from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/indices", tags=["indices"])


# index_code 严格白名单：QMT 格式，6 数字 + . + 大写后缀，避免 SQL 注入兜底。
_INDEX_CODE_RE = re.compile(r"^\d{6}\.[A-Z]{2}$")
# symbol 同样的格式
_SYMBOL_RE = re.compile(r"^\d{6}\.[A-Z]{2}$")


def _validate_index_code(code: str) -> str:
    if not _INDEX_CODE_RE.match(code or ""):
        raise HTTPException(
            status_code=400,
            detail=f"index_code 必须是 QMT 格式（如 000300.SH），收到 {code!r}",
        )
    return code


def _validate_symbol(sym: str) -> str:
    if not _SYMBOL_RE.match(sym or ""):
        raise HTTPException(
            status_code=400,
            detail=f"symbol 必须是 QMT 格式（如 600519.SH），收到 {sym!r}",
        )
    return sym


# ---------------------------- /api/indices/list ----------------------------


@router.get("/list")
def list_indices() -> dict:
    """所有指数的概览：活跃数、历次调整、首尾调整日。

    Response::

        [
          {"index_code": "000300.SH", "active": 300, "adjustments": 23, ...},
          ...
        ]
    """
    with mysql_conn() as c:
        with c.cursor() as cur:
            try:
                cur.execute(
                    "SELECT index_code, "
                    "  SUM(CASE WHEN end_date IS NULL THEN 1 ELSE 0 END) AS active, "
                    "  COUNT(DISTINCT effective_date) AS adjustments, "
                    "  MIN(effective_date) AS first_adjustment, "
                    "  MAX(effective_date) AS last_adjustment "
                    "FROM fr_index_constituent "
                    "GROUP BY index_code "
                    "ORDER BY index_code"
                )
                rows = cur.fetchall()
            except pymysql.err.ProgrammingError:
                rows = []

    out = []
    for r in rows:
        out.append({
            "index_code": r["index_code"],
            "active": int(r["active"] or 0),
            "adjustments": int(r["adjustments"] or 0),
            "first_adjustment": str(r["first_adjustment"]) if r["first_adjustment"] else None,
            "last_adjustment": str(r["last_adjustment"]) if r["last_adjustment"] else None,
        })
    return ok(out)


# ---------------------------- /api/indices/current ----------------------------


@router.get("/current")
def current_constituents(index_code: str) -> dict:
    """指定指数的当前活跃成分（end_date IS NULL）+ name + 行业归属。

    Response::

        {
          "index_code": "000300.SH",
          "as_of": "2026-04-25",  # 当前查询时间
          "items": [
            {"symbol": "600519.SH", "name": "贵州茅台", "industry_l1": "C13酒、饮料和精制茶制造业",
             "effective_date": "2024-12-15"},
            ...
          ]
        }
    """
    code = _validate_index_code(index_code)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ic.symbol, "
                "  ins.name AS name, "
                "  COALESCE(NULLIF(ind.industry_l1, ''), '未分类') AS industry_l1, "
                "  ic.effective_date "
                "FROM fr_index_constituent ic "
                "LEFT JOIN fr_instrument ins ON ic.symbol = ins.symbol "
                "LEFT JOIN fr_industry_current ind ON ic.symbol = ind.symbol "
                "WHERE ic.index_code = %s AND ic.end_date IS NULL "
                "ORDER BY ic.symbol",
                (code,),
            )
            rows = cur.fetchall()

    items = [{
        "symbol": r["symbol"],
        "name": r["name"] or "",
        "industry_l1": r["industry_l1"],
        "effective_date": str(r["effective_date"]) if r["effective_date"] else None,
    } for r in rows]
    return ok({"index_code": code, "items": items, "count": len(items)})


# ---------------------------- /api/indices/adjustments ----------------------------


@router.get("/adjustments")
def adjustments_timeline(index_code: str) -> dict:
    """指定指数的所有调整事件：每次调整的 effective_date 进入名单 + 离开名单。

    数据来源：``fr_index_constituent`` 行的 ``effective_date`` = 进入日，
    ``end_date`` = 离开日（NULL 为仍在）。所以一次调整日 ``D``：
    - 进入 = 该指数 ``effective_date = D`` 的所有 row.symbol
    - 离开 = 该指数 ``end_date = D`` 的所有 row.symbol

    Response::

        {
          "index_code": "000300.SH",
          "events": [
            {"date": "2024-06-15", "entries": [{"symbol":..,"name":..}], "departures": [...]},
            ...
          ]
        }
    """
    code = _validate_index_code(index_code)
    with mysql_conn() as c:
        with c.cursor() as cur:
            # 拿到所有 distinct 调整日（进入或离开）
            cur.execute(
                "SELECT DISTINCT effective_date AS d FROM fr_index_constituent "
                "WHERE index_code = %s "
                "UNION "
                "SELECT DISTINCT end_date AS d FROM fr_index_constituent "
                "WHERE index_code = %s AND end_date IS NOT NULL "
                "ORDER BY d",
                (code, code),
            )
            dates = [r["d"] for r in cur.fetchall() if r["d"] is not None]

            # entries: effective_date = D 的 (symbol, name)；JOIN fr_instrument 拿名
            cur.execute(
                "SELECT ic.effective_date AS d, ic.symbol, ins.name AS name "
                "FROM fr_index_constituent ic "
                "LEFT JOIN fr_instrument ins ON ic.symbol = ins.symbol "
                "WHERE ic.index_code = %s",
                (code,),
            )
            entries_by_date: dict = {}
            for r in cur.fetchall():
                entries_by_date.setdefault(r["d"], []).append({
                    "symbol": r["symbol"],
                    "name": r["name"] or "",
                })

            # departures: end_date = D 的 (symbol, name)
            cur.execute(
                "SELECT ic.end_date AS d, ic.symbol, ins.name AS name "
                "FROM fr_index_constituent ic "
                "LEFT JOIN fr_instrument ins ON ic.symbol = ins.symbol "
                "WHERE ic.index_code = %s AND ic.end_date IS NOT NULL",
                (code,),
            )
            departures_by_date: dict = {}
            for r in cur.fetchall():
                departures_by_date.setdefault(r["d"], []).append({
                    "symbol": r["symbol"],
                    "name": r["name"] or "",
                })

    events = []
    for d in dates:
        events.append({
            "date": str(d),
            "entries": sorted(entries_by_date.get(d, []), key=lambda x: x["symbol"]),
            "departures": sorted(departures_by_date.get(d, []), key=lambda x: x["symbol"]),
        })
    return ok({"index_code": code, "events": events})


# ---------------------------- /api/indices/symbol_membership ----------------------------


@router.get("/symbol_membership")
def symbol_membership(symbol: str) -> dict:
    """某只股票在所有指数中的历次进出记录（"被收录历史"）。

    Response::

        {
          "symbol": "600519.SH",
          "name": "贵州茅台",
          "memberships": [
            {"index_code": "000300.SH", "effective_date": "2015-06-30", "end_date": null},
            ...
          ]
        }
    """
    sym = _validate_symbol(symbol)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT name FROM fr_instrument WHERE symbol = %s", (sym,))
            row = cur.fetchone()
            name = row["name"] if row else ""

            cur.execute(
                "SELECT index_code, effective_date, end_date "
                "FROM fr_index_constituent "
                "WHERE symbol = %s "
                "ORDER BY index_code, effective_date",
                (sym,),
            )
            memberships = [{
                "index_code": r["index_code"],
                "effective_date": str(r["effective_date"]) if r["effective_date"] else None,
                "end_date": str(r["end_date"]) if r["end_date"] else None,
            } for r in cur.fetchall()]

    return ok({"symbol": sym, "name": name, "memberships": memberships})
