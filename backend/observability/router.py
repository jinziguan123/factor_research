"""Prometheus 抓取端点：``GET /metrics``（根路径，非 /api）。

与现有 ``/api/...`` 因子指标 API 区分开——这里暴露的是运维监控指标（任务计数、
时延、数据健康），供 Prometheus 周期抓取。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from backend.observability.db_metrics import render_db_metrics
from backend.observability.metrics import REGISTRY

router = APIRouter(tags=["observability"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    """Prometheus 文本（version 0.0.4）：任务指标从 MySQL 派生（跨进程一致），
    叠加进程内 REGISTRY（未来主进程指标）。"""
    return render_db_metrics() + REGISTRY.render()
