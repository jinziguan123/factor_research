"""中期上升、近期震荡、短期急跌复合形态因子。"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class UptrendSidewaysSharpSelloffFactor(BaseFactor):
    factor_id = "uptrend_sideways_sharp_selloff"
    display_name = "升势震荡后急跌"
    category = "custom"
    description = "识别中期上升、近期横盘后近3日快速下杀的形态，得分越高越符合该特征。"
    default_params = {
        "long_window": 60,
        "sideway_window": 20,
        "crash_window": 3,
        "long_min_return": 0.15,
        "sideway_abs_return_max": 0.05,
        "sideway_amplitude_max": 0.12,
        "crash_min_drop": 0.06,
    }
    params_schema = {
        "long_window": {"type": "int", "default": 60, "min": 20, "max": 252, "desc": "较长上升趋势窗口（交易日）"},
        "sideway_window": {"type": "int", "default": 20, "min": 10, "max": 60, "desc": "震荡窗口（交易日）"},
        "crash_window": {"type": "int", "default": 3, "min": 1, "max": 10, "desc": "快速下杀窗口（交易日）"},
        "long_min_return": {"type": "float", "default": 0.15, "min": 0.0, "max": 1.0, "desc": "长周期最小涨幅阈值"},
        "sideway_abs_return_max": {"type": "float", "default": 0.05, "min": 0.0, "max": 0.3, "desc": "震荡期最大绝对涨跌幅"},
        "sideway_amplitude_max": {"type": "float", "default": 0.12, "min": 0.01, "max": 0.5, "desc": "震荡期最大振幅阈值"},
        "crash_min_drop": {"type": "float", "default": 0.06, "min": 0.0, "max": 0.3, "desc": "近端快速下杀最小跌幅"},
    }
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        long_window = int(params.get("long_window", self.default_params["long_window"]))
        sideway_window = int(params.get("sideway_window", self.default_params["sideway_window"]))
        crash_window = int(params.get("crash_window", self.default_params["crash_window"]))
        total_window = long_window + sideway_window + crash_window
        return int(total_window * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        long_window = int(params.get("long_window", self.default_params["long_window"]))
        sideway_window = int(params.get("sideway_window", self.default_params["sideway_window"]))
        crash_window = int(params.get("crash_window", self.default_params["crash_window"]))
        long_min_return = float(params.get("long_min_return", self.default_params["long_min_return"]))
        sideway_abs_return_max = float(params.get("sideway_abs_return_max", self.default_params["sideway_abs_return_max"]))
        sideway_amplitude_max = float(params.get("sideway_amplitude_max", self.default_params["sideway_amplitude_max"]))
        crash_min_drop = float(params.get("crash_min_drop", self.default_params["crash_min_drop"]))

        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols,
            data_start,
            ctx.end_date.date(),
            freq="1d",
            field="close",
            adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()

        close = close.astype(float)

        long_end = close.shift(sideway_window + crash_window)
        long_start = close.shift(sideway_window + crash_window + long_window)
        long_ret = long_end / long_start - 1.0
        trend_score = ((long_ret - long_min_return) / max(long_min_return, 1e-12)).clip(lower=0.0)

        side_base = close.shift(crash_window)
        side_ret = side_base / side_base.shift(sideway_window) - 1.0
        side_roll_max = side_base.rolling(window=sideway_window, min_periods=sideway_window).max()
        side_roll_min = side_base.rolling(window=sideway_window, min_periods=sideway_window).min()
        side_amp = side_roll_max / side_roll_min - 1.0

        side_ret_score = (1.0 - (side_ret.abs() / max(sideway_abs_return_max, 1e-12))).clip(lower=0.0)
        side_amp_score = (1.0 - (side_amp / max(sideway_amplitude_max, 1e-12))).clip(lower=0.0)
        sideway_score = side_ret_score * side_amp_score

        crash_ret = close / close.shift(crash_window) - 1.0
        crash_score = (((-crash_ret) - crash_min_drop) / max(crash_min_drop, 1e-12)).clip(lower=0.0)

        factor = trend_score * sideway_score * crash_score
        return factor.loc[ctx.start_date:]
