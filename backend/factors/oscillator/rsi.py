"""RSI（相对强弱指标）因子。

公式：``factor_t = RSI(close, window) - 50``，RSI 使用 Wilder 平滑公式。

直觉：RSI < 30 超卖（看多信号）、RSI > 70 超买（看空信号）。
减 50 中心化后，负值 = 超卖 → 反转看多，正值 = 超买 → 反转看空。
取负后高分 = RSI 低 = 超卖 → 长仓信号，与平台反转因子方向一致。

预热 = ``int(window * 1.5) + 10`` 自然日。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RSI(BaseFactor):
    factor_id = "rsi"
    display_name = "RSI（相对强弱，Wilder 平滑）"
    category = "oscillator"
    description = "-(RSI - 50)，取负后高分 = 超卖 → 长仓，低分 = 超买 → 空仓。"
    hypothesis = "RSI 超卖后均值回归——Wilder 1978 经典技术分析反转指标。"
    params_schema: dict = {
        "window": {"type": "int", "default": 14, "min": 5, "max": 60,
                   "desc": "RSI 窗口（交易日，Wilder 标准 = 14）"},
    }
    default_params: dict = {"window": 14}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        delta = close.diff(1)
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        # Wilder 平滑：EMA(alpha=1/window, adjust=False)
        avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()
        rs = avg_gain / avg_loss.where(avg_loss > 0)
        rsi = 100.0 - 100.0 / (1.0 + rs)
        # 取负使方向与反转因子一致：超卖(rsi<50) → factor>0 → long
        factor = -(rsi - 50.0)
        return factor.loc[ctx.start_date:]
