"""组合级风控：集中度约束、目标波动率缩放、回撤熔断。

**纯函数，无 DB 依赖**，便于单测。作用在权重 / 净值层，给"走向实盘"的组合加上
风险护栏。约定权重为非负长仓（A 股不裸卖空）。

- ``concentration_cap``：个股 + 行业集中度上限（个股用水填充保持总仓位，行业超配
  按比例缩减）；
- ``target_vol_scaling``：用回看窗口估组合波动率，缩放总仓位到目标年化波动率；
- ``drawdown_throttle``：净值回撤超阈值时输出降仓乘子（接入需净值反馈，见 roadmap）。

``apply_portfolio_risk`` 把个股集中度 + 目标波动率逐调仓日应用到权重宽表，供回测接入。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def concentration_cap(
    w: pd.Series,
    max_weight: float = 1.0,
    industry: pd.Series | None = None,
    max_industry_weight: float = 1.0,
    iters: int = 200,
) -> pd.Series:
    """对权重施加个股 / 行业集中度上限。

    个股：超过 ``max_weight`` 的部分削平，超出量按比例水填充给未触顶个股（保持总
    仓位不变；当 ``max_weight × 个数 < 总仓位`` 物理不可行时，迭代到上限尽力均摊）。
    行业：超配行业按比例缩减到 ``max_industry_weight``（只缩不补，总仓位相应下降——
    实盘对超配行业的合理处置是减仓而非强行换仓）。

    Args:
        w: 权重 Series（index=symbol，非负）。
        max_weight: 单只个股权重上限（1.0 = 不约束）。
        industry: 可选，``index=symbol, value=行业代码`` 的映射。
        max_industry_weight: 单行业权重上限（1.0 = 不约束）。
        iters: 水填充迭代上限。

    Returns:
        约束后的权重 Series（与入参同 index）。
    """
    w = w.astype("float64").copy()
    total = float(w.sum())
    if total <= 0:
        return w

    # 个股集中度：水填充
    if max_weight < 1.0:
        for _ in range(iters):
            over = w > max_weight + 1e-12
            if not over.any():
                break
            excess = float((w[over] - max_weight).sum())
            w[over] = max_weight
            free = (~over) & (w > 0)
            if not free.any() or excess <= 0:
                break
            w[free] = w[free] + excess * (w[free] / float(w[free].sum()))

    # 行业集中度：超配行业按比例缩减（只缩不补）
    if industry is not None and max_industry_weight < 1.0:
        ind = industry.reindex(w.index)
        for _ in range(iters):
            ind_sum = w.groupby(ind).sum()
            over_ind = ind_sum[ind_sum > max_industry_weight + 1e-12]
            if over_ind.empty:
                break
            for code, s in over_ind.items():
                members = w.index[ind == code]
                w[members] = w[members] * (max_industry_weight / float(s))
    return w


def target_vol_scaling(
    w: pd.Series,
    returns: pd.DataFrame,
    target_vol: float,
    lookback: int = 60,
    max_leverage: float = 1.0,
    ann: int = 252,
) -> pd.Series:
    """按目标年化波动率缩放总仓位。

    用最近 ``lookback`` 日收益估年化协方差，算组合波动率，缩放系数 = target_vol /
    组合波动率，并受 ``max_leverage`` 约束（缩放后总仓位 ≤ max_leverage）。组合波动率
    过低（≈0）或样本不足时原样返回。

    Args:
        w: 权重 Series（index=symbol）。
        returns: 日收益宽表（columns ⊇ w.index）。
        target_vol: 目标年化波动率（如 0.15）。
        lookback: 回看天数。
        max_leverage: 总仓位上限。
        ann: 年化因子（日频 252）。
    """
    if target_vol <= 0:
        return w
    cols = list(w.index)
    window = returns.reindex(columns=cols).tail(lookback).dropna(how="all")
    if len(window) < 2:
        return w
    cov = window.cov().to_numpy() * ann
    wv = w.to_numpy(dtype="float64")
    port_var = float(wv @ cov @ wv)
    if not np.isfinite(port_var) or port_var <= 1e-12:
        return w
    port_vol = float(np.sqrt(port_var))
    scale = target_vol / port_vol
    cur_lev = float(w.sum())
    if cur_lev > 0:
        scale = min(scale, max_leverage / cur_lev)
    return w * scale


def drawdown_throttle(
    equity: np.ndarray | pd.Series,
    dd_threshold: float,
    throttle_factor: float = 0.5,
) -> np.ndarray:
    """回撤熔断：净值自峰值回撤超过阈值的时点，输出降仓乘子。

    Args:
        equity: 净值序列。
        dd_threshold: 回撤阈值（正数，如 0.2 表示回撤 20%）。
        throttle_factor: 触发后的仓位乘子（如 0.5 = 降到半仓）。

    Returns:
        与 equity 等长的乘子数组：回撤 ≤ −dd_threshold 处为 throttle_factor，否则 1.0。

    Note:
        精确接入回测需要"运行中净值"反馈（order_func 迭代回测路径），见设计文档
        roadmap；此处提供纯函数，可用于事后分析或迭代回测引擎。
    """
    eq = np.asarray(equity, dtype="float64")
    if eq.size == 0:
        return np.array([], dtype="float64")
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, eq / peak - 1.0, 0.0)
    return np.where(dd <= -abs(dd_threshold), throttle_factor, 1.0).astype("float64")


def apply_portfolio_risk(
    W: pd.DataFrame,
    close: pd.DataFrame,
    *,
    max_position_weight: float = 0.0,
    target_vol: float = 0.0,
    lookback: int = 60,
    industry: pd.Series | None = None,
    max_industry_weight: float = 0.0,
) -> pd.DataFrame:
    """把个股集中度 + 目标波动率逐调仓日应用到权重宽表（仅处理正权重多头组）。

    ``max_position_weight<=0`` 关闭个股集中度；``target_vol<=0`` 关闭波动率缩放；
    ``max_industry_weight<=0`` 关闭行业集中度。全关时原样返回。
    """
    if max_position_weight <= 0 and target_vol <= 0 and max_industry_weight <= 0:
        return W
    rets = close.pct_change(fill_method=None)
    out = W.copy()
    mw = max_position_weight if max_position_weight > 0 else 1.0
    miw = max_industry_weight if max_industry_weight > 0 else 1.0
    for dt in W.index:
        row = out.loc[dt]
        pos = row[row > 0]
        if len(pos) < 1:
            continue
        w = pos.copy()
        if max_position_weight > 0 or max_industry_weight > 0:
            w = concentration_cap(
                w, max_weight=mw, industry=industry, max_industry_weight=miw
            )
        if target_vol > 0:
            w = target_vol_scaling(w, rets.loc[:dt], target_vol, lookback=lookback)
        out.loc[dt, w.index] = w
    return out
