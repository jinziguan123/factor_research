"""FastAPI 应用入口。

职责：
- 创建 ``FastAPI`` 实例 + 统一 CORS；
- ``startup`` 钩子：跑一次 ``FactorRegistry.scan_and_register``，并按配置开启 watchdog；
- ``shutdown`` 钩子：停 watchdog + 释放 ProcessPool；
- 挂载所有子路由（factors / pools / evals / backtests / bars / admin）；
- 全局异常 handler：把 FastAPI ``HTTPException`` 转成 ``{"code","message"}``，
  未知异常转 500，确保前端判错只看 ``res.code``。

所有业务响应遵循 ``{"code": 0, "data": ...}`` 的统一契约。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.routers import (
    admin,
    backtests,
    bars,
    compositions,
    cost_sensitivity,
    evals,
    factor_assistant,
    factors,
    param_sensitivity,
    pools,
    symbols,
)
from backend.api.schemas import ok
from backend.config import settings
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.hot_reload import start_hot_reload

logging.basicConfig(level=settings.log_level)
log = logging.getLogger(__name__)

app = FastAPI(
    title="Factor Research Backend",
    version="0.1.0",
    description="因子研究平台后端 API。",
)

# CORS：开发态把 Vite 两个端口放进白名单；生产部署应在反向代理层限制。
# 注意：allow_origins=["*"] 与 allow_credentials=True 不兼容（浏览器规范），
# 这里明确 allow_credentials=False，保持兼容。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "*",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ---------------------------- 生命周期 ----------------------------


@app.on_event("startup")
def _startup() -> None:
    """启动钩子：扫描因子 + 可选开启热加载。

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


@app.on_event("shutdown")
def _shutdown() -> None:
    """关闭钩子：停 watchdog + 优雅释放 ProcessPool。"""
    obs = getattr(app.state, "observer", None)
    if obs is not None:
        try:
            obs.stop()
            obs.join(timeout=2)
        except Exception:  # noqa: BLE001
            log.exception("stop watchdog failed")
    # ProcessPool 单例只有在被用过时 _pool 非空；shutdown(wait=False) 不阻塞关闭流程。
    # 从内部 import 而不是模块顶 import：test 环境可能 monkeypatch 这个模块。
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
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
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
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail
            if isinstance(exc.detail, str)
            else str(exc.detail),
        },
    )


@app.exception_handler(Exception)
async def _general_exception_handler(request: Request, exc: Exception):
    """兜底任何未预期异常。打 traceback 日志，前端看到统一 500。"""
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "Internal Server Error"},
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
app.include_router(bars.router)
app.include_router(admin.router)
app.include_router(factor_assistant.router)
