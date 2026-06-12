"""图形相似度检索引擎（纯数值，无 LLM、无 IO）。

设计见 docs/plans/2026-06-06-pattern-search-design.md。
核心接口 ``shape_search(query_curve, candidates)``，便于未来替换为学习型 embedding。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba import njit, prange

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


def normalize_curves_batch(price_list: list[np.ndarray], target_len: int = TARGET_LEN) -> np.ndarray:
    """批量归一化：返回 (N, target_len) 矩阵，比逐条调用快。"""
    out = np.empty((len(price_list), target_len), dtype=np.float64)
    xq = np.linspace(0.0, 1.0, target_len)
    for i, prices in enumerate(price_list):
        arr = np.asarray(prices, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size < 2:
            out[i] = 0.0
            continue
        xp = np.linspace(0.0, 1.0, arr.size)
        resampled = np.interp(xq, xp, arr)
        sd = resampled.std()
        if sd < 1e-12:
            out[i] = 0.0
        else:
            out[i] = (resampled - resampled.mean()) / sd
    return out


def correlation_scores(query: np.ndarray, cand_matrix: np.ndarray) -> np.ndarray:
    """query 与候选矩阵每行的 Pearson 相关系数（向量化）。

    入参均假定已 z-score（mean≈0, std≈1），故相关系数 = 点积 / n。
    返回 shape=(N,)，值域约 [-1, 1]，越大越像。
    """
    n = query.shape[0]
    return (cand_matrix @ query) / n


@njit(cache=True)
def _dtw_band(a: np.ndarray, b: np.ndarray, band: int) -> float:
    """Sakoe-Chiba 带约束 DTW，平方欧氏距离。2 行滚动数组，O(n·band) 时间 O(n) 空间。"""
    n = a.shape[0]
    INF = 1e18
    prev = np.full(n + 1, INF)
    curr = np.full(n + 1, INF)
    prev[0] = 0.0
    for i in range(1, n + 1):
        curr[:] = INF
        jstart = max(1, i - band)
        jend = min(n, i + band)
        for j in range(jstart, jend + 1):
            d = a[i - 1] - b[j - 1]
            d = d * d
            m = prev[j]
            if curr[j - 1] < m:
                m = curr[j - 1]
            if prev[j - 1] < m:
                m = prev[j - 1]
            curr[j] = d + m
        prev, curr = curr, prev
    return prev[n]


@njit(parallel=True, cache=True)
def _dtw_batch(query: np.ndarray, cand_matrix: np.ndarray, band: int) -> np.ndarray:
    """对 cand_matrix 每行并行计算与 query 的 DTW 距离。"""
    k = cand_matrix.shape[0]
    dists = np.empty(k, dtype=np.float64)
    for idx in prange(k):
        dists[idx] = _dtw_band(query, cand_matrix[idx], band)
    return dists


def dtw_similarity(query: np.ndarray, cand: np.ndarray, band_ratio: float = 0.15) -> float:
    """DTW 距离 → [0,1] 相似度分。两序列均应已 z-score。"""
    band = max(1, int(query.shape[0] * band_ratio))
    dist = _dtw_band(query, cand, band)
    return 1.0 / (1.0 + math.sqrt(dist / query.shape[0]))


def dtw_similarities_batch(
    query: np.ndarray, cand_matrix: np.ndarray, band_ratio: float = 0.15,
) -> np.ndarray:
    """批量 DTW → 相似度数组，内部 numba 多线程并行。"""
    band = max(1, int(query.shape[0] * band_ratio))
    dists = _dtw_batch(query, cand_matrix, band)
    n = query.shape[0]
    return 1.0 / (1.0 + np.sqrt(dists / n))


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
    # 多查询（多截图）检索时，对每条查询曲线的分项相似度；单查询时为空。
    sub_scores: list[float] = field(default_factory=list)


def _downsample(curve: np.ndarray, n: int = 48) -> list[float]:
    if curve.shape[0] <= n:
        return [round(float(v), 4) for v in curve]
    idx = np.linspace(0, curve.shape[0] - 1, n).astype(int)
    return [round(float(v), 4) for v in curve[idx]]


# 综合评分里 DTW 的权重；剩余给（正）相关系数。
# 动机：DTW 能靠弯折时间轴把走势差异很大的曲线也对齐成低距离 → 假阳性高分。
# 相关系数不弯折时间轴，能识别这类"被强行对齐但整体不像"的候选，把它分数压低。
_DTW_WEIGHT = 0.6


def _blend_score(dtw_sim: float, corr: float) -> float:
    """融合 DTW 相似度与相关系数 → 最终分。

    corr 取 ``max(0, corr)``：负相关（反相形态）不该因 DTW 弯折拿高分。
    """
    return _DTW_WEIGHT * dtw_sim + (1.0 - _DTW_WEIGHT) * max(0.0, corr)


def shape_search(
    query_curve: np.ndarray,
    candidates: list[Candidate],
    top_k: int = 20,
    prefilter_k: int = 50,
    min_score: float = 0.0,
) -> list[Match]:
    """对候选窗口做形状检索：相关系数粗筛 Top-K → DTW+相关系数综合精排 → 取 top_k。

    query_curve 必须已是 normalize_curve 的输出（定长 z-score）。
    ``min_score``>0 时丢弃综合分低于阈值的结果（过滤"勉强像"的候选）。
    """
    if not candidates:
        return []
    prefilter_k = max(prefilter_k, top_k)
    norm = normalize_curves_batch([c.prices for c in candidates])
    corr = correlation_scores(query_curve, norm)
    k = min(prefilter_k, len(candidates))
    cand_idx = np.argpartition(-corr, k - 1)[:k] if k < len(candidates) else np.arange(len(candidates))
    sub_norm = norm[cand_idx]
    sims = dtw_similarities_batch(query_curve, sub_norm)
    scored = []
    for j, i in enumerate(cand_idx):
        score = _blend_score(float(sims[j]), float(corr[i]))
        scored.append((int(i), score))
    scored.sort(key=lambda t: t[1], reverse=True)
    out: list[Match] = []
    for i, score in scored[:top_k]:
        if score < min_score:
            continue
        c = candidates[i]
        out.append(
            Match(
                label=c.label,
                score=round(float(score), 4),
                scale=c.scale,
                start_date=c.start_date,
                end_date=c.end_date,
                curve=_downsample(norm[i]),
            )
        )
    return out


def shape_search_multi(
    query_curves: list[np.ndarray],
    candidates: list[Candidate],
    top_k: int = 20,
    prefilter_k: int = 50,
    agg: str = "min",
    min_score: float = 0.0,
) -> list[Match]:
    """多查询（多截图）形状检索：要求候选「对每条查询曲线都像」。

    每个候选先对各条 query 算 DTW 相似度（分项），再按 ``agg`` 聚合成总分：
    - ``"min"``（默认）：取最小分项 = 交集语义，任一条不像总分就低；
    - ``"mean"``：取平均分项 = 加权语义，更宽松。

    粗筛用「各 query 相关系数的最大值」作并集保留，避免某条 query 把对它弱、
    但对其它 query 强的候选过早淘汰。返回的 Match 带 ``sub_scores`` 分项。
    """
    if not candidates or not query_curves:
        return []
    prefilter_k = max(prefilter_k, top_k)
    norm = normalize_curves_batch([c.prices for c in candidates])
    corr_list = [correlation_scores(q, norm) for q in query_curves]
    corr_max = np.full(len(candidates), -np.inf)
    for corr in corr_list:
        corr_max = np.maximum(corr_max, corr)
    k = min(prefilter_k, len(candidates))
    cand_idx = (
        np.argpartition(-corr_max, k - 1)[:k] if k < len(candidates) else np.arange(len(candidates))
    )
    sub_norm = norm[cand_idx]
    # 每条 query 批量计算 DTW 相似度
    sims_per_query = [dtw_similarities_batch(q, sub_norm) for q in query_curves]
    scored: list[tuple[int, float, list[float]]] = []
    for j, i in enumerate(cand_idx):
        subs = [
            _blend_score(float(sims_per_query[qi][j]), float(corr_list[qi][i]))
            for qi in range(len(query_curves))
        ]
        total = min(subs) if agg == "min" else float(np.mean(subs))
        scored.append((int(i), float(total), subs))
    scored.sort(key=lambda t: t[1], reverse=True)
    out: list[Match] = []
    for i, total, subs in scored[:top_k]:
        if total < min_score:
            continue
        c = candidates[i]
        out.append(
            Match(
                label=c.label,
                score=round(float(total), 4),
                scale=c.scale,
                start_date=c.start_date,
                end_date=c.end_date,
                curve=_downsample(norm[i]),
                sub_scores=[round(float(s), 4) for s in subs],
            )
        )
    return out
