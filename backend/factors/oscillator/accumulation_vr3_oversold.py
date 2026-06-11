"""强承接吸筹·VR3 超卖因子（移植自通达信「强承接吸筹」指标）。

原指标核心：
    VR1 = (C - LLV(L,W)) / (HHV(H,W) - LLV(L,W)) * 100          # W 默认 34
    VR2 = SMA(VR1,3,1);  VR3 = SMA(VR2,3,1);  VR4 = SMA(VR3,3,1)  # 逐级变慢（KDJ式）
原指标的「强承接吸筹」信号 = CROSS(VR3,VR4) AND VR3<20，即在深度超卖区里较快线 VR3
金叉较慢线 VR4 → 低位止跌回升、被解读为低位承接/吸筹。

做成因子：分位数回测框架吃连续值、不吃 0/1 事件，所以输出摆动线本体
``factor = -VR3``（VR3 越低=越超卖=因子越大=越看多）。VR3 落在低分位即对应原指标的
超卖承接区；若要专门回测"金叉事件"，应走信号(signals)子系统而非分位因子。

预期方向：反转（低位超卖承接后反弹）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import load_hlc


class AccumulationVr3Oversold(BaseFactor):
    factor_id = "accumulation_vr3_oversold"
    display_name = "强承接吸筹·VR3超卖"
    category = "oscillator"
    description = "factor = -VR3；VR3=W日随机指标三重SMA平滑。因子越大=越超卖（低位承接区）。"
    hypothesis = "价格在W日区间低位、平滑随机指标转头向上（金叉）时倾向反弹——低位承接逻辑。"
    params_schema = {
        "window": {"type": "int", "default": 34, "min": 5, "max": 250, "desc": "高低区间窗口（交易日）"},
    }
    default_params = {"window": 34}
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
        vr1 = (close - low_min) / rng * 100
        vr2 = vr1.ewm(alpha=1 / 3, adjust=False).mean()   # SMA(VR1,3,1)
        vr3 = vr2.ewm(alpha=1 / 3, adjust=False).mean()   # SMA(VR2,3,1)
        return (-vr3).loc[ctx.start_date:]
