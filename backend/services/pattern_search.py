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
