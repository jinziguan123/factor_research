"""AttributionService unit tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.services.attribution import AttributionService


def make_test_data():
    dates = pd.date_range("2025-01-02", periods=10, freq="B")
    symbols = ["A.SZ", "B.SH", "C.SZ", "D.SH", "E.SZ"]
    np.random.seed(42)
    size = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    value = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    momentum = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    volatility = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    liquidity = pd.DataFrame(np.random.randn(10, 5), index=dates, columns=symbols)
    # Construct alpha = 0.5*Size + 0.3*Value + noise
    alpha = 0.5 * size + 0.3 * value + 0.1 * np.random.randn(10, 5)
    style_panels = {
        "Size": size, "Value": value, "Momentum": momentum,
        "Volatility": volatility, "Liquidity": liquidity,
    }
    return alpha, style_panels


def test_decompose_returns_exposures():
    alpha, style_panels = make_test_data()
    svc = AttributionService()
    result = svc.decompose(alpha, style_panels)
    assert set(result.exposures.keys()) == set(style_panels.keys())
    for name, series in result.exposures.items():
        assert len(series) == len(alpha.index)


def test_decompose_r_squared_between_0_and_1():
    alpha, style_panels = make_test_data()
    svc = AttributionService()
    result = svc.decompose(alpha, style_panels)
    assert (result.r_squared.dropna() >= 0).all()
    assert (result.r_squared.dropna() <= 1).all()


def test_decompose_residual_shape():
    alpha, style_panels = make_test_data()
    svc = AttributionService()
    result = svc.decompose(alpha, style_panels)
    assert result.residual.shape == alpha.shape
