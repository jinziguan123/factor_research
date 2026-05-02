from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class UptrendSidewaysFlashDrop(BaseFactor):
    factor_id = "uptrend_sideways_flash_drop"
    display_name = "上升震荡急杀反转"
    category = "reversal"
    description = "过去较长周期上升、近一月高低价窄幅震荡、近3日快速下杀的连续反转因子。因子值越大（三分量百分位秩之积越大），预期未来5日收益越正（反转假设）。"
    hypothesis = "上涨震荡后的急杀——上升趋势中短期恐慌提供反转买点。"
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

    # 日收益率 std 下限：A 股停牌/一字涨停票 std 会到 1e-5~1e-8，
    # 加个绝对下限防止 crash_score 分母爆破把截面 z-score/rank 整个拉偏。
    _VOL_FLOOR = 5e-3

    @staticmethod
    def _winsor_cs(df: pd.DataFrame, lo: float = 0.01, hi: float = 0.99) -> pd.DataFrame:
        """按日期（行）做 1%~99% 截面 winsorize，抹掉尾部异常。"""
        ql = df.quantile(lo, axis=1)
        qh = df.quantile(hi, axis=1)
        return df.clip(lower=ql, upper=qh, axis=0)

    def required_warmup(self, params: dict) -> int:
        trend_window = int(params.get("trend_window", self.default_params["trend_window"]))
        sideways_window = int(params.get("sideways_window", self.default_params["sideways_window"]))
        drop_window = int(params.get("drop_window", self.default_params["drop_window"]))
        total_window = trend_window + sideways_window + drop_window
        return self._calc_warmup(total_window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        trend_window = int(params.get("trend_window", self.default_params["trend_window"]))
        sideways_window = int(params.get("sideways_window", self.default_params["sideways_window"]))
        drop_window = int(params.get("drop_window", self.default_params["drop_window"]))

        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()

        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        if close.empty:
            return pd.DataFrame()
        high = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="high", adjust="qfq",
        )
        low = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="low", adjust="qfq",
        )

        close = close.astype(float)
        high = high.astype(float)
        low = low.astype(float)
        daily_ret = close.pct_change()

        min_p = max(5, sideways_window // 2)

        # 分量 1：长周期趋势——[t-sw-tw, t-sw] 区间累计收益率。
        long_trend = close.shift(sideways_window) / close.shift(sideways_window + trend_window) - 1.0

        # 分量 2：高低价带宽 / 起点价——这是真正的"震荡/窄幅"度量。
        # 原版用 -|recent_ret|/(vol·√T)，实际是"单边涨跌幅的归一路径"，无法识别路径颠簸，
        # 连续上涨和连续下跌都会给相同的负分，语义不符。改成近 sw 日 high.max - low.min。
        # 越窄 → 越震荡 → 我们越想要，所以取负做 choppy_score。
        range_high = high.rolling(sideways_window, min_periods=min_p).max()
        range_low = low.rolling(sideways_window, min_periods=min_p).min()
        base_px = close.shift(sideways_window).abs().replace(0, np.nan)
        sideways_amp = (range_high - range_low) / base_px
        choppy_score = -sideways_amp

        # 分量 3：近 drop_window 日累计跌幅 / 同期归一波动（波动带 VOL_FLOOR）。
        recent_vol = daily_ret.rolling(sideways_window, min_periods=min_p).std()
        recent_vol_safe = recent_vol.clip(lower=self._VOL_FLOOR)
        fast_drop_ret = close / close.shift(drop_window) - 1.0
        crash_score = -fast_drop_ret / (recent_vol_safe * np.sqrt(float(drop_window)))

        # 每分量先做截面 winsorize 抹极端值，再转百分位秩 [0,1]。
        long_trend = self._winsor_cs(long_trend)
        choppy_score = self._winsor_cs(choppy_score)
        crash_score = self._winsor_cs(crash_score)

        # AND 语义：三个条件"同时满足"才算目标票，用乘法而非加法。
        # 加法会让单分量突出的票（比如只涨不震荡）和目标票得分一样，稀释 AND 语义。
        long_rank = long_trend.rank(axis=1, pct=True)
        sides_rank = choppy_score.rank(axis=1, pct=True)
        crash_rank = crash_score.rank(axis=1, pct=True)

        factor = long_rank * sides_rank * crash_rank
        factor = factor.replace([np.inf, -np.inf], np.nan)
        return factor.loc[ctx.start_date:].astype(float)
