"""BBIC（BBI / 收盘价）多空均线相对位置因子。

公式：
    BBI = (MA(close, 3) + MA(close, 6) + MA(close, 12) + MA(close, 24)) / 4
    BBIC = BBI / close

直觉：
- BBI 是 4 条不同周期均线的平均，是"中长期多空均线"的代表；
- 价格远高于 BBI（强势上涨段，均线滞后于价格）→ BBIC < 1；
- 价格远低于 BBI（深度回调段）→ BBIC > 1；
- 因此 BBIC 越大，价格相对中长期均线越低，越接近超卖反弹位置——
  方向上是反转/均值回归型信号，常被归为"动量类"技术指标。

参数固定为经典 (3, 6, 12, 24)：BBI 文献几十年沿用这一组合，需要扫参时
再扩 schema；保持与 alpha101 系列一致的"零超参"风格。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class BBIC(BaseFactor):
    factor_id = "bbic"
    display_name = "BBIC（BBI / 收盘价）"
    category = "momentum"
    description = (
        "BBIC = BBI(3,6,12,24) / close；BBI 为 4 条均线均值，"
        "BBIC > 1 表示价格低于多空均线（潜在超卖），< 1 表示价格高于均线（强势）。"
    )
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    # BBI 周期固定 (3, 6, 12, 24)；最长 24 个交易日 + 1.5x 折自然日 + 10 天 buffer。
    _WINDOWS = (3, 6, 12, 24)

    def required_warmup(self, params: dict) -> int:
        return int(max(self._WINDOWS) * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
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

        # rolling 默认 min_periods=window，前 window-1 行 NaN，符合预热语义。
        # 4 个均线相加再除 4 等价于 BBI 定义；元素级 NaN 传播保证停牌段不强行出值。
        n1, n2, n3, n4 = self._WINDOWS
        bbi = (
            close.rolling(n1).mean()
            + close.rolling(n2).mean()
            + close.rolling(n3).mean()
            + close.rolling(n4).mean()
        ) / 4

        # close=0 在 A 股复权后理论上不会出现（除非脏数据）；这里不做特殊兜底，
        # 让除以 0 自然出 inf，便于后续在 IC / 绘图时一眼识别异常股票。
        factor = bbi / close
        return factor.loc[ctx.start_date :]
