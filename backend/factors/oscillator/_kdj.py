"""KDJ 三线计算 helper：给定 high/low/close 宽表返回 K/D/J 宽表。

抽出来的原因：kdj_* 5 个因子都要先算 K/D/J 再做不同转换，helper 让 KDJ 定义
只写一次、变一处生效。下划线前缀 `_kdj` 防止被人误当成因子模块 import——
实际 FactorRegistry.scan_and_register 靠识别 BaseFactor 子类，不看文件名，
但下划线前缀仍是"这是包内私有 helper"的 Python 惯用信号。

公式：
- RSV_t = (close_t - min_n(low)) / (max_n(high) - min_n(low)) * 100
- K_t = (2/3) * K_{t-1} + (1/3) * RSV_t       (EMA with alpha=1/3)
- D_t = (2/3) * D_{t-1} + (1/3) * K_t         (EMA with alpha=1/3)
- J_t = 3 * K_t - 2 * D_t

向量化策略：``DataFrame.rolling(n).min() / .max()`` 跨列同步算 RSV，然后
``.ewm(alpha=1/3, adjust=False)`` 跨列同步算 K、D——全程没有 Python 层 for。
"""
from __future__ import annotations

import pandas as pd


def compute_kdj(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    n: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """计算 K / D / J 宽表。

    Args:
        high / low / close: 行=日期、列=标的的宽表。三者必须同 index 同 columns。
        n: RSV 窗口（交易日）。

    Returns:
        (K, D, J) 三个宽表，shape 与输入一致。前 n-1 行是 NaN（窗口未就绪）。
    """
    # 输入 hlc 三表的 columns / index 必须一致——否则 pandas 会做广播，
    # 产生全 NaN 列而不报错，下游因子会静默丢标的。fail-fast 比事后 debug 便宜。
    if not (high.columns.equals(low.columns) and low.columns.equals(close.columns)):
        raise ValueError("compute_kdj: high/low/close columns 必须一致")
    if not (high.index.equals(low.index) and low.index.equals(close.index)):
        raise ValueError("compute_kdj: high/low/close index 必须一致")
    # rolling 跨列一起算，min / max 自动忽略 NaN；但如果窗口内**全 NaN** 会返 NaN，
    # 这正是我们想要的（停牌段因子输出应为 NaN）。
    low_min = low.rolling(n, min_periods=n).min()
    high_max = high.rolling(n, min_periods=n).max()
    rng = high_max - low_min
    # range=0 时（极端横盘）用 NaN 替代，避免 inf；下游 ewm 遇 NaN 自然跳过。
    rng = rng.where(rng > 0)
    rsv = (close - low_min) / rng * 100

    # alpha=1/3 <=> K_t = (2/3) K_{t-1} + (1/3) RSV_t，adjust=False 保 recurrence
    # 和公式一致（adjust=True 会用整个历史做分母加权，不是经典 KDJ）。
    # ignore_na=False 让 NaN 参与但不破坏 EMA（pandas 将 NaN 视为 0 与前值的加权一致）。
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def load_hlc(ctx, warmup: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None:
    """加载 high / low / close 三宽表（所有 kdj_* 因子的共用入口）。

    为什么抽出来：
    - 5 个 kdj 因子的 compute 开头都是同样的 3× load_panel + empty check + data_start
      计算，DRY；
    - 统一 "empty guard" 行为：任一字段 load_panel 返空即返 None，调用方据此返
      `pd.DataFrame()`（和 reversal_n 的空结果语义一致）；
    - 未来要换前复权 / 不复权，改一个地方就够了。

    Args:
        ctx: FactorContext；只用到 data / symbols / start_date / end_date。
        warmup: 向左多取的自然日天数，用于 rolling / ewm 的 warm-up。

    Returns:
        (high, low, close) 宽表三元组；任一 panel 为空时返 None。
    """
    data_start = (ctx.start_date - pd.Timedelta(days=warmup)).date()
    end = ctx.end_date.date()
    high = ctx.data.load_panel(
        ctx.symbols, data_start, end, freq="1d", field="high", adjust="qfq",
    )
    low = ctx.data.load_panel(
        ctx.symbols, data_start, end, freq="1d", field="low", adjust="qfq",
    )
    close = ctx.data.load_panel(
        ctx.symbols, data_start, end, freq="1d", field="close", adjust="qfq",
    )
    if high.empty or low.empty or close.empty:
        return None
    return high, low, close
