"""图形相似度检索引擎（纯数值，无 LLM、无 IO）。

设计见 docs/plans/2026-06-06-pattern-search-design.md。
核心接口 ``shape_search(query_curve, candidates)``，便于未来替换为学习型 embedding。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba import njit

# 所有曲线统一重采样到定长，保证不同长度窗口可比、DTW 计算量有界。
TARGET_LEN = 128


def normalize_curve(prices, target_len: int = TARGET_LEN) -> np.ndarray:
    """价格序列 → 形状向量：线性重采样到定长 + z-score。

    z-score 去掉绝对价位与涨幅，只保留走势形状。常数序列返回全 0。
    """
    arr = np.asarray(prices, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        raise ValueError("曲线至少需要 2 个有效点")
    xp = np.linspace(0.0, 1.0, arr.size)
    xq = np.linspace(0.0, 1.0, target_len)
    resampled = np.interp(xq, xp, arr)
    mu = float(resampled.mean())
    sd = float(resampled.std())
    if sd < 1e-12:
        return np.zeros(target_len, dtype=np.float64)
    return (resampled - mu) / sd


def correlation_scores(query: np.ndarray, cand_matrix: np.ndarray) -> np.ndarray:
    """query 与候选矩阵每行的 Pearson 相关系数（向量化）。

    入参均假定已 z-score（mean≈0, std≈1），故相关系数 = 点积 / n。
    返回 shape=(N,)，值域约 [-1, 1]，越大越像。
    """
    n = query.shape[0]
    return (cand_matrix @ query) / n


@njit(cache=True)
def _dtw_band(a: np.ndarray, b: np.ndarray, band: int) -> float:
    """Sakoe-Chiba 带约束 DTW，平方欧氏距离。返回累计距离。"""
    n = a.shape[0]
    INF = 1e18
    cost = np.full((n + 1, n + 1), INF)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        jstart = max(1, i - band)
        jend = min(n, i + band)
        for j in range(jstart, jend + 1):
            d = a[i - 1] - b[j - 1]
            d = d * d
            m = cost[i - 1, j]
            if cost[i, j - 1] < m:
                m = cost[i, j - 1]
            if cost[i - 1, j - 1] < m:
                m = cost[i - 1, j - 1]
            cost[i, j] = d + m
    return cost[n, n]


def dtw_similarity(query: np.ndarray, cand: np.ndarray, band_ratio: float = 0.15) -> float:
    """DTW 距离 → [0,1] 相似度分。两序列均应已 z-score。"""
    band = max(1, int(query.shape[0] * band_ratio))
    dist = _dtw_band(query, cand, band)
    return 1.0 / (1.0 + math.sqrt(dist / query.shape[0]))


@dataclass
class Candidate:
    """一个候选窗口。prices 为原始价格（未归一化）。"""
    label: str
    prices: np.ndarray
    scale: int
    start_date: str | None = None
    end_date: str | None = None


@dataclass
class Match:
    label: str
    score: float
    scale: int
    start_date: str | None
    end_date: str | None
    curve: list[float]  # 归一化后下采样的缩略曲线，供前端画 sparkline


def _downsample(curve: np.ndarray, n: int = 48) -> list[float]:
    if curve.shape[0] <= n:
        return [round(float(v), 4) for v in curve]
    idx = np.linspace(0, curve.shape[0] - 1, n).astype(int)
    return [round(float(v), 4) for v in curve[idx]]


def shape_search(
    query_curve: np.ndarray,
    candidates: list[Candidate],
    top_k: int = 20,
    prefilter_k: int = 50,
) -> list[Match]:
    """对候选窗口做形状检索：相关系数粗筛 Top-K → DTW 精排 → 取 top_k。

    query_curve 必须已是 normalize_curve 的输出（定长 z-score）。
    """
    if not candidates:
        return []
    # 精排池至少要能容下 top_k，否则相关系数粗筛会把结果数压在 prefilter_k 以下。
    prefilter_k = max(prefilter_k, top_k)
    norm = np.vstack([normalize_curve(c.prices) for c in candidates])
    corr = correlation_scores(query_curve, norm)
    k = min(prefilter_k, len(candidates))
    # 取相关系数最高的 k 个进入 DTW 精排（argpartition 比全排序快）
    cand_idx = np.argpartition(-corr, k - 1)[:k] if k < len(candidates) else np.arange(len(candidates))
    scored = []
    for i in cand_idx:
        sim = dtw_similarity(query_curve, norm[i])
        scored.append((int(i), sim))
    scored.sort(key=lambda t: t[1], reverse=True)
    out: list[Match] = []
    for i, sim in scored[:top_k]:
        c = candidates[i]
        out.append(
            Match(
                label=c.label,
                score=round(float(sim), 4),
                scale=c.scale,
                start_date=c.start_date,
                end_date=c.end_date,
                curve=_downsample(norm[i]),
            )
        )
    return out
