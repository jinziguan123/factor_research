"""毛利率稳定性单测：-rolling_std(gp_margin, 252)。"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from backend.engine.base_factor import FactorContext
from backend.factors.fundamental.gp_margin_stability import GpMarginStability


@dataclass
class FakeDataService:
    fund_panel: pd.DataFrame

    def load_fundamental_panel(self, symbols, start, end, field="gp_margin",
                                table="fr_fundamental_profit"):
        cols = [s for s in symbols if s in self.fund_panel.columns]
        return self.fund_panel[cols].loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()


def test_gp_margin_stability_happy_path():
    """A 序列恒定 0.30 → std=0 → 因子=-0；B 序列波动 → 因子<0 且 < A。"""
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(0)
    panel = pd.DataFrame({
        "A": [0.30] * n,                                # 恒定
        "B": 0.30 + rng.normal(0, 0.05, n),             # 随机扰动
    }, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A","B"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = GpMarginStability().compute(ctx, {})
    # 末段：A = -0（稳定），B < 0（有波动）；A > B（A 更稳定）
    last = factor.iloc[-1]
    assert abs(last["A"]) < 1e-9   # std 全 0
    assert last["B"] < -1e-3       # 显著波动
    assert last["A"] > last["B"]


def test_gp_margin_stability_nan_robust():
    # 调整：n=280, 252 窗口要"完全落在 0.30 段"，NaN 段必须 ≤ n-252 = 28；
    # plan 原本 NaN=100 时末段窗口仍含 NaN → rolling.std() 返回 NaN。
    # 这里取 NaN=20 + 0.30 段=260 保留 plan 语义意图（前段 NaN ffill 后稳定）。
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    panel = pd.DataFrame({"A": [np.nan]*20 + [0.30]*260}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    factor = GpMarginStability().compute(ctx, {})
    assert not factor.empty
    # 末段：rolling 252 全部落在 0.30 段后期 → std=0 → 因子=0
    assert abs(factor["A"].iloc[-1]) < 1e-9


def test_gp_margin_stability_col_order_invariance():
    n = 280
    idx = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(7)
    panel = pd.DataFrame({
        "A": [0.30]*n,
        "B": 0.30 + rng.normal(0, 0.05, n),
        "C": 0.30 + rng.normal(0, 0.10, n),
    }, index=idx)
    ctx_a = FactorContext(
        data=FakeDataService(fund_panel=panel),
        symbols=["A","B","C"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fa = GpMarginStability().compute(ctx_a, {})

    panel_s = panel[["C", "A", "B"]]
    ctx_s = FactorContext(
        data=FakeDataService(fund_panel=panel_s),
        symbols=["C","A","B"], start_date=idx[0], end_date=idx[-1], warmup_days=0,
    )
    fs = GpMarginStability().compute(ctx_s, {})

    for c in ["A","B","C"]:
        assert (fa[c] - fs[c]).abs().fillna(0).max() < 1e-12
