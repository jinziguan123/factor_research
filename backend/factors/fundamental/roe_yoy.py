"""ROE YoY：ROE 同比改善（质量动量）。

公式：``factor_t = roe_avg_t - roe_avg_{t - lag}``，lag 默认 252 交易日（≈ 1 年）。

直觉：AQR Quality 因子家族的 "Quality Momentum"——ROE 同比改善的公司
未来超额收益高。A 股财报季前后效应显著。

注意：因 ``load_fundamental_panel`` 返回 ffill 后的日频 panel，shift(252) 不
精确对齐"同期 announcement"，会有 ±10-30 交易日的偏差。学术上 ±20 交易日的
偏差不损 IC 显著性。批次 2 可补严格按 announcement_date 对齐的版本。

预热 = 0（PIT 自带左 seed；shift 后前 lag 天 NaN，下游会过滤）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RoeYoy(BaseFactor):
    factor_id = "roe_yoy"
    display_name = "ROE 同比改善（roe_avg - shift(252)）"
    category = "fundamental"
    description = "PIT roe_avg 减去 252 交易日前的同字段（同比变化）。"
    hypothesis = "ROE 同比改善 → 质量动量 → 长期超额。"
    params_schema: dict = {
        "yoy_lag": {"type": "int", "default": 252, "min": 200, "max": 260,
                    "desc": "同比 lag（交易日，252 ≈ 1 年；A 股年均 ~243-244 交易日）"},
    }
    default_params: dict = {"yoy_lag": 252}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        lag = int(params.get("yoy_lag", self.default_params["yoy_lag"]))
        s, e = ctx.start_date.date(), ctx.end_date.date()
        panel = ctx.data.load_fundamental_panel(ctx.symbols, s, e, field="roe_avg")
        if panel.empty:
            return pd.DataFrame()
        return (panel - panel.shift(lag)).loc[ctx.start_date :]
