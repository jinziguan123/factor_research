"""PIT ROE 因子单测：直接喂 fundamental panel，验证 compute 透传 + 切片。"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from backend.engine.base_factor import FactorContext
from backend.factors.custom.roe_pit import RoePit


@dataclass
class FakeFundService:
    """只实现 load_fundamental_panel 的 DataService 替身。

    本 fake 假定 ``panel`` 已是按交易日 ffill 好的稠密面板（如真实
    DataService.load_fundamental_panel 的返回值）；左 seed / 跨披露 ffill
    的正确性由 ``tests/test_data_service_fundamentals.py`` 单独覆盖，
    此处仅做 column 过滤 + 日期窗口切片。
    """
    panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.panel.columns]
        return self.panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


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


def test_roe_pit_returns_nan_before_first_disclosure():
    """披露日之前的交易日因子值必须是 NaN（PIT 语义不前视）。"""
    idx = pd.bdate_range("2025-12-01", periods=10)
    # 000001.SZ 在 idx[5] 才首次披露 0.1；之前 5 天必须是 NaN
    panel = pd.DataFrame(
        {"000001.SZ": [None]*5 + [0.1]*5},
        index=idx,
    )
    ctx = FactorContext(
        data=FakeFundService(panel=panel),
        symbols=["000001.SZ"],
        start_date=idx[2],   # 故意从披露日之前开始
        end_date=idx[-1],
        warmup_days=0,
    )
    factor = RoePit().compute(ctx, params={})

    # 披露前 3 天（idx[2], idx[3], idx[4]）必须 NaN
    assert pd.isna(factor["000001.SZ"].loc[idx[2]])
    assert pd.isna(factor["000001.SZ"].loc[idx[4]])
    # 披露日及之后是 0.1
    assert factor["000001.SZ"].loc[idx[5]] == pytest.approx(0.1)
    assert factor["000001.SZ"].loc[idx[-1]] == pytest.approx(0.1)
