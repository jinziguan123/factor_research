"""pattern_search 引擎单测：归一化 / 相关系数 / DTW / shape_search 排序。

纯数值，不依赖数据库 / 网络。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.pattern_search import normalize_curve, TARGET_LEN


def test_normalize_curve_resamples_to_target_len():
    out = normalize_curve(np.array([1.0, 2.0, 3.0]))
    assert out.shape == (TARGET_LEN,)


def test_normalize_curve_is_scale_and_level_invariant():
    # 同形状、不同价位与振幅，z-score 后应几乎相等
    base = np.linspace(0, 1, 50) ** 2
    a = normalize_curve(base * 10 + 100)
    b = normalize_curve(base * 3 + 5)
    assert np.allclose(a, b, atol=1e-6)


def test_normalize_curve_constant_series_returns_zeros():
    out = normalize_curve(np.full(20, 7.0))
    assert np.allclose(out, 0.0)


def test_normalize_curve_rejects_too_short():
    with pytest.raises(ValueError):
        normalize_curve(np.array([1.0]))


from backend.services.pattern_search import correlation_scores, dtw_similarity


def _norm(x):
    return normalize_curve(np.asarray(x, dtype=float))


def test_correlation_identical_is_one():
    q = _norm(np.linspace(0, 1, 60) ** 2)
    score = correlation_scores(q, q.reshape(1, -1))[0]
    assert score == pytest.approx(1.0, abs=1e-6)


def test_correlation_inverted_is_negative():
    base = np.linspace(0, 1, 60) ** 2
    q = _norm(base)
    inv = _norm(-base)
    score = correlation_scores(q, inv.reshape(1, -1))[0]
    assert score < -0.9


def test_dtw_phase_shift_still_high():
    # 相位平移：相关系数会掉，DTW 应仍判为高度相似
    n = 128
    a = np.zeros(n); a[40:60] = np.hanning(20)
    b = np.zeros(n); b[60:80] = np.hanning(20)
    qa, qb = _norm(a), _norm(b)
    corr = correlation_scores(qa, qb.reshape(1, -1))[0]
    sim = dtw_similarity(qa, qb)
    assert sim > corr  # DTW 对相位错位更鲁棒
    assert sim > 0.5


from backend.services.pattern_search import Candidate, Match, shape_search


def test_shape_search_ranks_planted_match_first():
    target = np.sin(np.linspace(0, np.pi, 80))  # 圆弧顶形状
    query = normalize_curve(target)
    rng = np.linspace(0, 1, 80)
    candidates = [
        Candidate(label="noise1", prices=np.cumsum(np.ones(80)), scale=80),
        Candidate(label="line", prices=rng, scale=80),
        Candidate(label="planted", prices=target * 5 + 100, scale=80),  # 同形状不同价位
        Candidate(label="vshape", prices=-target, scale=80),
    ]
    out = shape_search(query, candidates, top_k=4)
    assert isinstance(out[0], Match)
    assert out[0].label == "planted"
    assert out[0].score > 0.9


def test_shape_search_empty_candidates():
    q = normalize_curve(np.linspace(0, 1, 30))
    assert shape_search(q, [], top_k=5) == []
