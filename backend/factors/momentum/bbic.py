"""BBIC（BBI / 收盘价）多空均线相对位置因子。

公式：
    BBI = (MA(close, n1) + MA(close, n2) + MA(close, n3) + MA(close, n4)) / 4
    BBIC = BBI / close

直觉：
- BBI 是 4 条不同周期均线的平均，是"中长期多空均线"的代表；
- 价格远高于 BBI（强势上涨段，均线滞后于价格）→ BBIC < 1；
- 价格远低于 BBI（深度回调段）→ BBIC > 1；
- 因此 BBIC 越大，价格相对中长期均线越低，越接近超卖反弹位置——
  方向上是反转/均值回归型信号，常被归为"动量类"技术指标。

参数：经典 BBI 是 (3, 6, 12, 24)，本实现允许 4 个窗口独立调整以支持扫参；
保持默认即等价于论文版本。窗口顺序不要求严格升序——4 条 MA 求和与求平均
对加和顺序无关（数学上可交换），但保持 n1 < n2 < n3 < n4 更符合直觉。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class BBIC(BaseFactor):
    factor_id = "bbic"
    display_name = "BBIC（BBI / 收盘价）"
    category = "momentum"
    description = (
        "BBIC = BBI(n1,n2,n3,n4) / close；BBI 为 4 条均线均值，"
        "BBIC > 1 表示价格低于多空均线（潜在超卖），< 1 表示价格高于均线（强势）。"
    )
    params_schema: dict = {
        "n1": {"type": "int", "default": 3, "min": 2, "max": 60, "desc": "MA 周期 1（短）"},
        "n2": {"type": "int", "default": 6, "min": 2, "max": 120, "desc": "MA 周期 2"},
        "n3": {"type": "int", "default": 12, "min": 2, "max": 240, "desc": "MA 周期 3"},
        "n4": {"type": "int", "default": 24, "min": 2, "max": 504, "desc": "MA 周期 4（长）"},
    }
    default_params: dict = {"n1": 3, "n2": 6, "n3": 12, "n4": 24}
    supported_freqs = ("1d",)

    def _windows(self, params: dict) -> tuple[int, int, int, int]:
        """从 params 提取 4 个 MA 周期，缺失则取 default。"""
        d = self.default_params
        return (
            int(params.get("n1", d["n1"])),
            int(params.get("n2", d["n2"])),
            int(params.get("n3", d["n3"])),
            int(params.get("n4", d["n4"])),
        )

    def required_warmup(self, params: dict) -> int:
        # 最长 MA 决定 warmup；1.5x 折自然日 + 10 天 buffer。
        return int(max(self._windows(params)) * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n1, n2, n3, n4 = self._windows(params)
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
