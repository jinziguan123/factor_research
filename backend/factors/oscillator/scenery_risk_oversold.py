"""风景线·风险值超卖因子（移植自通达信「风景线」指标）。

原指标核心：
    风险值 = EMA( (C - LLV(LOW,M)) / (HHV(H,M) - LLV(LOW,M)) * 100 , N )
即一条 M 日随机指标再做 N 日 EMA 平滑的 0~100 摆动线。**值低=价格贴在 M 日区间
底部=超卖（低风险位）；值高=贴顶部=超买（高风险位）。** 原指标的「超低位区」就是
风险值<=5。

做成因子：``factor = -风险值``，与平台约定（因子越大越看多）对齐——风险值越低
（越超卖），因子越大，对应"超卖反弹"的看多方向。原指标里基于成交量柱/背离/变盘
的画线都是展示用，不影响这条核心摆动线，故不移植。

预期方向：反转（低位超卖后均值回归）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import load_hlc


class SceneryRiskOversold(BaseFactor):
    factor_id = "scenery_risk_oversold"
    display_name = "风景线·风险值超卖"
    category = "oscillator"
    description = "factor = -风险值；风险值=M日随机指标的N日EMA。因子越大越超卖、越看多。"
    hypothesis = "价格跌到M日区间底部（风险值极低）后倾向均值回归反弹；用EMA平滑去抖。"
    params_schema = {
        "m": {"type": "int", "default": 34, "min": 5, "max": 250, "desc": "高低区间窗口（交易日）"},
        "n": {"type": "int", "default": 3, "min": 1, "max": 30, "desc": "EMA 平滑周期"},
    }
    default_params = {"m": 34, "n": 3}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        m = int(params.get("m", self.default_params["m"]))
        return self._calc_warmup(m * 2)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        m = int(params.get("m", self.default_params["m"]))
        n = int(params.get("n", self.default_params["n"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels

        low_min = low.rolling(m, min_periods=m).min()
        high_max = high.rolling(m, min_periods=m).max()
        rng = (high_max - low_min).where(lambda x: x > 0)  # 横盘 range=0 → NaN，避免 inf
        raw = (close - low_min) / rng * 100
        risk = raw.ewm(span=n, adjust=False).mean()         # TDX EMA(X,N)
        return (-risk).loc[ctx.start_date:]
