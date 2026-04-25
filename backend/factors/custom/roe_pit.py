"""RoePit：基于 baostock 财报 PIT 的 ROE 因子。

定义：每个交易日 t 的因子值 = symbol 在 ``announcement_date <= t`` 的最近一期
``fr_fundamental_profit.roe_avg``。披露之前的交易日为 NaN。

实现要点：
- ``announcement_date`` 当日就视为可用（与现有量价因子的 T+0 信号粒度对齐）。
  若需保守口径，未来可在出口加 ``shift(1)`` 一档。
- 财报数据稀疏（季频），ffill 在 DataService.load_fundamental_panel 内统一做。
- 无需 warmup：``announcement_date <= start_date`` 的最近一条已被 panel 携带
  （load_fundamental_panel 内部用 union(cal_index) 保证左 seed 不丢）。
- 返回的 ``columns`` 可能是 ``ctx.symbols`` 的真子集——若某只 symbol 在窗口
  ``end_date`` 之前从未披露过 profit（或字段全为 NULL），它会从 panel 里彻底
  缺席而非补 NaN 列。下游 IC 计算会自动按 columns 取交集，此处不主动 reindex。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class RoePit(BaseFactor):
    factor_id = "roe_pit"
    display_name = "ROE (PIT, 季度披露 ffill 到日频)"
    category = "custom"
    description = (
        "baostock fr_fundamental_profit.roe_avg，按 announcement_date 在交易日维度 ffill。"
    )
    params_schema = {}
    default_params = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 0

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        panel = ctx.data.load_fundamental_panel(
            ctx.symbols,
            ctx.start_date.date(),
            ctx.end_date.date(),
            field="roe_avg",
        )
        if panel.empty:
            return pd.DataFrame()
        # 防御切片：DataService.load_fundamental_panel 内部已 reindex 到
        # cal_index（[start, end] 内的交易日），此处切片在生产路径下是 no-op；
        # 若 ctx.start_date 是非交易日 timestamp，这步把对齐再确认一遍。
        return panel.loc[ctx.start_date :]
