"""内置因子的纯计算单测（不依赖数据库）。

通过 ``FakeDataService`` 只实现 ``load_panel``，把期望的 close / amount_k 面板
直接喂给因子的 ``compute()``，这样就能在没有 MySQL / ClickHouse 的环境下验证：

- ReversalN 在单调上涨序列上应全为负；
- MomentumN 在单调上涨序列上应全为正；
- RealizedVol 在常数价格序列上应 ≈ 0；
- TurnoverRatio 返回非空、全部 > 0（且使用 amount_k 而非 close / volume）。

测试**不加** ``integration`` mark，属于常规单测。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from backend.engine.base_factor import FactorContext
from backend.factors.momentum.momentum_n import MomentumN
from backend.factors.reversal.reversal_n import ReversalN
from backend.factors.volatility.realized_vol import RealizedVol
from backend.factors.volume.turnover_ratio import TurnoverRatio


@dataclass
class FakeDataService:
    """只实现 load_panel 的最小 DataService 替身。

    把 ``{field: DataFrame}`` 预置到 ``panels`` 字段，``load_panel`` 按 field key 查。
    """

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
        """返回预置的完整 panel（按 symbols 列过滤，不按日期过滤）。

        为什么不按 ``start`` / ``end`` 切片：因子的 ``compute`` 会用
        ``start - warmup`` 作为 data_start，fake 面板本身是紧凑的，
        按 data_start 切片容易把"够用的历史"切掉。模拟无限历史即可。
        """
        df = self.panels.get(field)
        if df is None:
            return pd.DataFrame()
        cols = [s for s in symbols if s in df.columns]
        # 只按列过滤，保留完整时间轴
        return df[cols].copy()


def _biz_index(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    """B 频率（工作日）日期索引；避开周末让测试日期更直观。"""
    return pd.bdate_range(start=start, periods=n)


def test_reversal_monotonically_rising_close_is_negative() -> None:
    """单调上涨 close 序列下，ReversalN = -pct_change 应全为负。"""
    idx = _biz_index(30)
    # 从 100 线性涨到 130，close 一直上升。
    close = pd.DataFrame(
        {"000001.SZ": np.linspace(100.0, 130.0, num=30)}, index=idx
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ"],
        start_date=idx[5],
        end_date=idx[-1],
        warmup_days=25,
    )
    factor = ReversalN().compute(ctx, {"window": 3})
    # 只看非 NaN 行（前 window 行必然 NaN），其余必须 < 0。
    tail = factor.dropna()
    assert not tail.empty
    assert (tail.values < 0).all()


def test_momentum_monotonically_rising_close_is_positive() -> None:
    """单调上涨 close 序列下，MomentumN 应全为正。"""
    idx = _biz_index(160)
    close = pd.DataFrame(
        {"000001.SZ": np.linspace(100.0, 200.0, num=160)}, index=idx
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ"],
        start_date=idx[130],
        end_date=idx[-1],
        warmup_days=125,
    )
    factor = MomentumN().compute(ctx, {"window": 120, "skip": 5})
    tail = factor.dropna()
    assert not tail.empty
    assert (tail.values > 0).all()


def test_realized_vol_constant_close_is_zero() -> None:
    """常数价格序列上，日收益率恒为 0，滚动 std 也为 0。"""
    idx = _biz_index(40)
    close = pd.DataFrame(
        {"000001.SZ": np.full(40, 100.0)}, index=idx
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ"],
        start_date=idx[25],
        end_date=idx[-1],
        warmup_days=25,
    )
    factor = RealizedVol().compute(ctx, {"window": 20})
    tail = factor.dropna()
    assert not tail.empty
    # 浮点容差：pct_change 后 rolling.std 可能产生极小非 0 数值，用 abs < 1e-9。
    assert np.allclose(tail.values, 0.0, atol=1e-9)


def test_turnover_uses_amount_and_close() -> None:
    """TurnoverRatio 应同时读 amount_k（adjust=none）与 close（adjust=qfq）并返回正值。"""
    idx = _biz_index(30)
    amount = pd.DataFrame(
        {"000001.SZ": np.full(30, 1_000.0)}, index=idx  # 千元
    )
    close = pd.DataFrame(
        {"000001.SZ": np.full(30, 10.0)}, index=idx
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"amount_k": amount, "close": close}),
        symbols=["000001.SZ"],
        start_date=idx[22],
        end_date=idx[-1],
        warmup_days=25,
    )
    factor = TurnoverRatio().compute(ctx, {"window": 20})
    tail = factor.dropna()
    assert not tail.empty
    # 1000 / 10 = 100，应全为 100。
    assert (tail.values > 0).all()
    assert np.allclose(tail.values, 100.0, rtol=1e-9)


def test_required_warmup_values() -> None:
    """对照设计文档的 warmup 公式，避免未来改动后出现不一致。

    新公式：``int(N * 1.5) + 10``（MomentumN 用 ``window + skip``）。
    - window=20 → int(30) + 10 = 40
    - window=120, skip=5 → int(187.5) + 10 = 197
    """
    assert ReversalN().required_warmup({"window": 20}) == 40
    assert MomentumN().required_warmup({"window": 120, "skip": 5}) == 197
    assert RealizedVol().required_warmup({"window": 20}) == 40
    assert TurnoverRatio().required_warmup({"window": 20}) == 40
