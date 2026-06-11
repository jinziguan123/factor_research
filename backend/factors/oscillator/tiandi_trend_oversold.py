"""天地绝杀·趋势线超卖因子（移植自通达信「天地绝杀」指标）。

原指标核心：
    stoch = (C - LLV(L,W)) / (HHV(H,W) - LLV(L,W)) * 100         # W 默认 55
    V11   = 3*SMA(stoch,5,1) - 2*SMA(SMA(stoch,5,1),3,1)        # KDJ-J 式放大
    趋势线 = EMA(V11, 3)
趋势线是一条 0~100（会冲过头）的摆动线，原指标的「准备买入」= 趋势线<11 进超卖区，
真正买点是趋势线从超卖区上穿 11/6/3/1/0 等阈值。

做成因子：``factor = -趋势线``，因子越大=趋势线越低=越超卖=越看多（反转）。
注意：原指标的「中线」用 DYNAINFO（实时盘口昨收/最高/最低）算，回测里取不到、也无
意义，故只移植可回测的趋势线本体；5/3/3 这组平滑常数是该指标的"签名"，固定不参数化。

预期方向：反转（深度超卖后反弹）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import load_hlc


class TiandiTrendOversold(BaseFactor):
    factor_id = "tiandi_trend_oversold"
    display_name = "天地绝杀·趋势线超卖"
    category = "oscillator"
    description = "factor = -趋势线；趋势线=W日随机指标经3×快-2×慢三重平滑放大。因子越大越超卖。"
    hypothesis = "趋势线（放大版随机指标）跌进超卖区后倾向反弹；放大处理让拐点更灵敏。"
    params_schema = {
        "window": {"type": "int", "default": 55, "min": 10, "max": 250, "desc": "高低区间窗口（交易日）"},
    }
    default_params = {"window": 55}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        w = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(w * 2)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        w = int(params.get("window", self.default_params["window"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels

        low_min = low.rolling(w, min_periods=w).min()
        high_max = high.rolling(w, min_periods=w).max()
        rng = (high_max - low_min).where(lambda x: x > 0)
        stoch = (close - low_min) / rng * 100
        a = stoch.ewm(alpha=1 / 5, adjust=False).mean()        # SMA(stoch,5,1)
        v11 = 3 * a - 2 * a.ewm(alpha=1 / 3, adjust=False).mean()  # 3a - 2·SMA(a,3,1)
        trend = v11.ewm(span=3, adjust=False).mean()           # EMA(V11,3)
        return (-trend).loc[ctx.start_date:]
