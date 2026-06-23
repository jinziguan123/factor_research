"""组合权重优化器：替换简单的 top 等权分配。

**纯函数，无 DB 依赖**，便于单测。提供 A 股长仓选股常用的几种权重构造：
- ``equal_weights``：等权（基线）；
- ``inverse_vol_weights``：逆波动率（风险平价的对角近似，稳健实用）；
- ``risk_parity_weights``：迭代风险平价，使各资产风险贡献相等；
- ``mean_variance_weights``：均值-方差（Markowitz），long-only 用 SLSQP；
- ``ic_weighted_combine``：多因子按 IC 加权合成（横截面 z-score 后加权）。

辅助：``estimate_cov`` 从收益窗口估样本协方差；``apply_turnover_budget`` 用换手
预算把目标权重向上期收缩。

约定：权重向量非负且和为 1（A 股长仓，不裸卖空）。退化输入（空、奇异、全零）
一律回退等权，绝不返回 NaN / inf。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def equal_weights(n: int) -> np.ndarray:
    """等权基线。"""
    if n <= 0:
        return np.array([], dtype="float64")
    return np.full(n, 1.0 / n, dtype="float64")


def _safe_normalize(w: np.ndarray) -> np.ndarray:
    """裁负 + 归一化到和=1；全零回退等权。"""
    w = np.clip(np.asarray(w, dtype="float64"), 0.0, None)
    s = w.sum()
    if not np.isfinite(s) or s <= 0:
        return equal_weights(len(w))
    return w / s


def estimate_cov(returns: pd.DataFrame) -> np.ndarray:
    """从日收益窗口估样本协方差矩阵（asset × asset）。"""
    if returns is None or returns.empty or returns.shape[1] == 0:
        return np.zeros((0, 0))
    return returns.cov().to_numpy()


def inverse_vol_weights(cov: np.ndarray) -> np.ndarray:
    """逆波动率权重：``w_i ∝ 1/σ_i``（风险平价的零相关特例）。"""
    cov = np.asarray(cov, dtype="float64")
    n = cov.shape[0]
    if n == 0:
        return np.array([], dtype="float64")
    vol = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    inv = np.where(vol > 0, 1.0 / vol, 0.0)
    return _safe_normalize(inv)


def risk_parity_weights(
    cov: np.ndarray, iters: int = 500, tol: float = 1e-9
) -> np.ndarray:
    """迭代风险平价：调整权重使各资产风险贡献 ``w_i·(Σw)_i`` 相等。

    固定点迭代 + 阻尼（开方），对角输入收敛到逆波动率，一般正定输入收敛到风险平价。
    奇异 / 非正定时回退逆波动率。
    """
    cov = np.asarray(cov, dtype="float64")
    n = cov.shape[0]
    if n == 0:
        return np.array([], dtype="float64")
    if n == 1:
        return np.array([1.0])
    w = equal_weights(n)
    for _ in range(iters):
        mrc = cov @ w  # 边际风险贡献
        rc = w * mrc   # 风险贡献
        port_var = float(w @ cov @ w)
        if port_var <= 0 or not np.all(np.isfinite(rc)):
            return inverse_vol_weights(cov)
        target = port_var / n
        adj = np.where(rc > 1e-15, target / rc, 1.0)
        w_new = _safe_normalize(w * np.sqrt(adj))
        if np.max(np.abs(w_new - w)) < tol:
            return w_new
        w = w_new
    return w


def mean_variance_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    risk_aversion: float = 1.0,
    long_only: bool = True,
) -> np.ndarray:
    """均值-方差最优权重：``max  w·μ − (λ/2)·w'Σw``，``s.t. Σw=1``。

    Args:
        mu: 预期收益向量（可用因子值代理）。
        cov: 协方差矩阵。
        risk_aversion: 风险厌恶 λ，越大越偏低风险。
        long_only: True 时加 ``0 ≤ w ≤ 1``（A 股不裸卖空），用 SLSQP 求解；
            False 时用解析解 ``w ∝ Σ⁻¹μ``。

    Returns:
        权重向量（long_only 下非负且和=1）。求解失败回退等权。
    """
    mu = np.asarray(mu, dtype="float64")
    cov = np.asarray(cov, dtype="float64")
    n = len(mu)
    if n == 0:
        return np.array([], dtype="float64")
    if n == 1:
        return np.array([1.0])

    if not long_only:
        inv = np.linalg.pinv(cov)
        raw = inv @ (mu / max(risk_aversion, 1e-12))
        s = raw.sum()
        if not np.isfinite(s) or abs(s) < 1e-12:
            return equal_weights(n)
        return raw / s

    from scipy.optimize import minimize

    def neg_utility(w: np.ndarray) -> float:
        return -float(mu @ w) + 0.5 * risk_aversion * float(w @ cov @ w)

    cons = ({"type": "eq", "fun": lambda w: w.sum() - 1.0},)
    bounds = [(0.0, 1.0)] * n
    res = minimize(
        neg_utility, equal_weights(n), method="SLSQP",
        bounds=bounds, constraints=cons,
        options={"maxiter": 500, "ftol": 1e-10},
    )
    if not res.success or not np.all(np.isfinite(res.x)):
        return equal_weights(n)
    return _safe_normalize(res.x)


def ic_weighted_combine(
    factors: dict[str, pd.DataFrame], ic_weights: dict[str, float]
) -> pd.DataFrame:
    """多因子按 IC 加权合成：各因子横截面 z-score 后按 IC 权重线性叠加。

    IC 权重可正可负（负 IC 因子自动反向）；权重为 0 的因子被忽略。按 ``Σ|ic|``
    归一化，使合成因子量纲稳定。

    Args:
        factors: ``{factor_name: 因子宽表(date × symbol)}``。
        ic_weights: ``{factor_name: ic 权重}``。

    Returns:
        合成因子宽表（date × symbol）。

    Raises:
        ValueError: 所有权重为 0（无法归一化）。
    """
    total = sum(abs(float(v)) for v in ic_weights.values())
    if total <= 0:
        raise ValueError("ic_weights 全为 0，无法合成")
    combined: pd.DataFrame | None = None
    for name, F in factors.items():
        w = float(ic_weights.get(name, 0.0))
        if w == 0.0:
            continue
        mean = F.mean(axis=1)
        std = F.std(axis=1).replace(0.0, np.nan)
        z = F.sub(mean, axis=0).div(std, axis=0)
        contrib = z * w
        combined = contrib if combined is None else combined.add(contrib, fill_value=0.0)
    if combined is None:
        raise ValueError("没有任何非零权重因子参与合成")
    return combined / total


def apply_turnover_budget(
    target: np.ndarray, prev: np.ndarray, max_turnover: float
) -> np.ndarray:
    """换手预算约束：若 ``Σ|target−prev| > max_turnover``，向上期权重线性收缩。

    收缩系数 ``α = max_turnover / turnover``，结果 ``prev + α·(target−prev)``，
    恰好把换手压到预算上限。``max_turnover<=0`` 视为不约束。
    """
    target = np.asarray(target, dtype="float64")
    prev = np.asarray(prev, dtype="float64")
    if max_turnover is None or max_turnover <= 0:
        return target
    turnover = float(np.abs(target - prev).sum())
    if turnover <= max_turnover or turnover <= 0:
        return target
    alpha = max_turnover / turnover
    return prev + alpha * (target - prev)


def reweight_intragroup(
    W: pd.DataFrame,
    returns: pd.DataFrame,
    method: str = "equal",
    lookback: int = 60,
) -> pd.DataFrame:
    """把等权宽表升级为组内风险加权，保持各组总权重不变。

    对每个持仓日，分别处理正权重组（多头）与负权重组（空头）：用过去 ``lookback``
    日收益估协方差，按 ``inverse_vol`` / ``risk_parity`` 重分配，再缩放回该组原总权重
    （多头和=1 或 0.5，空头和=-0.5），不改变多空敞口。

    Args:
        W: 权重宽表（date × symbol），``_build_weights`` 输出。
        returns: 日收益宽表（date × symbol），与 W 同 columns。
        method: ``"equal"``（原样返回）| ``"inverse_vol"`` | ``"risk_parity"``。
        lookback: 协方差估计回看天数。

    Returns:
        重分配后的权重宽表，与 W 同 shape。
    """
    if method == "equal":
        return W
    if method not in ("inverse_vol", "risk_parity"):
        raise ValueError(f"weighting 方法不支持: {method!r}")
    out = W.copy()
    for dt in W.index:
        row = W.loc[dt]
        for sign in (1.0, -1.0):
            grp = row[(row * sign) > 0].index
            if len(grp) < 2:
                continue
            total = float(row[grp].sum())
            window = returns.loc[:dt, grp].tail(lookback)
            if len(window) < 2:
                continue
            cov = estimate_cov(window)
            if cov.shape[0] != len(grp):
                continue
            w = (
                inverse_vol_weights(cov)
                if method == "inverse_vol"
                else risk_parity_weights(cov)
            )
            out.loc[dt, grp] = w * total
    return out
