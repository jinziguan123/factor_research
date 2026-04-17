"""Alpha 101 风格因子：负收益平方的时序 ArgMax 横截面排名（中心化）。

公式：``(rank(Ts_ArgMax(SignedPower(((returns<0) ? |returns| : -|returns|), 2), 5)) - 0.5)``

简化推导（便于看懂实现）：

1. ``(returns<0) ? |returns| : -|returns|`` 对每个标量等价于 **-returns**——
   - 负收益 r < 0 → |r| = -r；
   - 正收益 r > 0 → -|r| = -r；
   - r = 0 两个分支都是 0。
2. ``SignedPower(x, 2) = sign(x) * |x|^2 = x * |x|``（2 次幂时符号等价于 x 本身）。
3. 两步合并：``SignedPower(-returns, 2) = (-returns) * |returns|``——
   负收益日该值为正并放大，正收益日为负并放大，幅度越大值越极端。
4. ``Ts_ArgMax(..., 5)``：5 日 rolling window 内取最大值出现的位置索引（0 = 最老，
   window-1 = 最新）。``argmax`` 越大，说明"最剧烈下跌日"越靠近当前。
5. ``rank(...)``：当日跨 symbol 做横截面百分位排名（``pct=True`` → (0, 1]）。
6. ``-0.5``：中心化到约 [-0.5, 0.5]。

捕捉直觉：窗口内"最剧烈下跌日"刚刚发生（argmax 靠后）的股票排名较高，
可能代表短期过跌、接下来几日有反弹修复机会。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class NegReturnArgmaxRank(BaseFactor):
    factor_id = "neg_return_argmax_rank"
    display_name = "负收益 ArgMax 横截面排名"
    category = "custom"
    description = (
        "(rank(Ts_ArgMax(SignedPower(-returns, 2), window)) - 0.5)；"
        "窗口内最剧烈下跌日越靠后排名越高，捕捉短期过跌反弹。"
    )
    params_schema = {
        "window": {
            "type": "int",
            "default": 5,
            "min": 2,
            "max": 60,
            "desc": "Ts_ArgMax 的时间窗口（交易日）",
        }
    }
    default_params = {"window": 5}
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        window = int(params.get("window", self.default_params["window"]))
        # 需要 1 天算 pct_change + window 天 rolling 凑齐，共 (window + 1) 交易日。
        # 1.5× 折自然日 + 5 天 buffer 兜住周末 / 小长假。
        return int((window + 1) * 1.5) + 5

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        window = int(params.get("window", self.default_params["window"]))
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

        returns = close.pct_change()
        # (returns<0 ? |r| : -|r|) * |...| 的 SignedPower(·, 2) 化简结果：
        # = (-returns) * |returns|
        # 即：负收益日 => +r^2（放大的正值），正收益日 => -r^2（放大的负值）。
        signed_sq = -returns * returns.abs()

        # rolling(window).apply 在 raw=True 下 pandas 把窗口切片成 1-D ndarray 传入；
        # 这里用 nanargmax 而不是 argmax：窗口里某些早期行因 pct_change 是 NaN，
        # 若用普通 argmax，NaN 会被当作 "not less than anything" 而可能被选中（实现取决于
        # numpy 版本），结果不稳定。nanargmax 显式忽略 NaN，窗口里至少有 1 个非 NaN 就能出
        # 一个整数位置；全 NaN 返回 NaN（我们先挡一下，避免 RuntimeWarning）。
        def _nan_argmax(arr: np.ndarray) -> float:
            if np.all(np.isnan(arr)):
                return np.nan
            return float(np.nanargmax(arr))

        # rolling 默认 min_periods=window，前 window-1 行输出 NaN，符合"预热"语义。
        argmax_pos = signed_sq.rolling(window).apply(_nan_argmax, raw=True)

        # 横截面百分位 rank：method='average' 对并列值取平均 rank，pct=True → (0, 1]。
        # 整行全 NaN（所有 symbol 都无值）的日期 rank 仍然返回全 NaN，不会被强转 0.5。
        cross_rank = argmax_pos.rank(axis=1, method="average", pct=True)

        factor = cross_rank - 0.5
        # 切回 [start_date, end_date]：如果用户 start 早于实际数据首日，
        # .loc 只返回可用日期，行为确定。
        return factor.loc[ctx.start_date :]
