from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RapidBreakdownSupport(BaseFactor):
    factor_id = "rapid_breakdown_support"
    display_name = "急跌破位因子"
    category = "momentum"
    description = "刻画短期快速下杀并跌破近三个月支撑的弱势形态，因子值越大，预期未来5日收益越负（下破延续假设）。"
    default_params = {
        "support_window": 60,
        "fast_drop_window": 7,
        "trend_window": 20,
        "volume_window": 20,
    }
    params_schema = {
        "support_window": {"type": "int", "default": 60, "min": 20, "max": 120, "desc": "支撑参考窗口（交易日，近三个月约60日）"},
        "fast_drop_window": {"type": "int", "default": 7, "min": 2, "max": 20, "desc": "短期快速下杀窗口（交易日）"},
        "trend_window": {"type": "int", "default": 20, "min": 5, "max": 60, "desc": "趋势均线窗口（交易日）"},
        "volume_window": {"type": "int", "default": 20, "min": 5, "max": 60, "desc": "成交量均值窗口（交易日）"},
    }
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        support_window = int(params.get("support_window", self.default_params["support_window"]))
        fast_drop_window = int(params.get("fast_drop_window", self.default_params["fast_drop_window"]))
        trend_window = int(params.get("trend_window", self.default_params["trend_window"]))
        volume_window = int(params.get("volume_window", self.default_params["volume_window"]))
        lookback = max(support_window + 1, fast_drop_window + 1, trend_window * 2, volume_window + 1)
        return int(lookback * 1.5) + 10

    @staticmethod
    def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
        mu = df.mean(axis=1)
        sigma = df.std(axis=1)
        sigma = sigma.where(sigma > 0)
        return df.sub(mu, axis=0).div(sigma, axis=0)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        support_window = int(params.get("support_window", self.default_params["support_window"]))
        fast_drop_window = int(params.get("fast_drop_window", self.default_params["fast_drop_window"]))
        trend_window = int(params.get("trend_window", self.default_params["trend_window"]))
        volume_window = int(params.get("volume_window", self.default_params["volume_window"]))

        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        data_end = ctx.end_date.date()

        close = ctx.data.load_panel(
            ctx.symbols, data_start, data_end, freq="1d", field="close", adjust="qfq"
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, data_end, freq="1d", field="low", adjust="qfq"
        )
        volume = ctx.data.load_panel(
            ctx.symbols, data_start, data_end, freq="1d", field="volume", adjust="none"
        )

        if close.empty or low.empty or volume.empty:
            return pd.DataFrame()

        close = close.astype(float)
        low = low.astype(float)
        volume = volume.astype(float)

        fast_drop = -(close / close.shift(fast_drop_window) - 1.0)

        support_level = low.shift(1).rolling(window=support_window, min_periods=max(5, support_window // 3)).min()
        break_depth = support_level / close - 1.0
        break_depth = pd.DataFrame(np.tanh(break_depth * 5.0), index=break_depth.index, columns=break_depth.columns)

        trend_ref = close.ewm(span=trend_window, adjust=False, min_periods=trend_window).mean()
        trend_break = trend_ref / close - 1.0
        trend_break = pd.DataFrame(np.tanh(trend_break * 4.0), index=trend_break.index, columns=trend_break.columns)

        avg_volume = volume.rolling(window=volume_window, min_periods=max(5, volume_window // 2)).mean()
        volume_confirm = np.log1p(volume / avg_volume.replace(0.0, np.nan))

        intraday_break = close / support_level - 1.0
        intraday_break = -pd.DataFrame(np.tanh(intraday_break * 4.0), index=intraday_break.index, columns=intraday_break.columns)

        factor = (
            self._cs_zscore(fast_drop)
            + self._cs_zscore(break_depth)
            + 0.7 * self._cs_zscore(trend_break)
            + 0.5 * self._cs_zscore(volume_confirm)
            + 0.5 * self._cs_zscore(intraday_break)
        )

        factor = factor.replace([np.inf, -np.inf], np.nan)
        return factor.loc[ctx.start_date:]
