"""换手率代理因子（Turnover-Ratio Proxy）。

目前 stock_bar_1d 里没有独立的流通股本 / 换手率字段，这里用
``amount_k 均值 / close 均值`` 做代理，数值大小反映该股的"日成交额 / 价"比值，
能近似刻画不同股票的相对流动性差异。

- ``amount_k`` 必须读未复权（``adjust="none"``）——因子是单位千元，本身不应被复权缩放；
- ``close`` 读 qfq 前复权——保持和其它 close 因子的参照一致；
- 两个序列取同一 rolling window 均值后相除。

预热期 = ``window + 5`` 自然日。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class TurnoverRatio(BaseFactor):
    factor_id = "turnover_ratio"
    display_name = "换手率代理（amount/close）"
    category = "volume"
    description = (
        "用 rolling window 上的日成交额均值 / 前复权 close 均值做流动性代理。"
        "数值高代表该股近期活跃。"
    )
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 2,
            "max": 252,
            "desc": "滚动窗口（交易日）",
        }
    }
    default_params = {"window": 20}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        return window + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        amt = ctx.data.load_panel(
            ctx.symbols,
            data_start,
            ctx.end_date.date(),
            freq="1d",
            field="amount_k",
            adjust="none",
        )
        close = ctx.data.load_panel(
            ctx.symbols,
            data_start,
            ctx.end_date.date(),
            freq="1d",
            field="close",
            adjust="qfq",
        )
        if amt.empty or close.empty:
            return pd.DataFrame()
        # rolling window 对齐；分母理论上不会为 0（close>0），但若数据中出现
        # 停牌置零，rolling 均值也可能为 0 —— 这种病态股票会产出 inf，交给上层
        # winsorize / z-score 处理。
        factor = amt.rolling(window).mean() / close.rolling(window).mean()
        return factor.loc[ctx.start_date :]
