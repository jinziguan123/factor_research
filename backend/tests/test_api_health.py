"""``GET /api/health`` 端点的冒烟测试（纯单元，无需数据库）。

验证点：
- HTTP 200；
- 响应 envelope ``{"code": 0, "data": {"status": "ok"}}``。
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_api_health_returns_ok_envelope():
    # 延迟 import，避免模块级 side effect（startup 连接数据库）污染其它测试。
    from backend.api.main import app

    # 不用 ``with TestClient(app)``：health 不需要 startup 做扫描 / 起 watchdog，
    # 直接 GET 更快、且避免把 watchdog 线程泄漏给同进程里其他测试。
    c = TestClient(app)
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "ok"
