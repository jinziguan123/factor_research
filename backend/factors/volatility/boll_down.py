"""布林线下轨因子（Bollinger Band Lower）。

定义：``factor_t = (MA(CLOSE, window) - 2 * STD(CLOSE, window)) / CLOSE_t``，
即过去 window 日收盘价的 "下轨价 / 当日收盘价" 比值。

直觉：因子值越大，说明收盘价越靠近（甚至跌破）布林下轨——相对于中枢往下偏离
2 个标准差以上，隐含超跌反弹机会；越小则说明价格远在下轨上方。

**预处理约定**：本因子只产出原始值。项目里"中位数去极值 → 行业市值对数中性化
→ zscore 标准化"这套流水线应在后续的合成 / 评估层统一做（参考
``backend/services/composition_service.py`` 的 ``_zscore_per_day``），而非每个
因子自己塞一份。行业和市值数据目前未接入，待接入后会由全局 util 提供。

预热期 = ``int(window * 1.5) + 10`` 自然日（交易日 → 自然日折算 + 长假 buffer）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class BollDown(BaseFactor):
    factor_id = "boll_down"
    display_name = "布林线下轨比值"
    category = "volatility"
    description = (
        "(MA(close, window) - 2*STD(close, window)) / close；"
        "值越大表示收盘价越贴近下轨，超跌反弹信号。"
    )
    hypothesis = "收盘价靠近布林下轨表示过度悲观，均值回归预期支撑反弹——布林带统计套利。"
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 5,
            "max": 120,
            "desc": "MA / STD 滚动窗口（交易日）",
        }
    }
    default_params = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        ma = close.rolling(window).mean()
        std = close.rolling(window).std()
        lower = ma - 2.0 * std
        factor = lower / close
        return factor.loc[ctx.start_date:]
