"""周线多头+KDJ-J超卖 × 日线强承接吸筹（用户 TDX 选股移植）。

组合选股信号（三者同日成立取 1）：
1. **周线 EMA21 > EMA55**（多头排列）。
2. **周线标准 KDJ 的 J < 0**（J=3K−2D；深度超卖时才为负。注：原公式里 ``J:=EMA(stoch30,5)``
   值域 0~100 不会 <0，故"J<0"指的是标准 KDJ 的 J 线，而非那个同名变量）。
3. **日线强承接吸筹 VR5**：34 日随机指标三重 ``SMA(_,3,1)`` 平滑得 VR2/VR3/VR4，
   ``VR5 = CROSS(VR3,VR4) 且 VR3<20``——从超卖区底部上穿的金叉。
   （``SMA(x,3,1) ≡ ewm(alpha=1/3, adjust=False)``，与本仓库 KDJ/tiandi 口径一致。）

方向：反转（周线多头趋势的深度超卖回调 + 日线金叉确认，抄底吸筹）。值越大越看多。

—— 输出口径 ——
稀疏 0/1 事件信号：三条件同日成立为 1，否则 0，停牌/未上市（close 为 NaN）处 NaN。
宜按多头命中率或信号回测（signal_mode=absolute、阈值 0）评估；不适合分位多空。

—— 无未来函数 ——
日线全为 rolling/ewm/shift；周线用 resample(W-FRI)→ewm/rolling→reindex(ffill) 对齐，
周中各日只取上一个**已完成**周的判定，不引入前视。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import load_hlc


class WeeklyBullDailyAbsorb(BaseFactor):
    factor_id = "weekly_bull_daily_absorb"
    display_name = "周线多头+J超卖×日线强承接吸筹"
    category = "custom"
    description = (
        "周线 EMA21>EMA55 多头、周线标准 KDJ 的 J<0（深度超卖），且日线 34 日随机指标"
        "三重 SMA(3,1) 平滑后 VR3 上穿 VR4 且 VR3<20（强承接吸筹金叉）——三者同日成立取 1。"
    )
    hypothesis = (
        "周线多头趋势中回调到深度超卖(J<0)，日线又出现底部金叉吸筹信号，是主力承接、"
        "顺大势抄超跌的买点。"
    )
    params_schema = {
        "daily_window": {"type": "int", "default": 34, "min": 10, "max": 120,
                         "desc": "日线随机指标高低区间窗口（交易日）"},
        "oversold_th": {"type": "float", "default": 20.0, "min": 5.0, "max": 50.0,
                        "desc": "日线金叉的超卖阈值（VR3 低于该值才算）"},
    }
    default_params = {"daily_window": 34, "oversold_th": 20.0}
    supported_freqs = ("1d",)

    # —— 策略签名常数（固定不参数化）——
    _WEEKLY_FAST = 13          # 周线快线 EMA
    _WEEKLY_SLOW = 34          # 周线慢线 EMA
    _WEEKLY_KDJ_N = 9          # 周线标准 KDJ 的 RSV 窗口
    _SMOOTH_ALPHA = 1.0 / 3.0  # SMA(_,3,1)

    def required_warmup(self, params: dict) -> int:
        dw = int(params.get("daily_window", self.default_params["daily_window"]))
        # 绑定约束是周线 E55（约 55 周≈275 交易日）；日线窗口远小于它。
        return self._calc_warmup(max(dw, self._WEEKLY_SLOW * 5))

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        dw = int(params.get("daily_window", self.default_params["daily_window"]))
        oversold_th = float(params.get("oversold_th", self.default_params["oversold_th"]))

        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels

        # —— 日线强承接吸筹 VR5（跨列向量化）——
        llv = low.rolling(dw, min_periods=dw).min()
        hhv = high.rolling(dw, min_periods=dw).max()
        rng = (hhv - llv).where(lambda x: x > 0)          # 区间=0 → NaN，避免 inf
        vr1 = (close - llv) / rng * 100
        vr2 = vr1.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        vr3 = vr2.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        vr4 = vr3.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        # CROSS(VR3,VR4): 前一日 VR3<VR4 且 当日 VR3>VR4
        cross_up = (vr3.shift(1) < vr4.shift(1)) & (vr3 > vr4)
        vr5 = (cross_up & (vr3 < oversold_th)).fillna(False)

        # —— 周线条件：EMA21>EMA55 且 标准 KDJ 的 J<0（resample→ffill 到日频，无前视）——
        wk_close = close.resample("W-FRI").last()
        wk_high = high.resample("W-FRI").max()
        wk_low = low.resample("W-FRI").min()

        e_fast = wk_close.ewm(span=self._WEEKLY_FAST, adjust=False).mean()
        e_slow = wk_close.ewm(span=self._WEEKLY_SLOW, adjust=False).mean()

        n = self._WEEKLY_KDJ_N
        w_llv = wk_low.rolling(n, min_periods=n).min()
        w_hhv = wk_high.rolling(n, min_periods=n).max()
        w_rng = (w_hhv - w_llv).where(lambda x: x > 0)
        rsv = (wk_close - w_llv) / w_rng * 100
        wk_k = rsv.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        wk_d = wk_k.ewm(alpha=self._SMOOTH_ALPHA, adjust=False).mean()
        wk_j = 3 * wk_k - 2 * wk_d

        weekly_ok_wk = (e_fast > e_slow) & (wk_j < 0)
        weekly_ok = weekly_ok_wk.reindex(close.index, method="ffill")
        # reindex 引入 NaN 使布尔帧变 object；用 where 填 False 再定型，避开 fillna 降型告警。
        weekly_ok = weekly_ok.where(weekly_ok.notna(), False).astype(bool)

        # —— 三条件同日成立 ——
        xg = vr5 & weekly_ok
        factor = xg.astype(float).where(close.notna())
        return factor.loc[ctx.start_date:]
