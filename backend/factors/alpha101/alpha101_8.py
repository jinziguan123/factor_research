"""WorldQuant Alpha 101 #8。

公式：
    (-1 * rank(((sum(open, sum_window) * sum(returns, sum_window))
                - delay((sum(open, sum_window) * sum(returns, sum_window)), delay_periods))))

逐步分解：
1. ``S_t = sum(open, sum_window) * sum(returns, sum_window)``：近 N 日开盘价之和
   × 近 N 日收益和。两者乘积可视为"近期价位水平 × 近期累计收益强度"的复合度量。
2. ``D_t = S_t - delay(S_t, delay_periods) = S_t - S_{t-D}``：当前与 D 日前同口径值的差。
3. ``rank(D_t)``：当日跨 symbol 横截面百分位排名（``pct=True`` → (0, 1]）。
4. 整体取负号：差值越大 → rank 越大 → 因子越小。

直觉：复合度量短期"上行变化"越剧烈的股票因子越小，方向上偏反转。

参数：原始论文公式 (sum_window=5, delay_periods=10)。本实现允许微调以支持扫参研究；
保持默认即等价于论文版本。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class Alpha101_8(BaseFactor):
    factor_id = "alpha101_8"
    display_name = "Alpha101 #8（开盘×收益反转）"
    category = "alpha101"
    description = (
        "(-1 * rank((sum(open,N)*sum(returns,N)) "
        "- delay((sum(open,N)*sum(returns,N)), D)))；"
        "默认 N=5, D=10 即论文标准 Alpha 101 第 8 号因子。"
    )
    hypothesis = "开盘价×收益的复合度量短期上行变化越剧烈，未来反转越强——Alpha101 #8 反转信号。"
    params_schema: dict = {
        "sum_window": {
            "type": "int",
            "default": 5,
            "min": 2,
            "max": 60,
            "desc": "sum(open,N) / sum(returns,N) 的滚动求和窗口",
        },
        "delay_periods": {
            "type": "int",
            "default": 10,
            "min": 1,
            "max": 60,
            "desc": "delay 滞后期 D（X_t - X_{t-D}）",
        },
    }
    default_params: dict = {"sum_window": 5, "delay_periods": 10}
    supported_freqs = ("1d",)

    def _params(self, params: dict) -> tuple[int, int]:
        """提取 (sum_window, delay_periods)，缺失则取 default。"""
        d = self.default_params
        return (
            int(params.get("sum_window", d["sum_window"])),
            int(params.get("delay_periods", d["delay_periods"])),
        )

    def required_warmup(self, params: dict) -> int:
        sum_w, delay_p = self._params(params)
        return self._calc_warmup(sum_w + delay_p + 1, buffer_days=5)

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        sum_w, delay_p = self._params(params)
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

        # rolling(N) 默认 min_periods=N，前 N-1 行 NaN，符合预热语义。
        sum_open = open_.rolling(sum_w).sum()
        sum_ret = returns.rolling(sum_w).sum()
        compound = sum_open * sum_ret

        # delay(X, D) ↔ X.shift(D)：对每个 symbol 时间序列前移 D 个交易日。
        diff = compound - compound.shift(delay_p)

        # 横截面 rank：axis=1 跨 symbol，pct=True → (0, 1]。整行全 NaN 仍为 NaN。
        cross_rank = diff.rank(axis=1, method="average", pct=True)

        factor = -cross_rank
        return factor.loc[ctx.start_date :]
