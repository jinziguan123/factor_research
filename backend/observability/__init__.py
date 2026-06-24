"""可观测性：零依赖的 Prometheus 指标导出 + /metrics 端点。"""
from backend.observability.db_metrics import render_db_metrics
from backend.observability.metrics import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
)

__all__ = [
    "REGISTRY",
    "MetricsRegistry",
    "Counter",
    "Gauge",
    "Histogram",
    "render_db_metrics",
]
