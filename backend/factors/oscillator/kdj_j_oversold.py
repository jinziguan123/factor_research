"""KDJ J 超卖深度因子。

定义：``factor = -J``；J = 3K - 2D 是 KDJ 最灵敏的衍生线，可以跑出 0-100 之外
的尖锐值，J<<0 表示极端超卖 / J>>100 表示极端超买。取负号后"因子越大越看多"，
与平台其它 reversal 类因子约定对齐。

直觉：
- 顶部 RSV→100，K、D 收敛到 100，J = 3·100 - 2·100 = 100，因子 = -100（强空）；
- 底部 RSV→0，K、D 收敛到 0，J = 0，因子 = 0；
- 深度超卖段（RSV 刚从极低 rebound），K 先于 D 上升，J 能短暂跌破 0，因子取正的
  大数字 → 超卖反弹买点。

预期方向：反转（与 reversal_n 同向，但驱动量是"相对 N 日高低点位置"而非收益率）。
"""
from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext
from backend.factors.oscillator._kdj import compute_kdj, load_hlc


class KdjJOversold(BaseFactor):
    factor_id = "kdj_j_oversold"
    display_name = "J 超卖深度"
    category = "oscillator"
    description = "factor = -J；J 是 KDJ 最灵敏线，因子值越高表示越超卖，越看多。"
    params_schema = {
        "n": {
            "type": "int",
            "default": 9,
            "min": 3,
            "max": 60,
            "desc": "RSV 窗口（交易日）",
        }
    }
    default_params = {"n": 9}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        n = int(params.get("n", self.default_params["n"]))
        # K/D 是 alpha=1/3 的 EMA，衰减系数 2/3，3n 样本后残余 ~5%（(2/3)^27≈5%）；
        # 1.5× 交易日→自然日折算，+10 兜春节 / 国庆长假。
        return int(n * 3 * 1.5) + 10

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        n = int(params.get("n", self.default_params["n"]))
        panels = load_hlc(ctx, self.required_warmup(params))
        if panels is None:
            return pd.DataFrame()
        high, low, close = panels
        _, _, j = compute_kdj(high, low, close, n=n)
        return (-j).loc[ctx.start_date:]
