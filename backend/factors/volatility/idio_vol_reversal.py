"""IdioVolReversal：特质波动率反转（IVOL 异象）。

公式：
  ret      = close.pct_change()
  mkt      = ret.mean(axis=1)           # 横截面均值近似市场收益
  residual = ret - mkt
  factor   = -1 * rolling_std(residual, window=60)

直觉：Ang-Hodrick-Xing-Zhang 2006 IVOL 异象——特质波动越高，未来收益越低。
A 股 Cao-Han 等多个研究确认。取负后高分股 → 低 IVOL → 长仓预期。

为何用横截面均值代替指数：A 股没拉沪深300/中证500 这种基准。横截面均值
在 universe 充分大（≥ 100 票）时统计上等价于市场收益的无偏估计（CAPM 视角）。

预热 = ret_window + 5 个交易日折算自然日 ≈ ``int(ret_window * 1.5) + 10``。

NaN 行为：``rolling(window).std()`` 默认 ``min_periods=window``，窗口内任意 NaN
（停牌 / 上市未满 window 日）→ 该日因子 NaN；**NaN 影响范围 = window 个交易日**。
``pct_change(fill_method=None)`` 仅保证当日 ret 不被 ffill 伪造，不消除 rolling
窗口的 NaN 传染性。下游 evaluate 自带 cross-section valid mask，会自动剔除 NaN
位置，不污染信号。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class IdioVolReversal(BaseFactor):
    factor_id = "idio_vol_reversal"
    display_name = "特质波动率反转（-std(ret - cs_mean, 30)）"
    category = "volatility"
    description = (
        "对 close.pct_change 减去横截面均值得残差，再取 30 日 rolling std 取负。"
    )
    hypothesis = "高 IVOL 未来收益更低（IVOL 异象）；取负使高分 → 长仓。"
    params_schema: dict = {
        "ret_window": {"type": "int", "default": 30, "min": 20, "max": 252,
                       "desc": "rolling std 窗口（交易日）"},
    }
    default_params: dict = {"ret_window": 30}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        w = int(params.get("ret_window", self.default_params["ret_window"]))
        return self._calc_warmup(w)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        w = int(params.get("ret_window", self.default_params["ret_window"]))
        close = self._load_close_panel(ctx, params)
        if close is None:
            return pd.DataFrame()
        ret = close.pct_change(fill_method=None)
        mkt = ret.mean(axis=1)                  # cross-section mean
        residual = ret.sub(mkt, axis=0)         # 每行减市场
        factor = -residual.rolling(w).std()
        return factor.loc[ctx.start_date :]
