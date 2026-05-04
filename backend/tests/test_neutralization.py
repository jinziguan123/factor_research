"""NeutralizationService unit tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.neutralization import NeutralizationService


def make_test_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Construct 3 stocks x 5 days of fake data."""
    dates = pd.date_range("2025-01-02", periods=5, freq="B")
    symbols = ["A.SZ", "B.SH", "C.SZ"]
    np.random.seed(42)
    factor = pd.DataFrame(
        np.random.randn(5, 3), index=dates, columns=symbols
    )
    mktcap = pd.DataFrame(
        [[1e10, 5e10, 2e10]] * 5, index=dates, columns=symbols, dtype=float
    )
    industry = pd.Series(
        {"A.SZ": "银行", "B.SH": "电子", "C.SZ": "银行"}
    )
    return factor, mktcap, industry


def test_neutralize_returns_same_shape():
    factor, mktcap, industry = make_test_data()
    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry)
    assert result.shape == factor.shape
    assert list(result.index) == list(factor.index)
    assert list(result.columns) == list(factor.columns)


def test_neutralize_reduces_industry_bias():
    """After neutralization, within-industry mean should be near zero."""
    factor, mktcap, industry = make_test_data()
    factor.iloc[:, :] = 0.0
    # Inject industry bias: bank stocks get +0.1
    bank_cols = [c for c in factor.columns if industry[c] == "银行"]
    factor[bank_cols] += 0.1

    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry)
    bank_residual = result[bank_cols].values.mean()
    assert abs(bank_residual) < 0.02


def test_neutralize_handles_nan():
    """NaN factor values should remain NaN in output."""
    factor, mktcap, industry = make_test_data()
    factor.iloc[2, 0] = np.nan
    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry)
    assert np.isnan(result.iloc[2, 0])


def test_neutralize_small_industry_merged():
    """Industries with < min_industry_size stocks should be merged into 'other'."""
    factor, mktcap, industry = make_test_data()
    industry["C.SZ"] = "稀有行业"  # single stock in this industry
    svc = NeutralizationService()
    result = svc.neutralize(factor, mktcap, industry, min_industry_size=3)
    assert result.shape == factor.shape


def test_neutralize_market_cap_only():
    factor, mktcap, industry = make_test_data()
    svc = NeutralizationService()
    result = svc.neutralize_with_market_cap_only(factor, mktcap)
    assert result.shape == factor.shape


def test_neutralize_industry_only():
    factor, mktcap, industry = make_test_data()
    svc = NeutralizationService()
    result = svc.neutralize_with_industry_only(factor, industry)
    assert result.shape == factor.shape
