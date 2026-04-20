from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class UptrendSidewaysFlashDrop(BaseFactor):
    factor_id = "uptrend_sideways_flash_drop"
    display_name = "上升震荡急杀反转"
    category = "reversal"
    description = "过去较长周期上升、近一月震荡且近3日快速下杀的连续反转因子。因子值越大，预期未来5日收益越正（反转假设）。"
    default_params = {
        "trend_window": 60,
        "sideways_window": 20,
        "drop_window": 3,
    }
    params_schema = {
        "trend_window": {"type": "int", "default": 60, "min": 20, "max": 252, "desc": "较长周期趋势窗口（交易日）"},
        "sideways_window": {"type": "int", "default": 20, "min": 10, "max": 60, "desc": "近端震荡识别窗口（交易日）"},
        "drop_window": {"type": "int", "default": 3, "min": 1, "max": 10, "desc": "快速下杀窗口（交易日）"},
    }
    supported_freqs = ("1d",)

    @staticmethod
    def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
        mu = df.mean(axis=1)
        sigma = df.std(axis=1)
        sigma = sigma.where(sigma > 0)
        return df.sub(mu, axis=0).div(sigma, axis=0)

    def required_warmup(self, params: dict) -> int:
        trend_window = int(params.get("trend_window", self.default_params["trend_window"]))
        sideways_window = int(params.get("sideways_window", self.default_params["sideways_window"]))
        drop_window = int(params.get("drop_window", self.default_params["drop_window"]))
        total_window = trend_window + sideways_window + drop_window
        return int(total_window * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        trend_window = int(params.get("trend_window", self.default_params["trend_window"]))
        sideways_window = int(params.get("sideways_window", self.default_params["sideways_window"]))
        drop_window = int(params.get("drop_window", self.default_params["drop_window"]))

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
        daily_ret = close.pct_change()

        long_trend = close.shift(sideways_window) / close.shift(sideways_window + trend_window) - 1.0
        recent_ret = close / close.shift(sideways_window) - 1.0
        recent_vol = daily_ret.rolling(sideways_window, min_periods=max(5, sideways_window // 2)).std()
        choppy_score = -recent_ret.abs() / (recent_vol * np.sqrt(float(sideways_window)) + 1e-12)

        fast_drop_ret = close / close.shift(drop_window) - 1.0
        crash_score = -fast_drop_ret / (recent_vol * np.sqrt(float(drop_window)) + 1e-12)

        factor = (
            self._cs_zscore(long_trend) +
            self._cs_zscore(choppy_score) +
            self._cs_zscore(crash_score)
        )
        factor = factor.replace([np.inf, -np.inf], np.nan)
        return factor.loc[ctx.start_date:].astype(float)
