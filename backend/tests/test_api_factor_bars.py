"""GET /api/factors/{factor_id}/bars endpoint integration tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_factor_bars_returns_time_series():
    """Normal request returns {dates, values} time series aligned with K-line window."""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-15",
                "freq": "1d",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["factor_id"] == "reversal_n"
    assert data["symbol"] == "000001.SZ"
    assert isinstance(data["dates"], list)
    assert isinstance(data["values"], list)
    assert len(data["dates"]) == len(data["values"])
    assert "params" in data


def test_factor_bars_unsupported_freq_returns_400():
    """Factor doesn't support the requested frequency → 400."""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-05",
                "freq": "1m",
            },
        )
    # reversal_n default supported_freqs=("1d",), does NOT support 1m
    assert r.status_code == 400, r.text


def test_factor_bars_invalid_factor_returns_404():
    """Non-existent factor returns 404."""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/__nonexistent__/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-05",
                "freq": "1d",
            },
        )
    assert r.status_code == 404


def test_factor_bars_with_custom_params():
    """Custom params are passed through and used for computation."""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-10",
                "freq": "1d",
                "params": '{"window": 10}',
            },
        )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["params"] == {"window": 10}


def test_factor_bars_invalid_params_returns_400():
    """Invalid params return 400."""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-05",
                "freq": "1d",
                "params": '{"window": -1}',
            },
        )
    assert r.status_code == 400
