"""GpMarginStability：毛利率稳定性（AQR Quality）。

公式：``factor_t = -1 * rolling_std(gp_margin, window=252)``。

直觉：毛利率波动小 = 商业模式稳定 = 高质量企业。AQR Quality 因子里
"Profitability Stability" 维度。取负后大值 → 稳定 → 长仓信号。

注意：``load_fundamental_panel`` 返回 ffill 后的日频 panel，含大量重复值
（季报 ~60 个交易日才更新一次），rolling std 在重复值期间是 0；这种"伪低波"
是 ffill 的副作用，所有股票同样被偏置，下游 cross-section 排序不受影响。

预热 = 0（PIT 自带左 seed；rolling 前 window-1 天 NaN）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class GpMarginStability(BaseFactor):
    factor_id = "gp_margin_stability"
    display_name = "毛利率稳定性（-rolling_std(gp_margin, 252)）"
    category = "fundamental"
    description = "PIT gp_margin 的 252 交易日 rolling std 取负——稳定 → 长仓。"
    hypothesis = "毛利率稳定 → 商业模式可预测 → 长期超额（AQR Quality）。"
    params_schema: dict = {
        "window": {"type": "int", "default": 252, "min": 60, "max": 504,
                   "desc": "rolling std 窗口（交易日）"},
    }
    default_params: dict = {"window": 252}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        panel = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="gp_margin")
        if panel.empty:
            return pd.DataFrame()
        return (-1.0 * panel.rolling(window).std()).loc[ctx.start_date :]
