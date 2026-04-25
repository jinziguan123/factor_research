"""财报数据探查只读 API：服务于 ``/data/fundamentals/profit`` 探查页。

# 路由

- ``GET /api/fundamentals/profit/quarters`` — 已入库的所有 report_date（含每季样本量）
- ``GET /api/fundamentals/profit/series?symbol=600519.SH`` — 单股 PIT 时间序列
  （5 指标：roe_avg / np_margin / gp_margin / eps_ttm / net_profit）
- ``GET /api/fundamentals/profit/cross_section?report_date=YYYY-MM-DD&metric=roe_avg&top=30``
  — 给定季度 + 指标，全市场 TopN + BottomN + 按行业聚合统计

# 设计取舍

- **Decimal → float**：PyMySQL 默认返 Decimal，FastAPI 序列化后前端 echarts 不能用，
  统一在出口 ``float()``。``None`` 透传，前端按需 connectNulls。
- **缺失季度也要返回 entries=0**：series 端不补缺，前端用稀疏曲线 + connectNulls；
  cross_section 端只看当季有数据的股票，total ≠ 全市场。
- **行业聚合**：cross_section 顺手按行业返回 mean/median/count，前端可以画一个
  "该季度行业 ROE 中位数" 条形图，比单纯 TopN 更有 context。
- **metric 白名单**：避免 SQL 注入。前端只能从既定列表选。
"""
from __future__ import annotations

import logging
import re
import statistics
from decimal import Decimal

import pymysql
from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])


# 允许暴露给前端的 profit 数值列；用作白名单防 SQL 注入。
# 顺序也作为前端 metric 选择器的默认顺序。
_PROFIT_METRICS: list[tuple[str, str]] = [
    ("roe_avg", "ROE（平均净资产收益率）"),
    ("np_margin", "净利率"),
    ("gp_margin", "毛利率"),
    ("eps_ttm", "EPS（TTM）"),
    ("net_profit", "归母净利润"),
]
_PROFIT_METRIC_KEYS = {k for k, _ in _PROFIT_METRICS}

_SYMBOL_RE = re.compile(r"^\d{6}\.[A-Z]{2}$")
# YYYY-MM-DD：交给 SQL 校验比正则更严格，但这里至少把奇形怪状挡掉
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_symbol(sym: str) -> str:
    if not _SYMBOL_RE.match(sym or ""):
        raise HTTPException(
            status_code=400,
            detail=f"symbol 必须是 QMT 格式（如 600519.SH），收到 {sym!r}",
        )
    return sym


def _validate_metric(m: str) -> str:
    if m not in _PROFIT_METRIC_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"metric 必须是 {sorted(_PROFIT_METRIC_KEYS)} 之一，收到 {m!r}",
        )
    return m


def _validate_date(d: str) -> str:
    if not _DATE_RE.match(d or ""):
        raise HTTPException(
            status_code=400,
            detail=f"report_date 必须是 YYYY-MM-DD 格式，收到 {d!r}",
        )
    return d


def _to_float(v) -> float | None:
    """Decimal → float；None 透传。"""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


# ---------------------------- /api/fundamentals/metrics ----------------------------


@router.get("/metrics")
def list_metrics() -> dict:
    """返回前端可用的指标列表（key + 中文 label）。"""
    return ok([{"key": k, "label": lbl} for k, lbl in _PROFIT_METRICS])


# ---------------------------- /api/fundamentals/profit/quarters ----------------------------


@router.get("/profit/quarters")
def list_quarters() -> dict:
    """所有已入库的 report_date + 每季度的样本量。倒序最新在前。

    Response::

        [
          {"report_date": "2026-03-31", "symbols": 172},
          {"report_date": "2025-12-31", "symbols": 472},
          ...
        ]
    """
    with mysql_conn() as c:
        with c.cursor() as cur:
            try:
                cur.execute(
                    "SELECT report_date, COUNT(DISTINCT symbol) AS symbols "
                    "FROM fr_fundamental_profit "
                    "GROUP BY report_date "
                    "ORDER BY report_date DESC"
                )
                rows = cur.fetchall()
            except pymysql.err.ProgrammingError:
                rows = []
    return ok([{
        "report_date": str(r["report_date"]),
        "symbols": int(r["symbols"]),
    } for r in rows])


# ---------------------------- /api/fundamentals/profit/series ----------------------------


