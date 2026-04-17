"""``/api/factors`` 路由的集成测试。

依赖：MySQL 可连通（startup 会扫描写 ``fr_factor_meta``）。
覆盖：
- ``GET /api/factors`` 列出所有注册因子，至少包含 Task 5 的 4 个内置因子；
- ``GET /api/factors/{factor_id}`` 正常返回单个因子详情；
- ``GET /api/factors/{factor_id}`` 对未注册因子返回 404 且 envelope 为 ``{"code":404, ...}``。

``with TestClient(app) as c`` 会触发 FastAPI ``@app.on_event("startup")``，
从而执行 ``FactorRegistry().scan_and_register()``；不用 ``with`` 块的话
startup 不跑，registry 会空，断言会失败。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_list_factors_after_startup():
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get("/api/factors")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    ids = {f["factor_id"] for f in body["data"]}
    assert {
        "reversal_n",
        "momentum_n",
        "realized_vol",
        "turnover_ratio",
    }.issubset(ids)


def test_get_single_factor():
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get("/api/factors/reversal_n")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["factor_id"] == "reversal_n"
    # 前端表单依赖 params_schema 自动生成，这里做契约断言。
    assert "params_schema" in body["data"]


def test_get_factor_not_found_returns_standard_envelope():
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get("/api/factors/__nonexistent__")
    assert r.status_code == 404
    body = r.json()
    # 全局异常 handler 把 HTTPException 转成 ``{code, message}`` 统一格式。
    assert body["code"] == 404
    assert "message" in body
