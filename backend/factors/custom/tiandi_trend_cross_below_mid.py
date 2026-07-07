"""天地绝杀·趋势线超卖分档上穿 × 收盘低于中线（用户 TDX 选股公式移植）。

原公式是一个**单票选股信号 XG**：趋势线（放大版 55 日随机指标）从超卖低档
"分档上穿"对应阈值，且当日收盘价低于"中线"。是一个稀疏的事件型 AND 门买入信号。

—— 口径 ——
1. **趋势线**（与 ``tiandi_trend_oversold`` 完全同口径）：
   ``stoch = (C-LLV(L,W))/(HHV(H,W)-LLV(L,W))*100``（W 默认 55）；
   ``V11 = 3*SMA(stoch,5,1) - 2*SMA(SMA(stoch,5,1),3,1)``（KDJ-J 式放大）；
   ``趋势线 = EMA(V11,3)``。TDX 的 ``SMA(x,n,1) ≡ ewm(alpha=1/n, adjust=False)``、
   ``EMA(x,n) ≡ ewm(span=n, adjust=False)``——与本仓库 KDJ/tiandi 口径一致。
2. **中线**（原公式用 DYNAINFO 实时盘口，这里按用户要求换成 hlco 可回测量）：
   ``H1 = max(昨收, 当日最高)``、``L1 = min(昨收, 当日最低)``、``P1 = H1-L1``；
   ``阻力 = L1 + P1*7/8``、``支撑 = L1 + P1*0.5/8``、``中线 = (支撑+阻力)/2``。
3. **BB 分档上穿**（CROSS(趋势线,k) = 趋势线[t-1]<k 且 趋势线[t]>k，即当日上穿 k）：
   - BB2：趋势线[t-1]∈(3,6) 且上穿 6
   - BB3：趋势线[t-1]∈(1,3) 且上穿 3
   - BB4：趋势线[t-1]∈(0,1) 且上穿 1
   - BB5：趋势线[t-1]<0   且上穿 0
   语义：趋势线越深（坑越低），触发所需上穿的阈值越低——刻画"从超卖底部刚拐头"。
4. **XG = (BB2 或 BB3 或 BB4 或 BB5) 且 收盘 < 中线**。

方向：反转（深度超卖拐头 + 价仍在中线下方，抄底信号）。因子值越大越看多。

—— 输出口径 ——
稀疏 0/1 事件信号：XG 当日为 1，否则 0，停牌/未上市（close 为 NaN）处为 NaN。
适合当**多头选股信号**评估（命中率/事件后收益），或用信号回测（signal_mode=absolute、
阈值 0）；不适合分位多空（单日截面几乎全 0，qcut 会退化）。

—— 无未来函数 ——
全部为 rolling / ewm / shift(1)，仅用 ≤t 数据；H1/L1 用当日 high/low + 昨收(shift1)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import load_hlc


class TiandiTrendCrossBelowMid(BaseFactor):
    factor_id = "tiandi_trend_cross_below_mid"
    display_name = "天地绝杀·趋势线超卖上穿×价低于中线"
    category = "custom"
    description = (
        "趋势线（55 日随机指标经 3×SMA(5,1)−2×SMA(3,1) 放大后 EMA(3)）从超卖低档"
        "分档上穿 6/3/1/0，且当日收盘低于中线(H1=max(昨收,高)、L1=min(昨收,低) 派生)"
        "——五重条件的稀疏买入选股信号，值为 1 即选中。"
    )
    hypothesis = (
        "放大版随机指标深跌进超卖区后从底部拐头上穿、且价仍在中线下方，是恐慌超跌的"
        "抄底买点；分档阈值让越深的坑越灵敏触发。"
    )
    params_schema = {
        "window": {"type": "int", "default": 55, "min": 10, "max": 250,
                   "desc": "随机指标高低区间窗口（交易日）"},
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

        # —— 趋势线（同 tiandi_trend_oversold 口径）——
        llv = low.rolling(w, min_periods=w).min()
        hhv = high.rolling(w, min_periods=w).max()
        rng = (hhv - llv).where(lambda x: x > 0)          # 区间=0 → NaN，避免 inf
        stoch = (close - llv) / rng * 100
        a = stoch.ewm(alpha=1 / 5, adjust=False).mean()   # SMA(stoch,5,1)
        v11 = 3 * a - 2 * a.ewm(alpha=1 / 3, adjust=False).mean()  # 3a − 2·SMA(a,3,1)
        trend = v11.ewm(span=3, adjust=False).mean()      # EMA(V11,3)
        s1 = trend.shift(1)                               # REF(趋势线,1)

        # —— 中线（DYNAINFO(3/5/6) → 昨收/当日最高/当日最低）——
        prev_close = close.shift(1)
        h1 = np.maximum(prev_close, high)
        l1 = np.minimum(prev_close, low)
        p1 = h1 - l1
        support = l1 + p1 * 0.5 / 8
        resist = l1 + p1 * 7.0 / 8
        mid = (support + resist) / 2.0

        # —— BB 分档上穿（CROSS(趋势线,k): 前一日<k 且 当日>k）——
        bb2 = (s1 > 3) & (s1 < 6) & (trend > 6)
        bb3 = (s1 > 1) & (s1 < 3) & (trend > 3)
        bb4 = (s1 > 0) & (s1 < 1) & (trend > 1)
        bb5 = (s1 < 0) & (trend > 0)
        bb = bb2 | bb3 | bb4 | bb5

        # —— XG = BB 且 收盘 < 中线 —— NaN 比较返回 False，天然不触发。
        xg = bb & (close < mid)

        # 有数据处 0/1，停牌/未上市（close 为 NaN）处 NaN。
        factor = xg.astype(float).where(close.notna())
        return factor.loc[ctx.start_date:]
