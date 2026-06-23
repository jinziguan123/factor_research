"""回测执行模型：把"目标权重"翻译成贴近 A 股实盘的成交价 / 费用 / 滑点 / 容量。

**纯函数，无 DB 依赖**，便于单测（与 ``metrics.py`` 同风格）。所有宽表入参在调用前
应已按 ``(date × symbol)`` 对齐（同 index / columns）。设计依据见
``docs/plans/2026-06-23-backtest-realism-redesign.md``。

核心思路：
- T 日盘后定目标组合，T+1 成交 → ``shift_for_t1``；
- 成交价取 T+1 ``open``（默认）或复权典型价 VWAP；
- A 股成本不对称（卖出含印花税）→ ``build_fee_array`` 构造方向相关费率数组；
- 滑点 = 固定 bp + 平方根市场冲击（量相关）→ ``build_slippage_array``；
- 单日成交额不超过当日成交额的 k% → ``apply_volume_cap`` 逐行路径裁剪。

非有限值处理沿用项目约定：在比率 / 冲击中以 0 兜底，避免把 NaN / inf 灌进 VectorBT。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def shift_for_t1(w: pd.DataFrame) -> pd.DataFrame:
    """把调仓日（T 日）的目标权重整体后移一行，实现 T+1 成交。

    语义：``w.loc[T]`` 是 T 日盘后定下的目标权重（已用到 T 日 close），实际最早
    只能在 T+1 成交，因此目标持仓应在 T+1 这一行生效。首行（无前序信号）补 0，
    表示空仓起步。

    Args:
        w: 权重宽表，index=trade_date，columns=symbol。

    Returns:
        与 ``w`` 同 shape 的宽表，整体下移一行，首行全 0。空表原样返回。
    """
    if w.empty:
        return w.copy()
    return w.shift(1).fillna(0.0)


def build_exec_price(
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    mode: str = "open",
) -> pd.DataFrame:
    """构造成交价宽表。

    Args:
        open_ / high / low / close: 已对齐的复权价宽表（同 index / columns）。
        mode: ``"open"`` 用开盘价（最诚实、无前视）；``"vwap"`` 用复权典型价
            ``(high + low + close) / 3``（量纲与复权价自洽，作为日内成交均价代理；
            真实 amount/volume VWAP 列入 roadmap）。

    Returns:
        成交价宽表，与入参同 shape。

    Raises:
        ValueError: mode 非法（尽早暴露拼写错误）。
    """
    if mode == "open":
        return open_.copy()
    if mode == "vwap":
        return (high + low + close) / 3.0
    raise ValueError(f"exec_price mode 必须是 'open' 或 'vwap'，收到 {mode!r}")


def build_fee_array(
    w_exec: pd.DataFrame,
    commission_bps: float,
    stamp_tax_bps: float,
    transfer_fee_bps: float,
) -> np.ndarray:
    """构造方向相关的不对称费率数组（A 股：卖出额外收印花税）。

    VectorBT ``fees`` 对买卖同费率，无法直接表达"卖出额外印花税"。这里按
    ``w_exec.diff()`` 的符号区分买卖：负 = 卖出用 ``sell_fee``，其余（买入 / 不变）
    用 ``buy_fee``（不变的位置不会真正成交，费率不影响结果）。

    Args:
        w_exec: 已 T+1 平移的权重宽表（``shift_for_t1`` 的输出）。
        commission_bps: 佣金，双边（bp）。
        stamp_tax_bps: 印花税，仅卖出（bp）。
        transfer_fee_bps: 过户费，双边（bp）。

    Returns:
        与 ``w_exec`` 同 shape 的 float64 数组，每个位置 = "若此处成交的费率"。

    Note:
        ``targetamount + cash_sharing`` 下，VectorBT 实际成交方向受可用现金约束，
        可能与 ``w_exec.diff()`` 符号不完全一致（现金不足导致少买）。绝大多数调仓
        场景一致，作为一阶近似可接受。
    """
    buy_fee = (commission_bps + transfer_fee_bps) / 1e4
    sell_fee = (commission_bps + stamp_tax_bps + transfer_fee_bps) / 1e4
    # 首行 diff 为 NaN，fillna(0) 后视为"不变"→ buy_fee（首行 w_exec 已是 0，无成交，无害）。
    dw = w_exec.diff().fillna(0.0).values
    return np.where(dw < 0, sell_fee, buy_fee).astype("float64")


def build_slippage_array(
    w_exec: pd.DataFrame,
    init_cash: float,
    daily_amount: pd.DataFrame,
    exec_price: pd.DataFrame,
    slippage_bps: float,
    impact_coef: float,
) -> np.ndarray:
    """固定滑点 + 平方根市场冲击合成 slippage 数组。

    VectorBT 的 ``slippage`` 按比例调整成交价且**自动按方向施加**（买价上浮 / 卖价
    下浮），所以这里只需给出非负的滑点比例。

    冲击（Almgren 简化）：``impact = impact_coef * sqrt(order_value / daily_amount)``，
    其中 ``order_value = |Δw| * init_cash`` 为当期成交额，``daily_amount`` 为当日成交额。

    Args:
        w_exec: 已 T+1 平移的权重宽表。
        init_cash: 初始资金（元），用于把权重变化折算成成交额。
        daily_amount: 当日成交额宽表（元）。
        exec_price: 成交价宽表（此实现未直接用，预留给"按价档分层滑点"扩展）。
        slippage_bps: 固定滑点，双边（bp）。
        impact_coef: 平方根冲击系数；0 表示关闭冲击。

    Returns:
        与 ``w_exec`` 同 shape 的 float64 数组，值为非负滑点比例。
    """
    base_slip = slippage_bps / 1e4
    order_value = (w_exec.diff().abs().fillna(0.0).values) * init_cash
    amt = np.nan_to_num(daily_amount.values.astype("float64"), nan=0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(amt > 0, order_value / amt, 0.0)
        impact = impact_coef * np.sqrt(np.clip(ratio, 0.0, None))
    impact = np.nan_to_num(impact, nan=0.0, posinf=0.0, neginf=0.0)
    return (base_slip + impact).astype("float64")


def apply_volume_cap(
    target_size: pd.DataFrame,
    daily_amount: pd.DataFrame,
    exec_price: pd.DataFrame,
    max_volume_pct: float,
) -> pd.DataFrame:
    """逐行路径裁剪：单票单日成交额 ≤ ``max_volume_pct × 当日成交额``。

    因为"实际持仓"依赖前一行裁剪后的结果（路径依赖），用逐行扫描（外层循环交易日，
    内层向量化所有 symbol）。被限制的买入会留存现金（符合真实"买不到那么多"）；
    停牌日 ``daily_amount=0`` → ``cap=0`` → 持仓不变（自动处理停牌不可交易）。

    统一用成交额（元）口径，避开 ``volume`` 的"股 vs 手"单位歧义。

    Args:
        target_size: 目标持仓股数宽表（``W_exec * init_cash / exec_price``）。
        daily_amount: 当日成交额宽表（元）。
        exec_price: 成交价宽表（元/股），把成交股数折算成成交额。
        max_volume_pct: 单日成交额占比上限（如 0.10）；≤0 表示关闭裁剪。

    Returns:
        裁剪后的实际目标持仓宽表，与 ``target_size`` 同 index / columns。
    """
    if max_volume_pct is None or max_volume_pct <= 0:
        return target_size.copy()

    tgt = target_size.values.astype("float64")
    amt = np.nan_to_num(daily_amount.values.astype("float64"), nan=0.0)
    px = np.nan_to_num(exec_price.values.astype("float64"), nan=0.0)
    n, m = tgt.shape
    actual = np.zeros_like(tgt)
    prev = np.zeros(m)
    for t in range(n):
        cap_value = max_volume_pct * amt[t]
        delta = tgt[t] - prev
        order_value = np.abs(delta) * px[t]
        over = (order_value > cap_value) & (order_value > 0)
        scale = np.ones(m)
        # 仅在超额位置写入 cap/order（≤1）；其余保持 1.0 不裁剪。
        np.divide(cap_value, order_value, out=scale, where=over)
        delta = delta * scale
        prev = prev + delta
        actual[t] = prev
    return pd.DataFrame(actual, index=target_size.index, columns=target_size.columns)
