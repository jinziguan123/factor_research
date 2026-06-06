"""pattern_search 路由集成测试：TestClient + monkeypatch（服务层 / mysql / submit），不连库。

注意：模块级构造 TestClient(app) 且不进入 `with`，故 startup 钩子不触发（无需 DB）。
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routers import pattern_search as router_mod

client = TestClient(app)


# ---------------------------- 需求2 by_stock（同步） ----------------------------


def test_by_stock_endpoint(monkeypatch):
    def _fake(data, symbol, **kw):
        return {"query_curve": [0.0, 1.0], "matches": [
            {"label": f"{symbol}@2020-01-01", "score": 0.95, "scale": 60,
             "start_date": "2020-01-01", "end_date": "2020-03-25", "curve": [0.0, 1.0]}
        ]}
    monkeypatch.setattr(router_mod, "search_by_stock", _fake)
    resp = client.post("/api/pattern_search/by_stock", json={"symbol": "000001.SZ", "scales": [60], "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["matches"][0]["score"] == 0.95


# ---------------------------- 需求1 by_image（异步） ----------------------------


class _FakeCursor:
    def __init__(self, fetchone_q, fetchall_q, rowcount):
        self._one = list(fetchone_q)
        self._all = list(fetchall_q)
        self.rowcount = rowcount
        self.calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self._one.pop(0) if self._one else None

    def fetchall(self):
        return self._all.pop(0) if self._all else []


class _FakeConn:
    def __init__(self, fetchone_q=(), fetchall_q=(), rowcount=1):
        self.cur = _FakeCursor(fetchone_q, fetchall_q, rowcount)

    def cursor(self):
        return self.cur

    def commit(self):
        pass


def _patch_conn(monkeypatch, conn):
    @contextmanager
    def _cm():
        yield conn
    monkeypatch.setattr(router_mod, "mysql_conn", _cm)


def test_by_image_creates_run(monkeypatch):
    conn = _FakeConn()
    _patch_conn(monkeypatch, conn)
    submitted: list = []
    monkeypatch.setattr(router_mod, "submit", lambda fn, *a, **k: submitted.append((fn, a)))

    resp = client.post("/api/pattern_search/by_image", json={
        "images": ["data:image/png;base64,a", "data:image/png;base64,b"],
        "image_names": ["a.png", "b.png"],
        "pool_id": 1, "scales": [60], "top_k": 5,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "pending"
    assert len(body["data"]["run_id"]) == 32
    # 派发到了 pattern_search_entry
    assert submitted and submitted[0][0] is router_mod.pattern_search_entry


def test_by_image_rejects_empty(monkeypatch):
    conn = _FakeConn()
    _patch_conn(monkeypatch, conn)
    monkeypatch.setattr(router_mod, "submit", lambda *a, **k: None)
    resp = client.post("/api/pattern_search/by_image", json={"pool_id": 1})
    assert resp.status_code == 400


def test_list_runs(monkeypatch):
    rows = [{"run_id": "r1", "pool_id": 1, "image_names": '["a.png"]', "num_images": 1,
             "status": "success", "progress": 100}]
    conn = _FakeConn(fetchall_q=[rows])
    _patch_conn(monkeypatch, conn)
    resp = client.get("/api/pattern_search/runs")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["run_id"] == "r1"
    assert data[0]["image_names"] == ["a.png"]  # JSON 字段被解析


def test_get_run_detail(monkeypatch):
    run_row = {"run_id": "r1", "pool_id": 1, "image_names": '["a.png"]', "status": "success"}
    res_row = {"query_curves_json": "[[0.0,1.0]]",
               "matches_json": '[{"label":"AAA.SZ","score":0.9}]'}
    conn = _FakeConn(fetchone_q=[run_row, res_row])
    _patch_conn(monkeypatch, conn)
    resp = client.get("/api/pattern_search/runs/r1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["query_curves"] == [[0.0, 1.0]]
    assert data["matches"][0]["label"] == "AAA.SZ"


def test_get_run_404(monkeypatch):
    conn = _FakeConn(fetchone_q=[None])
    _patch_conn(monkeypatch, conn)
    resp = client.get("/api/pattern_search/runs/nope")
    assert resp.status_code == 404


def test_abort_run(monkeypatch):
    conn = _FakeConn(fetchone_q=[{"status": "aborting"}], rowcount=1)
    _patch_conn(monkeypatch, conn)
    resp = client.post("/api/pattern_search/runs/r1/abort")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "aborting"


def test_delete_run(monkeypatch):
    conn = _FakeConn(rowcount=1)
    _patch_conn(monkeypatch, conn)
    resp = client.delete("/api/pattern_search/runs/r1")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True
