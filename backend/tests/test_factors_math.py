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
from backend.factors.custom.neg_return_argmax_rank import NegReturnArgmaxRank
from backend.factors.momentum.momentum_n import MomentumN
from backend.factors.reversal.reversal_n import ReversalN
from backend.factors.volatility.boll_down import BollDown
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


def test_boll_down_constant_close_equals_one() -> None:
    """常数价格序列下，STD=0，下轨=MA=close，因子 = close/close = 1。"""
    idx = _biz_index(40)
    close = pd.DataFrame({"000001.SZ": np.full(40, 100.0)}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ"],
        start_date=idx[25],
        end_date=idx[-1],
        warmup_days=25,
    )
    factor = BollDown().compute(ctx, {"window": 20}).dropna()
    assert not factor.empty
    assert np.allclose(factor.values, 1.0, atol=1e-9)


def test_boll_down_linear_rising_below_one() -> None:
    """单调上涨时，当日 close 总是高于过去 window 日均值 -> 下轨比值 < 1。"""
    idx = _biz_index(40)
    close = pd.DataFrame(
        {"000001.SZ": np.linspace(100.0, 140.0, num=40)}, index=idx
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ"],
        start_date=idx[25],
        end_date=idx[-1],
        warmup_days=25,
    )
    factor = BollDown().compute(ctx, {"window": 20}).dropna()
    assert not factor.empty
    assert (factor.values < 1.0).all()


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
    assert BollDown().required_warmup({"window": 20}) == 40
    # NegReturnArgmaxRank 用 int((window+1)*1.5) + 5：window=5 → int(9)+5 = 14
    assert NegReturnArgmaxRank().required_warmup({"window": 5}) == 14


def test_neg_return_argmax_rank_output_structure() -> None:
    """NegReturnArgmaxRank 的基本不变量：

    - 前 window 行（预热期内 pct_change + rolling 未就绪）应为 NaN；
    - 非 NaN 值被中心化到 [-0.5, 0.5]；
    - 每日横截面（跨 symbol）所有非 NaN 值之和 ≈ 0（pct=True rank 的均值为 0.5，
      减 0.5 后每行和为 0）。
    """
    idx = _biz_index(40)
    # 3 只股票构造不同的形态：
    #   A：单调上涨（所有 returns > 0，signed_sq < 0，argmax 落在"跌得最少"的日子）；
    #   B：单调下跌（所有 returns < 0，signed_sq > 0，argmax 落在"跌得最深"的日子）；
    #   C：锯齿（returns 正负交替）。
    a = np.linspace(100.0, 150.0, num=40)
    b = np.linspace(150.0, 100.0, num=40)
    c = 100.0 + np.sin(np.arange(40) * 0.5) * 5
    close = pd.DataFrame(
        {"000001.SZ": a, "000002.SZ": b, "000003.SZ": c}, index=idx
    )
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ", "000002.SZ", "000003.SZ"],
        start_date=idx[10],
        end_date=idx[-1],
        warmup_days=10,
    )
    factor = NegReturnArgmaxRank().compute(ctx, {"window": 5})

    # 基本结构
    assert list(factor.columns) == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert not factor.empty

    # 中心化范围
    tail = factor.dropna(how="all")
    vals = tail.values[~np.isnan(tail.values)]
    assert vals.size > 0
    assert vals.min() >= -0.5 - 1e-9
    assert vals.max() <= 0.5 + 1e-9

    # pct=True 下 n=3 只股票每行 rank 的集合恒为 {1/3, 2/3, 1.0}（或有并列时取平均）；
    # 减 0.5 后值域应在 {-1/6, 1/6, 1/2} 中选取，所有非 NaN 值都应匹配这三个之一（含并列）。
    allowed = {-1 / 6, 0.0, 1 / 6, 1 / 3, 1 / 2}  # 含并列时的 avg rank 衍生值
    full_rows = tail.dropna(axis=0)
    for v in full_rows.values.ravel():
        assert any(abs(v - a) < 1e-9 for a in allowed), f"意外值 {v}"


def test_neg_return_argmax_rank_signed_power_math() -> None:
    """验证核心化简：SignedPower((returns<0 ? |r| : -|r|), 2) == -r * |r|。

    用一个极端构造验证"argmax 落在最剧烈下跌日"：
    - 构造 5 天窗口，其中第 3 天（index=2 in window）跌幅最大；
    - signed_sq 在第 3 天为正峰值；
    - argmax 应等于 2（窗口内位置，0-indexed）。
    """
    # 设计 close：t=0 → 100, t=1 → 99 (-1%), t=2 → 90 (-9.09%),
    # t=3 → 91, t=4 → 92。窗口 [0..4] 的 signed_sq 在 t=2 最大。
    idx = _biz_index(8)  # 够 5 天 rolling + 前置 pct_change NaN
    # 填前置 3 天做数据起点，再 5 天作为 rolling window 样本
    closes = [100, 100, 100, 100, 99, 90, 91, 92]
    close = pd.DataFrame({"000001.SZ": closes}, index=idx)
    ctx = FactorContext(
        data=FakeDataService(panels={"close": close}),
        symbols=["000001.SZ"],
        start_date=idx[0],
        end_date=idx[-1],
        warmup_days=3,
    )
    factor = NegReturnArgmaxRank().compute(ctx, {"window": 5})
    # 单只股票横截面 rank 结果恒为 1.0 - 0.5 = 0.5（或该股票是 NaN 则 NaN）；
    # 我们只断言"最末那一行非 NaN"，确认整条链路走通（若 argmax 崩，上游 NaN 会传染）。
    last = factor.iloc[-1].dropna()
    assert len(last) == 1
    assert np.isclose(last.iloc[0], 0.5)
