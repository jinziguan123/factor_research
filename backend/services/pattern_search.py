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
