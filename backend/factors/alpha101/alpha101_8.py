"""WorldQuant Alpha 101 #8。

公式：
    (-1 * rank(((sum(open, 5) * sum(returns, 5))
                - delay((sum(open, 5) * sum(returns, 5)), 10))))

逐步分解：
1. ``S_t = sum(open, 5) * sum(returns, 5)``：近 5 日开盘价之和 × 近 5 日收益和。
   两者乘积可视为"近期价位水平 × 近期累计收益强度"的复合度量。
2. ``D_t = S_t - delay(S_t, 10) = S_t - S_{t-10}``：当前与 10 日前同口径值的差。
3. ``rank(D_t)``：当日跨 symbol 横截面百分位排名（``pct=True`` → (0, 1]）。
4. 整体取负号：差值越大 → rank 越大 → 因子越小。

直觉：复合度量短期"上行变化"越剧烈的股票因子越小，方向上偏反转。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_8(BaseFactor):
    factor_id = "alpha101_8"
    display_name = "Alpha101 #8（开盘×收益反转）"
    category = "alpha101"
    description = (
        "(-1 * rank((sum(open,5)*sum(returns,5)) "
        "- delay((sum(open,5)*sum(returns,5)), 10)))；"
        "WorldQuant Alpha 101 第 8 号因子，固定参数。"
    )
    # 论文公式参数固定，不暴露超参；后续若要扫参再加 schema。
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    # 数据需要：rolling sum 5 + delay 10 + pct_change 1 ≈ 16 个交易日。
    # 1.5× 折自然日 + 5 天 buffer 兜节假日。
    _WARMUP_TRADE_DAYS = 16

    def required_warmup(self, params: dict) -> int:
        return int(self._WARMUP_TRADE_DAYS * 1.5) + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()

        open_ = ctx.data.load_panel(
            ctx.symbols,
            data_start,
            ctx.end_date.date(),
            freq="1d",
            field="open",
            adjust="qfq",
        )
        close = ctx.data.load_panel(
            ctx.symbols,
            data_start,
            ctx.end_date.date(),
            freq="1d",
            field="close",
            adjust="qfq",
        )
        if open_.empty or close.empty:
            return pd.DataFrame()

        # 对齐两张面板的 index/columns，避免某只票仅在其一中出现导致 NaN 漂移。
        open_, close = open_.align(close, join="outer")

        # fill_method=None：停牌日 close=NaN → returns=NaN，不做 ffill 偏置。
        returns = close.pct_change(fill_method=None)

        # rolling(5) 默认 min_periods=5，前 4 行 NaN，符合预热语义。
        sum_open = open_.rolling(5).sum()
        sum_ret = returns.rolling(5).sum()
        compound = sum_open * sum_ret

        # delay(X, 10) ↔ X.shift(10)：对每个 symbol 时间序列前移 10 个交易日。
        diff = compound - compound.shift(10)

        # 横截面 rank：axis=1 跨 symbol，pct=True → (0, 1]。整行全 NaN 仍为 NaN。
        cross_rank = diff.rank(axis=1, method="average", pct=True)

        factor = -cross_rank
        return factor.loc[ctx.start_date :]
