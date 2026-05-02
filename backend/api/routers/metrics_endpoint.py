"""Prometheus 兼容指标端点。

GET /api/metrics 返回文本格式指标，可直接被 Prometheus / Grafana 抓取。
"""

from __future__ import annotations

import time as time_mod

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api", tags=["metrics"])

# 应用启动时间
_start_ts = time_mod.monotonic()


def _collect_metrics() -> str:
    """收集当前进程的基础指标（无外部依赖）。"""
    import os
    import threading

    lines: list[str] = [
        "# HELP fr_uptime_seconds 应用运行时间（秒）",
        "# TYPE fr_uptime_seconds gauge",
        f"fr_uptime_seconds {time_mod.monotonic() - _start_ts:.1f}",
        "# HELP fr_thread_count 当前进程线程数",
        "# TYPE fr_thread_count gauge",
        f"fr_thread_count {threading.active_count()}",
    ]

    # 进程池状态（如果存在）
    try:
        from backend.runtime.task_pool import _pool

        if _pool is not None:  # noqa: SLF001
            lines.append("# HELP fr_task_pool_workers 进程池 worker 数")
            lines.append("# TYPE fr_task_pool_workers gauge")
            lines.append(f"fr_task_pool_workers {_pool._max_workers}")  # noqa: SLF001
    except Exception:
        pass

    # live_market 熔断器状态
    try:
        from backend.workers.live_market import _spot_cb

        cb_open = 1 if not _spot_cb.allow() else 0
        lines.append("# HELP fr_live_market_circuit_breaker_open 实盘行情熔断器是否打开")
        lines.append("# TYPE fr_live_market_circuit_breaker_open gauge")
        lines.append(f"fr_live_market_circuit_breaker_open {cb_open}")
    except Exception:
        pass

    # 内存使用
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        lines.append("# HELP fr_memory_rss_bytes 进程 RSS 内存（字节）")
        lines.append("# TYPE fr_memory_rss_bytes gauge")
        lines.append(f"fr_memory_rss_bytes {mem.rss}")
    except Exception:
        pass

    return "\n".join(lines) + "\n"


@router.get("/metrics")
def get_metrics():
    """Prometheus 文本格式指标。"""
    return PlainTextResponse(_collect_metrics())