@router.get("/profit/series")
def get_profit_series(symbol: str) -> dict:
    """单只股票的 PIT 财报时间序列。

    Response::

        {
          "symbol": "600519.SH",
          "name": "贵州茅台",
          "industry_l1": "C15酒、饮料和精制茶制造业",
          "metrics": [{"key": "roe_avg", "label": "ROE..."}, ...],
          "rows": [
            {"report_date": "2018-03-31", "announcement_date": "2018-04-25",
             "roe_avg": 0.0296, "np_margin": ..., "gp_margin": ...,
             "eps_ttm": ..., "net_profit": ...},
            ...
          ]
        }
    """
    sym = _validate_symbol(symbol)
    metric_cols = ", ".join(k for k, _ in _PROFIT_METRICS)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ins.name, "
                "  COALESCE(NULLIF(i.industry_l1, ''), '未分类') AS industry_l1 "
                "FROM fr_instrument ins "
                "LEFT JOIN fr_industry_current i ON ins.symbol = i.symbol "
                "WHERE ins.symbol = %s",
                (sym,),
            )
            meta = cur.fetchone() or {"name": "", "industry_l1": "未分类"}

            try:
                cur.execute(
                    f"SELECT report_date, announcement_date, {metric_cols} "
                    f"FROM fr_fundamental_profit "
                    f"WHERE symbol = %s "
                    f"ORDER BY report_date",
                    (sym,),
                )
                raw = cur.fetchall()
            except pymysql.err.ProgrammingError:
                raw = []

    rows = []
    for r in raw:
        item = {
            "report_date": str(r["report_date"]),
            "announcement_date": (
                str(r["announcement_date"]) if r["announcement_date"] else None
            ),
        }
        for k, _ in _PROFIT_METRICS:
            item[k] = _to_float(r[k])
        rows.append(item)

    return ok({
        "symbol": sym,
        "name": meta["name"] or "",
        "industry_l1": meta["industry_l1"],
        "metrics": [{"key": k, "label": lbl} for k, lbl in _PROFIT_METRICS],
        "rows": rows,
    })


# ---------------------------- /api/fundamentals/profit/cross_section ----------------------------


@router.get("/profit/cross_section")
def get_profit_cross_section(
    report_date: str,
    metric: str = "roe_avg",
    top: int = 30,
) -> dict:
    """指定季度 + 指标的截面：全市场 TopN / BottomN + 按行业聚合。

    Args:
        report_date: 报告期，如 ``2025-12-31``
        metric: 见 ``_PROFIT_METRICS``
        top: TopN / BottomN 各取多少条（夹紧到 [5, 100]）

    Response::

        {
          "report_date": "2025-12-31",
          "metric": "roe_avg",
          "metric_label": "ROE（平均净资产收益率）",
          "total": 472,
          "top": [{"symbol":..,"name":..,"industry_l1":..,"value":..}, ...],
          "bottom": [...],
          "by_industry": [
            {"industry_l1": "C15...", "count": 8, "mean": 0.34, "median": 0.31},
            ...
          ]
        }
    """
    rd = _validate_date(report_date)
    m = _validate_metric(metric)
    top_n = max(5, min(int(top), 100))

    with mysql_conn() as c:
        with c.cursor() as cur:
            try:
                # 一把拉齐当季所有非 null 的样本，Python 端再切 top/bottom + 行业聚合。
                # 当季样本 < 1k，全部回内存毫秒级。
                cur.execute(
                    f"SELECT p.symbol, ins.name, "
                    f"  COALESCE(NULLIF(i.industry_l1, ''), '未分类') AS industry_l1, "
                    f"  p.{m} AS value "
                    f"FROM fr_fundamental_profit p "
                    f"LEFT JOIN fr_instrument ins ON p.symbol = ins.symbol "
                    f"LEFT JOIN fr_industry_current i ON p.symbol = i.symbol "
                    f"WHERE p.report_date = %s AND p.{m} IS NOT NULL "
                    f"ORDER BY p.{m} DESC",
                    (rd,),
                )
                raw = cur.fetchall()
            except pymysql.err.ProgrammingError:
                raw = []

    items = [{
        "symbol": r["symbol"],
        "name": r["name"] or "",
        "industry_l1": r["industry_l1"],
        "value": _to_float(r["value"]),
    } for r in raw]

    total = len(items)
    top_items = items[:top_n]
    # bottom 取尾部并升序，方便阅读
    bottom_items = list(reversed(items[-top_n:])) if total >= top_n else []

    # 按行业聚合：count + mean + median；行业内样本 < 3 跳过 median 计算稳定性
    by_industry: list[dict] = []
    by_ind: dict[str, list[float]] = {}
    for it in items:
        if it["value"] is None:
            continue
        by_ind.setdefault(it["industry_l1"], []).append(it["value"])
    for ind, vals in by_ind.items():
        if not vals:
            continue
        by_industry.append({
            "industry_l1": ind,
            "count": len(vals),
            "mean": round(statistics.fmean(vals), 6),
            "median": round(statistics.median(vals), 6),
        })
    by_industry.sort(key=lambda x: x["median"], reverse=True)

    metric_label = next((lbl for k, lbl in _PROFIT_METRICS if k == m), m)

    return ok({
        "report_date": rd,
        "metric": m,
        "metric_label": metric_label,
        "total": total,
        "top": top_items,
        "bottom": bottom_items,
        "by_industry": by_industry,
    })
