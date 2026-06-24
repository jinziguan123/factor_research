"""可观测性：零依赖的 Prometheus 指标导出 + /metrics 端点。"""
from backend.observability.metrics import (
    DATA_HEALTH,
    REGISTRY,
    TASK_DURATION,
    TASK_TOTAL,
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    observe_task,
)

__all__ = [
    "REGISTRY",
    "MetricsRegistry",
    "Counter",
    "Gauge",
    "Histogram",
    "TASK_TOTAL",
    "TASK_DURATION",
    "DATA_HEALTH",
    "observe_task",
]
