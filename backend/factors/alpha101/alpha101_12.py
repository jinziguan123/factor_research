"""WorldQuant Alpha 101 #12（量价短期反转）。

公式：``sign(volume_t - volume_{t-1}) * (-1 * (close_t - close_{t-1}))``。

直觉：当日"放量上涨"或"缩量下跌"被视作反转信号——sign(Δvol) 给量方向，
(-Δclose) 给反向涨跌。两者乘积 = 短期反转预期。最简单的 1 行 Alpha101 因子。

预热 = 3 自然日（diff 1 + safety）。

停牌处理：volume.diff(1)==0（连续两日 volume 相同，最常见情形是停牌）→ 视作缺失，
        因子输出 NaN，不参与 cross-section rank。如果硬留 sign(0)=0，会让停牌
        股票被错误地排在 cross-section 中位数附近。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_12(BaseFactor):
    factor_id = "alpha101_12"
    display_name = "Alpha101 #12（量价短期反转）"
    category = "alpha101"
    description = "sign(Δvolume) * (-Δclose)；放量上涨视作反转信号。"
    hypothesis = "Δvolume 与 Δclose 同向时反转概率高；取负后高分 → 多头预期。"
    params_schema: dict = {}
    default_params: dict = {}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        return 3

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        warmup = self.required_warmup(params)
        data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
        close = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="close", adjust="qfq",
        )
        volume = ctx.data.load_panel(
            ctx.symbols, data_start, ctx.end_date.date(),
            freq="1d", field="volume", adjust="none",
        )
        if close.empty or volume.empty:
            return pd.DataFrame()
        close, volume = close.align(volume, join="outer")
        # Δvol==0 视作缺失（最常见情形是停牌：volume=0 连续两日 → Δ=0）
        # 否则 sign(0)=0 会让停牌股在 cross-section 排到中位数附近，污染 rank
        dvol = volume.diff(1)
        dvol = dvol.where(dvol != 0)
        factor = np.sign(dvol) * (-close.diff(1))
        return factor.loc[ctx.start_date :]
