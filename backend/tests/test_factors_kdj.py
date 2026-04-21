"""KDJ oscillator 因子的纯计算单测。

通过 FakeDataService 喂预置 high / low / close panel，验证：
- helper compute_kdj 的数学正确性（形状、值域、边界）；
- 5 个因子在极端构造序列下的输出符合预期方向与公式。

测试**不加** integration mark，和 test_factors_math.py 同属常规单测。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.factors.oscillator._kdj import compute_kdj


@dataclass
class FakeDataService:
    """只实现 load_panel 的最小替身（同 test_factors_math.FakeDataService）。"""
    panels: dict[str, pd.DataFrame]

    def load_panel(
        self,
        symbols,
        start,
        end,
        freq: str = "1d",
        field: str = "close",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        return df[cols].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


# ====================== helper compute_kdj ======================

def test_compute_kdj_basic_shape_and_range() -> None:
    """单调上涨序列上：RSV 接近 100 → K/D 从首值 50 单调逼近 100；
    J = 3K - 2D 在 K/D 同向时也应为正。"""
    idx = _biz_index(30)
    # 从 10 涨到 30，连续单调
    close = np.linspace(10.0, 30.0, num=30)
    # high / low 相对 close 做小幅扰动，保证 (max - min) > 0
    high = close + 0.2
    low = close - 0.2
    h = pd.DataFrame({"A": high}, index=idx)
    l = pd.DataFrame({"A": low}, index=idx)
    c = pd.DataFrame({"A": close}, index=idx)

    K, D, J = compute_kdj(h, l, c, n=9)

    # 形状一致、列一致
    assert K.shape == c.shape
    assert list(K.columns) == list(c.columns)
    # 窗口就位后（index >= n-1），K 值应在 [0, 100] 之间
    tail_k = K["A"].iloc[15:].dropna()
    assert (tail_k >= 0 - 1e-9).all() and (tail_k <= 100 + 1e-9).all()
    # J = 3K - 2D 的定义性断言
    assert np.allclose(J.values, 3 * K.values - 2 * D.values, equal_nan=True)
    # 单调上涨下，末尾 K 应显著 > 50（RSV ≈ 100，EMA 收敛向 100）
    assert K["A"].iloc[-1] > 80
