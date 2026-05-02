"""FastAPI 应用入口。

职责：
- 创建 ``FastAPI`` 实例 + 统一 CORS；
- 请求级 ``x-request-id`` 中间件（日志追踪 + 关联前端调用链）；
- ``startup`` 钩子：跑一次 ``FactorRegistry.scan_and_register``，并按配置开启 watchdog；
- ``shutdown`` 钩子：停 watchdog + 释放 ProcessPool；
- 挂载所有子路由（factors / pools / evals / backtests / bars / admin）；
- 全局异常 handler：把 FastAPI ``HTTPException`` 转成 ``{"code","message"}``，
  未知异常转 500，确保前端判错只看 ``res.code``。

所有业务响应遵循 ``{"code": 0, "data": ...}`` 的统一契约。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.routers import (
    admin,
    backtests,
    bars,
    compositions,
    cost_sensitivity,
    data_health,
    evals,
    factor_assistant,
    factors,
    fundamentals,
    indices,
    param_sensitivity,
    pools,
    signal_subscriptions,
    signals,
    symbols,
)
from backend.api.schemas import ok
from backend.config import settings
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.hot_reload import start_hot_reload

# -- 日志初始化 -----------------------------------------------------------

if settings.log_json:
    _stream = logging.StreamHandler()
    _stream.setFormatter(
        logging.Formatter(
            json.dumps({
                "ts": "%(asctime)s",
                "level": "%(levelname)s",
                "name": "%(name)s",
                "msg": "%(message)s",
                "req_id": "%(req_id)s",
            })
        )
    )
    logging.basicConfig(level=settings.log_level, handlers=[_stream])
else:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
log = logging.getLogger(__name__)

app = FastAPI(
    title="Factor Research Backend",
    version="0.1.0",
    description="因子研究平台后端 API。",
)

# -- 中间件（栈序 = 后 add 的先执行） -----------------------------------------

# 1. 请求体大小限制（拒绝 > 10 MiB 的请求）
_MAX_BODY_BYTES = 10 * 1024 * 1024


@app.middleware("http")
async def _body_size_limit(request: Request, call_next: Callable):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"code": 413, "message": "Request body too large (max 10 MiB)"},
        )
    return await call_next(request)


# 2. 请求追踪：注入 request-id + 记录耗时
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        req_id = request.headers.get("x-request-id", str(uuid.uuid4())[:12])
        request.state.req_id = req_id
        t0 = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.monotonic() - t0) * 1000
            log.error(
                "%s %s | req=%s | %.0fms | unhandled",
                request.method,
                request.url.path,
                req_id,
                elapsed,
            )
            raise
        elapsed = (time.monotonic() - t0) * 1000
        log.info(
            "%s %s | req=%s | %d | %.0fms",
            request.method,
            request.url.path,
            req_id,
            response.status_code,
            elapsed,
        )
        response.headers["x-request-id"] = req_id
        return response


app.add_middleware(RequestIdMiddleware)

# CORS：开发态把 Vite 两个端口放进白名单；生产部署应在反向代理层限制。
# 注意：allow_origins=["*"] 与 allow_credentials=True 不兼容（浏览器规范），
# 这里明确 allow_credentials=False，保持兼容。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ---------------------------- 生命周期 ----------------------------


@app.on_event("startup")
def _startup() -> None:
    """启动钩子：扫描因子 + 可选开启热加载 + 可选启动 live_market worker。

    FastAPI 在 3.10 下仍然支持 ``@app.on_event``（lifespan 是更新做法，但 on_event
    足够表达且兼容 TestClient 的 ``with`` 语义）。
    """
    # 扫描注册内置因子；失败（DB 挂）会抛异常让服务 fail fast。
    FactorRegistry().scan_and_register()
    if settings.hot_reload:
        try:
            app.state.observer = start_hot_reload(Path(settings.factors_dir))
        except Exception:  # noqa: BLE001
            # 热加载是锦上添花；启动期 watchdog 起不来不应阻断服务。
            log.exception(
                "start_hot_reload failed (factors_dir=%s); continue without watchdog",
                settings.factors_dir,
            )

    # live_market worker（嵌入式 daemon thread）——订阅驱动的实盘行情采集
    if settings.live_market_worker_enabled:
        try:
            from backend.workers.live_market import (
                LiveMarketConfig,
                start_in_thread,
            )

            cfg = LiveMarketConfig(
                archive_1m_enabled=settings.live_market_archive_1m,
            )
            thread, stop_event = start_in_thread(cfg)
            app.state.live_market_thread = thread
            app.state.live_market_stop_event = stop_event
            log.info(
                "live_market worker started in background thread (archive_1m=%s)",
                settings.live_market_archive_1m,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "live_market worker startup failed; "
                "set FR_LIVE_MARKET_WORKER=0 to disable"
            )


@app.on_event("shutdown")
def _shutdown() -> None:
    """关闭钩子：停 worker + watchdog + 优雅释放 ProcessPool。"""
    # live_market worker：set stop_event 让主循环 1s 内退出，再 join
    stop_event = getattr(app.state, "live_market_stop_event", None)
    thread = getattr(app.state, "live_market_thread", None)
    if stop_event is not None and thread is not None:
        try:
            stop_event.set()
            thread.join(timeout=10)
            if thread.is_alive():
                log.warning("live_market worker did not stop in 10s")
            else:
                log.info("live_market worker stopped cleanly")
        except Exception:  # noqa: BLE001
            log.exception("stop live_market worker failed")

    obs = getattr(app.state, "observer", None)
    if obs is not None:
        try:
            obs.stop()
            obs.join(timeout=2)
        except Exception:  # noqa: BLE001
            log.exception("stop watchdog failed")
    # ProcessPool 单例只有在被用过时 _pool 非空；shutdown(wait=False) 不阻塞关闭流程。
    try:
        from backend.runtime import task_pool as tp

        if tp._pool is not None:  # noqa: SLF001
            tp._pool.shutdown(wait=False, cancel_futures=False)  # noqa: SLF001
            tp._pool = None  # noqa: SLF001
    except Exception:  # noqa: BLE001
        log.exception("shutdown task_pool failed")


# ---------------------------- 全局异常 handler ----------------------------


@app.exception_handler(FastAPIHTTPException)
async def _http_exception_handler(request: Request, exc: FastAPIHTTPException):
    """把 FastAPI/Starlette ``HTTPException`` 统一成 ``{"code","message"}``。

    - ``status_code`` 作 ``code``，与 HTTP 状态 1:1；
    - ``detail`` 作 ``message``；不附加 traceback，避免把实现细节暴露给前端。
    """
    req_id = getattr(request.state, "req_id", "-")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            "req_id": req_id,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def _starlette_http_exception_handler(
    request: Request, exc: StarletteHTTPException
):
    """兜底 Starlette 层的 404 / 405（例如 路径不存在）。

    FastAPI ``HTTPException`` 继承自 Starlette ``HTTPException``，理论上前一个 handler
    就能接住。但 Starlette 源头抛出的（如 method not allowed）会走这里。
    """
    req_id = getattr(request.state, "req_id", "-")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail
            if isinstance(exc.detail, str)
            else str(exc.detail),
            "req_id": req_id,
        },
    )


@app.exception_handler(Exception)
async def _general_exception_handler(request: Request, exc: Exception):
    """兜底任何未预期异常。打 traceback 日志，前端看到统一 500。"""
    req_id = getattr(request.state, "req_id", "-")
    log.exception("unhandled error on %s | req=%s", request.url.path, req_id)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "Internal Server Error", "req_id": req_id},
    )


# ---------------------------- 路由挂载 ----------------------------


@app.get("/api/health")
def health() -> dict:
    """健康检查：探针使用，不依赖任何外部资源。"""
    return ok({"status": "ok"})


app.include_router(factors.router)
app.include_router(pools.router)
app.include_router(symbols.router)
app.include_router(evals.router)
app.include_router(backtests.router)
app.include_router(cost_sensitivity.router)
app.include_router(param_sensitivity.router)
app.include_router(compositions.router)
app.include_router(signals.router)
app.include_router(signal_subscriptions.router)
app.include_router(bars.router)
app.include_router(admin.router)
app.include_router(data_health.router)
app.include_router(indices.router)
app.include_router(fundamentals.router)
app.include_router(factor_assistant.router)
