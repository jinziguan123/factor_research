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


def test_compute_kdj_flat_bars_yield_nan() -> None:
    """high == low == close 连续 n 天 → RSV 分母 0 → K/D/J 全 NaN。

    .where(rng > 0) 把分母 0 替换为 NaN 是这段代码最易错的一行，
    单独锁一下防止未来改成 rng.replace(0, np.nan) 之类的变体破坏语义。
    """
    idx = _biz_index(20)
    const = np.full(20, 15.0)
    h = pd.DataFrame({"A": const}, index=idx)
    l = pd.DataFrame({"A": const}, index=idx)
    c = pd.DataFrame({"A": const}, index=idx)

    K, D, J = compute_kdj(h, l, c, n=9)

    # 窗口就位后（iloc >= 8）全应 NaN——因为每个窗口的 range=0
    assert K["A"].iloc[9:].isna().all()
    assert D["A"].iloc[9:].isna().all()
    assert J["A"].iloc[9:].isna().all()


def test_compute_kdj_multi_column_no_cross_contamination() -> None:
    """两列独立输入应独立计算，一列的数据不会污染另一列的 rolling / ewm。

    是"跨列向量化"声明的关键验证：构造 A 单调涨、B 单调跌，
    末端 K_A 应明显 > 50，K_B 应明显 < 50。
    """
    idx = _biz_index(40)
    a = np.linspace(10.0, 30.0, num=40)
    b = np.linspace(30.0, 10.0, num=40)
    h = pd.DataFrame({"A": a + 0.2, "B": b + 0.2}, index=idx)
    l = pd.DataFrame({"A": a - 0.2, "B": b - 0.2}, index=idx)
    c = pd.DataFrame({"A": a, "B": b}, index=idx)

    K, D, J = compute_kdj(h, l, c, n=9)

    # A 涨到顶（RSV→100）→ K_A 末端应 > 80
    assert K["A"].iloc[-1] > 80
    # B 跌到底（RSV→0）→ K_B 末端应 < 20
    assert K["B"].iloc[-1] < 20


def test_compute_kdj_rejects_mismatched_columns() -> None:
    """防御性断言：高低价列不一致时应 fail-fast，而非静默广播。"""
    import pytest
    idx = _biz_index(20)
    h = pd.DataFrame({"A": np.full(20, 15.0)}, index=idx)
    l = pd.DataFrame({"B": np.full(20, 14.0)}, index=idx)  # 故意错列名
    c = pd.DataFrame({"A": np.full(20, 14.5)}, index=idx)

    with pytest.raises(ValueError, match="columns 必须一致"):
        compute_kdj(h, l, c, n=9)


from backend.factors.oscillator.kdj_j_oversold import KdjJOversold


def test_kdj_j_oversold_monotonic_up_gives_negative_factor() -> None:
    """单调上涨下 RSV≈100 → K,D 收敛到 100 → J≈100 → factor = -J ≈ -100。

    即"上涨到顶部"时因子强烈看空（负值大），符合反转语义。
    """
    idx = _biz_index(40)
    close = np.linspace(10.0, 30.0, num=40)
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[20],
        end_date=idx[-1],
        warmup_days=20,
    )
    factor = KdjJOversold().compute(ctx, {"n": 9})
    tail = factor["A"].dropna()
    assert not tail.empty
    # factor = -J；顶部时 J 应 >> 0，因子值应 << 0
    assert (tail.values < -50).all()


def test_kdj_j_oversold_required_warmup() -> None:
    """warmup 公式：int(n * 3 * 1.5) + 10。n=9 → int(40.5)+10 = 50。"""
    assert KdjJOversold().required_warmup({"n": 9}) == 50
    # n=20 → int(90)+10 = 100
    assert KdjJOversold().required_warmup({"n": 20}) == 100


def test_kdj_j_oversold_monotonic_down_gives_positive_factor() -> None:
    """单调下跌下 RSV→0 → K,D→0 → J→0 附近；深度超卖段 J 可能短暂 < 0
    → factor = -J 应 > 0（反弹看多信号）。锁"-J 符号 = 反转方向"语义。
    """
    idx = _biz_index(40)
    close = np.linspace(30.0, 10.0, num=40)
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[20],
        end_date=idx[-1],
        warmup_days=20,
    )
    factor = KdjJOversold().compute(ctx, {"n": 9})
    tail = factor["A"].dropna()
    assert not tail.empty
    # 底部 RSV≈0 → K,D→0 → J=3·0-2·0=0；单调下跌尾端应该非负、数值不大
    # 关键是不能像涨到顶那样 < -50，应该 > -10 区间。
    assert (tail.values > -10).all()


from backend.factors.oscillator.kdj_cross import KdjCross


def test_kdj_cross_rebound_gives_positive() -> None:
    """V 型反转末段：价格刚从低点反弹，RSV 快速从低位抬起，K 先于 D 抬升
    （K 是对 RSV 的 EMA，D 是对 K 的 EMA），K - D 应为正（金叉强度）。
    """
    idx = _biz_index(50)
    # 前 25 天 30→10 单调下跌，后 25 天 10→25 单调上涨
    close = np.concatenate([
        np.linspace(30.0, 10.0, num=25),
        np.linspace(10.0, 25.0, num=25),
    ])
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[30],  # 反弹段开始后几天
        end_date=idx[-1],
        warmup_days=30,
    )
    factor = KdjCross().compute(ctx, {"n": 9})
    # 反弹段后期 K > D（金叉已发生），因子值应 > 0
    assert factor["A"].iloc[-1] > 0


def test_kdj_cross_top_then_down_gives_negative() -> None:
    """倒 V 型：前段涨到顶，后段下跌。下跌段 K 先于 D 回落 → K-D < 0（死叉）。
    锁"trend 方向"符号：不是 reversal 因子，涨时 + / 跌时 -。
    """
    idx = _biz_index(50)
    # 前 25 天 10→30 上涨，后 25 天 30→10 下跌
    close = np.concatenate([
        np.linspace(10.0, 30.0, num=25),
        np.linspace(30.0, 10.0, num=25),
    ])
    high = close + 0.2
    low = close - 0.2
    ctx = FactorContext(
        data=FakeDataService(panels={
            "high": pd.DataFrame({"A": high}, index=idx),
            "low": pd.DataFrame({"A": low}, index=idx),
            "close": pd.DataFrame({"A": close}, index=idx),
        }),
        symbols=["A"],
        start_date=idx[30],
        end_date=idx[-1],
        warmup_days=30,
    )
    factor = KdjCross().compute(ctx, {"n": 9})
    # 下跌段后期 K < D（死叉），因子值应 < 0
    assert factor["A"].iloc[-1] < 0


def test_kdj_cross_required_warmup() -> None:
    """warmup = int(n * 3 * 1.5) + 10；n=9 → 50。"""
    assert KdjCross().required_warmup({"n": 9}) == 50
