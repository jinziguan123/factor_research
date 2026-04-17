"""``/api/pools`` 路由的集成测试。

依赖：MySQL 可连通（stock_pool / stock_pool_symbol / stock_symbol 均在测试库存在）。

测试使用 ``__test_`` 前缀隔离池名，autouse fixture 每次用例前后清理，避免污染。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _cleanup_test_pools():
    """删除所有 ``__test`` 前缀 + 本 owner_key 的测试池及成员。

    用 owner_key 过滤保证不误删生产 / timing_driven 维护的股票池。
    """
    from backend.config import settings
    from backend.storage.mysql_client import mysql_conn

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM stock_pool_symbol WHERE pool_id IN "
                "(SELECT pool_id FROM stock_pool "
                " WHERE owner_key=%s AND pool_name LIKE '__test%%')",
                (settings.owner_key,),
            )
            cur.execute(
                "DELETE FROM stock_pool "
                "WHERE owner_key=%s AND pool_name LIKE '__test%%'",
                (settings.owner_key,),
            )
        c.commit()


@pytest.fixture(autouse=True)
def _clean_pool_before_and_after():
    _cleanup_test_pools()
    yield
    _cleanup_test_pools()


def test_create_pool_and_fetch_with_symbols():
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/pools",
            json={
                "name": "__test_poolA",
                "description": "demo",
                # 这两个 symbol 由 seed_stock_symbol（docker-compose-test）灌入，
                # 若环境未种子，断言会失败提示 DBA。
                "symbols": ["000001.SZ", "000002.SZ"],
            },
        )
        assert r.status_code == 200, r.text
        pid = r.json()["data"]["pool_id"]
        assert isinstance(pid, int) and pid > 0

        r2 = c.get(f"/api/pools/{pid}")
    assert r2.status_code == 200
    data = r2.json()["data"]
    assert data["pool_name"] == "__test_poolA"
    assert len(data["symbols"]) == 2
    # 保序断言：create_pool 按 symbols 列表的入参顺序写 sort_order，读回应保持一致。
    assert [s["symbol"] for s in data["symbols"]] == ["000001.SZ", "000002.SZ"]


def test_pool_import_text_ignores_unknown_symbols():
    from backend.api.main import app

    with TestClient(app) as c:
        # 先建空池。
        r = c.post("/api/pools", json={"name": "__test_poolB", "symbols": []})
        assert r.status_code == 200
        pid = r.json()["data"]["pool_id"]

        # text 支持换行 / 空格 / 逗号混合分隔；``999999.XX`` 不在 stock_symbol，
        # resolver 会跳过，断言只计入 3 条合法。
        r2 = c.post(
            f"/api/pools/{pid}:import",
            json={"text": "000001.SZ 600000.SH\n600519.SH, 999999.XX"},
        )
    assert r2.status_code == 200, r2.text
    body = r2.json()["data"]
    assert body["total_input"] == 4
    assert body["inserted"] == 3


def test_list_pools_contains_created_one():
    """新建池后 ``GET /api/pools`` 必须能看到，且只看到本 owner 下的记录。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/pools", json={"name": "__test_poolC", "symbols": []})
        assert r.status_code == 200
        pid = r.json()["data"]["pool_id"]

        r2 = c.get("/api/pools")
    assert r2.status_code == 200
    pools = r2.json()["data"]
    # list_pools 按 created_at DESC 排序，新池通常在最前；用 pool_id 断言存在更稳。
    assert any(p["pool_id"] == pid for p in pools)


def test_delete_pool_soft_deletes():
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/pools", json={"name": "__test_poolD", "symbols": []})
        pid = r.json()["data"]["pool_id"]

        r2 = c.delete(f"/api/pools/{pid}")
        assert r2.status_code == 200

        r3 = c.get("/api/pools")
    # 软删后列表不再包含该池（list_pools 过滤 is_active=1）。
    pools = r3.json()["data"]
    assert not any(p["pool_id"] == pid for p in pools)
