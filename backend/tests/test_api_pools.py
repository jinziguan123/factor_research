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


def test_pool_import_dedups_and_preserves_order():
    """批量 import 同批重复 symbol 只记一次；``sort_order`` 按首次出现位置。

    回归点：改批量 INSERT 后，去重必须做在 Python 侧（不是靠 INSERT IGNORE 撞
    唯一索引），否则多次出现的 symbol 在 VALUES 里会多次出现、rowcount=1 只来自
    第一次，后续同 pool_id+symbol_id 撞主键被 IGNORE 吞掉，结果与老路径一致但
    sort_order 会分配给第一次出现的位置而不是最后一次（行为可观测）。
    """
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/pools", json={"name": "__test_poolDedup", "symbols": []})
        pid = r.json()["data"]["pool_id"]

        # 重复 600000.SH，未知 999999.XX 过滤掉
        r2 = c.post(
            f"/api/pools/{pid}:import",
            json={"text": "000001.SZ 600000.SH 600000.SH 600519.SH 999999.XX"},
        )
        assert r2.status_code == 200
        body = r2.json()["data"]
        assert body["total_input"] == 5
        # 去重后的合法 symbol = 3（000001 / 600000 / 600519）
        assert body["inserted"] == 3

        r3 = c.get(f"/api/pools/{pid}")
    data = r3.json()["data"]
    symbols = [s["symbol"] for s in data["symbols"]]
    # 保序：按首次出现位置排
    assert symbols == ["000001.SZ", "600000.SH", "600519.SH"]


def test_update_pool_without_symbols_preserves_members():
    """PUT 不传 symbols 时必须保留成员，只改 name / description。

    回归点：schemas.PoolIn.symbols 的默认值从 ``[]`` 改成 ``None`` 后，原先
    "用户只改池名 → 成员全被清空" 的既存 bug 才被修掉。若默认回到 ``[]``，这条
    用例会立刻炸。
    """
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/pools",
            json={
                "name": "__test_poolKeep",
                "symbols": ["000001.SZ", "600519.SH"],
            },
        )
        pid = r.json()["data"]["pool_id"]

        # 只改池名，不传 symbols
        r2 = c.put(
            f"/api/pools/{pid}",
            json={"name": "__test_poolKeep_renamed", "description": "x"},
        )
        assert r2.status_code == 200, r2.text

        r3 = c.get(f"/api/pools/{pid}")
    data = r3.json()["data"]
    assert data["pool_name"] == "__test_poolKeep_renamed"
    assert [s["symbol"] for s in data["symbols"]] == ["000001.SZ", "600519.SH"]


def test_update_pool_with_empty_symbols_clears_members():
    """PUT 显式传 ``symbols: []`` 时**清空**成员。

    与上一条配对：验证 None / [] 两种入参的行为分叉。
    """
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/pools",
            json={"name": "__test_poolClear", "symbols": ["000001.SZ"]},
        )
        pid = r.json()["data"]["pool_id"]

        r2 = c.put(
            f"/api/pools/{pid}",
            json={"name": "__test_poolClear", "symbols": []},
        )
        assert r2.status_code == 200

        r3 = c.get(f"/api/pools/{pid}")
    assert r3.json()["data"]["symbols"] == []


def test_remove_symbol_single():
    """DELETE /{pool_id}/symbols/{symbol} 移除单只，不动其他成员。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/pools",
            json={
                "name": "__test_poolRemove",
                "symbols": ["000001.SZ", "000002.SZ", "600519.SH"],
            },
        )
        pid = r.json()["data"]["pool_id"]

        r2 = c.delete(f"/api/pools/{pid}/symbols/000002.SZ")
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"]["removed"] == 1

        r3 = c.get(f"/api/pools/{pid}")
    symbols = [s["symbol"] for s in r3.json()["data"]["symbols"]]
    assert symbols == ["000001.SZ", "600519.SH"]


def test_remove_symbol_idempotent():
    """重复删除同一 symbol 返回 removed=0，不抛 404（幂等）。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post(
            "/api/pools",
            json={"name": "__test_poolIdem", "symbols": ["000001.SZ"]},
        )
        pid = r.json()["data"]["pool_id"]

        # 第 1 次：真删除
        r2 = c.delete(f"/api/pools/{pid}/symbols/000001.SZ")
        assert r2.json()["data"]["removed"] == 1
        # 第 2 次：已不在池里，返回 removed=0 而不是 404
        r3 = c.delete(f"/api/pools/{pid}/symbols/000001.SZ")
    assert r3.status_code == 200
    assert r3.json()["data"]["removed"] == 0


def test_remove_symbol_unknown_404():
    """未知 symbol（不在 stock_symbol 主表）返回 404，帮前端尽早发现输错代码。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/pools", json={"name": "__test_poolUnk", "symbols": []})
        pid = r.json()["data"]["pool_id"]

        r2 = c.delete(f"/api/pools/{pid}/symbols/999999.XX")
    assert r2.status_code == 404


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
