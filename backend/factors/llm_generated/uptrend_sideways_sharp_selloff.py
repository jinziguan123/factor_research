"""中期升势 + 近期横盘后出现短期急跌 → 预期短期反弹（反转假设）。

方向假设：因子值越大，预期未来 1 日收益越正。

相比初版改动：
- 去掉 `.clip(lower=0)` 三元乘积（那是稀疏信号的罪魁祸首：绝大多数样本恰好为 0）。
- 三个子特征（中期涨幅 / 横盘紧度 / 急跌强度）各自按日期做截面 z-score 后相加，
  量纲对齐、每个子项贡献相同权重，最终因子在截面上有稠密方差。
- 丢掉"必须涨 15%、振幅不超 12%、跌幅至少 6%"这类硬阈值参数：截面排序自动完成相对强弱判断。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


def _cs_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """按日期做截面 z-score。截面 std=0（所有股票同值）的日期整行变 NaN，留给评估层丢弃。"""
    mu = df.mean(axis=1)
    sigma = df.std(axis=1)
    return df.sub(mu, axis=0).div(sigma.where(sigma > 0), axis=0)


class UptrendSidewaysSharpSelloffFactor(BaseFactor):
    factor_id = "uptrend_sideways_sharp_selloff"
    display_name = "升势震荡后急跌反弹"
    category = "reversal"
    description = "中期上升 + 近期横盘后出现短期急跌；因子值越大预期未来 1 日收益越正（反转假设）。"
    default_params = {
        "long_window": 60,
        "sideway_window": 20,
        "crash_window": 3,
    }
    params_schema = {
        "long_window": {"type": "int", "default": 60, "min": 20, "max": 252, "desc": "中期上升趋势窗口（交易日）"},
        "sideway_window": {"type": "int", "default": 20, "min": 10, "max": 60, "desc": "横盘震荡窗口（交易日）"},
        "crash_window": {"type": "int", "default": 3, "min": 1, "max": 10, "desc": "快速下杀窗口（交易日）"},
    }
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        long_window = int(params.get("long_window", self.default_params["long_window"]))
        sideway_window = int(params.get("sideway_window", self.default_params["sideway_window"]))
        crash_window = int(params.get("crash_window", self.default_params["crash_window"]))
        total = long_window + sideway_window + crash_window
        return int(total * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        long_window = int(params.get("long_window", self.default_params["long_window"]))
        sideway_window = int(params.get("sideway_window", self.default_params["sideway_window"]))
        crash_window = int(params.get("crash_window", self.default_params["crash_window"]))

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

        # 1. 中期涨幅：窗口 [t - sideway - crash - long, t - sideway - crash]
        long_end = close.shift(sideway_window + crash_window)
        long_start = close.shift(sideway_window + crash_window + long_window)
        trend_feat = long_end / long_start - 1.0

        # 2. 横盘紧度：sideway_window 内价格的相对振幅取负——越紧 = 分越高
        side_base = close.shift(crash_window)
        side_roll_max = side_base.rolling(sideway_window, min_periods=sideway_window).max()
        side_roll_min = side_base.rolling(sideway_window, min_periods=sideway_window).min()
        sideway_feat = -(side_roll_max / side_roll_min - 1.0)

        # 3. 急跌强度：crash_window 内的负收益——跌得越狠 = 分越高
        crash_feat = -(close / close.shift(crash_window) - 1.0)

        # 截面 z-score 对齐量纲后相加；三个维度等权，最终因子在截面上有稠密方差。
        factor = _cs_zscore(trend_feat) + _cs_zscore(sideway_feat) + _cs_zscore(crash_feat)
        return factor.loc[ctx.start_date:]
