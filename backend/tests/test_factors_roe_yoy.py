"""ROE YoY 因子单测：roe_avg - shift(252)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.fundamental.roe_yoy import RoeYoy


@dataclass
class FakeDataService:
    fund_panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="roe_avg",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_roe_yoy_happy_path():
    """构造 280 天 ROE 序列：前 252 天 0.10，之后 0.15 → 因子在 t=252 起 = 0.05。"""
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({"A": [0.10]*252 + [0.15]*(n-252)}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = RoeYoy().compute(ctx, {})
    # 前 252 天因 shift(252) NaN → 因子 NaN
    assert factor["A"].iloc[:252].isna().all()
    # 第 252+ 天 = 0.15 - 0.10 = 0.05
    assert abs(factor["A"].iloc[252] - 0.05) < 1e-9


def test_roe_yoy_nan_robust():
    """披露前 NaN 段 + shift 后段都应是 NaN，不崩。"""
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    # NaN 段长度需 < n - 252 = 28，否则末日 shift(252) 仍落在 NaN 段，因子无法验证 = 0
    panel = pd.DataFrame({"A": [np.nan]*20 + [0.10]*(n-20)}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = RoeYoy().compute(ctx, {})
    # 至少不抛异常 + 末段 = 0（0.10 - 0.10）
    assert not factor.empty
    assert factor["A"].iloc[-1] == 0.0


def test_roe_yoy_col_order_invariance():
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({
        "A": [0.10]*252 + [0.15]*(n-252),
        "B": [0.05]*252 + [0.20]*(n-252),
        "C": [0.12]*252 + [0.10]*(n-252),
    }, index=idx)
    ctx_a = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A","B","C"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fa = RoeYoy().compute(ctx_a, {})

    panel_s = panel[["C", "A", "B"]]
    ctx_s = FactorContext(
        data=FakeDataService(fund_panel=panel_s),
        symbols=["C","A","B"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fs = RoeYoy().compute(ctx_s, {})
    for c in ["A","B","C"]:
        assert (fa[c] - fs[c]).abs().fillna(0).max() < 1e-12
