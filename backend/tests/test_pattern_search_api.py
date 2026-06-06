"""pattern_search 路由集成测试：用 TestClient + monkeypatch 服务层，不连数据库。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routers import pattern_search as router_mod

client = TestClient(app)


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
