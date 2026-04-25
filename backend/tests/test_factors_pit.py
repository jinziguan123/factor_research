"""PIT ROE 因子单测：直接喂 fundamental panel，验证 compute 透传 + 切片。"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from backend.engine.base_factor import FactorContext
from backend.factors.custom.roe_pit import RoePit


@dataclass
class FakeFundService:
    """只实现 load_fundamental_panel 的 DataService 替身。"""
    panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.panel.columns]
        return self.panel[cols].copy()


def test_roe_pit_returns_panel_sliced_to_window():
    idx = pd.bdate_range("2025-12-01", periods=10)
    panel = pd.DataFrame(
        {"000001.SZ": [None]*3 + [0.1]*7, "600000.SH": [None]*5 + [0.2]*5},
        index=idx,
    )
    ctx = FactorContext(
        data=FakeFundService(panel=panel),
        symbols=["000001.SZ", "600000.SH"],
        start_date=idx[5],
        end_date=idx[-1],
        warmup_days=0,
    )
    factor = RoePit().compute(ctx, params={})

    assert factor.index[0] == idx[5]
    assert factor.index[-1] == idx[-1]
    assert set(factor.columns) == {"000001.SZ", "600000.SH"}
    assert factor["600000.SH"].iloc[0] == pytest.approx(0.2)
    assert factor["000001.SZ"].iloc[0] == pytest.approx(0.1)


def test_roe_pit_required_warmup_is_zero():
    assert RoePit().required_warmup({}) == 0
