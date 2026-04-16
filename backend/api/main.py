"""FastAPI 应用入口（最小骨架）。

当前仅暴露 ``GET /api/health`` 用于冒烟；Task 9 会把因子 / 任务 / 评估等路由挂载到此处。
所有业务响应遵循 ``{"code": 0, "data": ...}`` 的统一契约，便于前端简单判错。
"""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="Factor Research Backend",
    version="0.1.0",
    description="因子研究平台后端 API 骨架。",
)


@app.get("/api/health")
def health() -> dict:
    """健康检查：部署 / 容器探针使用，不依赖任何外部资源。"""
    return {"code": 0, "data": {"status": "ok"}}
