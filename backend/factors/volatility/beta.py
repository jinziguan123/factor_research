"""Beta（市场贝塔）因子。

公式：60 日滚动 CAPM beta（ret = alpha + beta * mkt_ret），
mkt_ret 用横截面均值近似。取负后低 beta → 高分 → 长仓。

直觉：高 beta 股票牛市中跑赢、熊市中跑输；低 beta 组合经风险调整后收益更高
（Frazzini-Pedersen 2014, Bali-Engle-Murray 2016）。

与 idio_vol_reversal 的区别：IVOL 是**特质**波动（残差 std），beta 是**系统**
暴露。两者正交，分别覆盖低波动异象的两个维度。

预热 = ``int(window * 1.5) + 10`` 自然日。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Beta(BaseFactor):
    factor_id = "beta"
    display_name = "市场贝塔（-rolling_beta, 60d）"
    category = "volatility"
    description = "60 日滚动 CAPM beta（cs_mean 代理市场），取负后低 beta 看多。"
    hypothesis = "低 beta 股票风险调整后收益更高——低波动异象的贝塔维度（Frazzini-Pedersen 2014）。"
    params_schema: dict = {
        "window": {"type": "int", "default": 60, "min": 20, "max": 252,
                   "desc": "滚动回归窗口（交易日）"},
    }
    default_params: dict = {"window": 60}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return self._calc_warmup(window)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        mkt = ret.mean(axis=1)
        # rolling beta = rolling_cov(ret_i, mkt) / rolling_var(mkt)
        # pandas .rolling().cov() 可批量计算各列与 mkt 的协方差
        mkt_df = pd.DataFrame({"mkt": mkt.values}, index=ret.index)
        cov_with_mkt = ret.rolling(window).cov(mkt_df["mkt"])
        var_mkt = mkt.rolling(window).var().where(lambda x: x > 1e-12)
        # cov_with_mkt 是 DataFrame，columns=ret.columns，每列是 ret_i 与 mkt 的滚动协方差
        beta = cov_with_mkt.div(var_mkt, axis=0)
        factor = -beta
        return factor.loc[ctx.start_date:]
