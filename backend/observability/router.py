"""Prometheus 抓取端点：``GET /metrics``（根路径，非 /api）。

与现有 ``/api/...`` 因子指标 API 区分开——这里暴露的是运维监控指标（任务计数、
时延、数据健康），供 Prometheus 周期抓取。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from backend.observability.metrics import REGISTRY

router = APIRouter(tags=["observability"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    """渲染全局 REGISTRY 为 Prometheus 文本格式（version=0.0.4）。"""
    return REGISTRY.render()
