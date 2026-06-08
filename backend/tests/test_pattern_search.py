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


from backend.services.pattern_search import shape_search_multi


def test_shape_search_multi_requires_all_queries_similar():
    arc = np.sin(np.linspace(0, np.pi, 80))
    down = np.linspace(1, 0, 80)
    q_arc = normalize_curve(arc)
    q_down = normalize_curve(down)
    candidates = [
        Candidate(label="arc_only", prices=arc * 5 + 100, scale=80),
        Candidate(label="down_only", prices=down, scale=80),
    ]
    # 两条查询：一条圆弧、一条下跌。min 聚合下，谁都不该拿到高分（无候选同时像两者）。
    out = shape_search_multi([q_arc, q_down], candidates, top_k=2)
    assert isinstance(out[0], Match)
    # 每条结果都带 sub_scores（对每条 query 的分项）
    assert len(out[0].sub_scores) == 2
    # min 聚合分 = 两个分项里的较小者
    assert out[0].score == pytest.approx(min(out[0].sub_scores), abs=1e-6)


def test_shape_search_multi_ranks_match_to_all_queries_first():
    arc = np.sin(np.linspace(0, np.pi, 80))
    q1 = normalize_curve(arc)
    q2 = normalize_curve(arc * 2 + 50)  # 同形状不同价位
    candidates = [
        Candidate(label="line", prices=np.linspace(0, 1, 80), scale=80),
        Candidate(label="planted", prices=arc * 3 + 10, scale=80),  # 对两条 query 都像
    ]
    out = shape_search_multi([q1, q2], candidates, top_k=2)
    assert out[0].label == "planted"
    assert out[0].score > 0.9


def test_shape_search_multi_empty():
    q = normalize_curve(np.linspace(0, 1, 30))
    assert shape_search_multi([q], [], top_k=5) == []
    assert shape_search_multi([], [Candidate(label="x", prices=np.linspace(0, 1, 80), scale=80)], top_k=5) == []


from backend.services.pattern_search import _blend_score


def test_blend_score_suppresses_negative_correlation():
    # DTW 分相同，但相关系数为负（反相形态）的综合分必须更低。
    high = _blend_score(0.8, 0.9)
    low = _blend_score(0.8, -0.9)
    assert high > low
    # 负相关被 clamp 到 0，综合分 = 0.6*0.8
    assert low == pytest.approx(0.6 * 0.8, abs=1e-9)


def test_shape_search_ranks_true_shape_above_inverted():
    # 反相曲线即使 DTW 距离不大，综合评分也应排在同形曲线之后。
    target = np.sin(np.linspace(0, np.pi, 80))
    query = normalize_curve(target)
    candidates = [
        Candidate(label="inverted", prices=-target, scale=80),
        Candidate(label="same", prices=target * 3 + 50, scale=80),
    ]
    out = shape_search(query, candidates, top_k=2)
    assert out[0].label == "same"


def test_shape_search_min_score_filters():
    target = np.sin(np.linspace(0, np.pi, 80))
    query = normalize_curve(target)
    candidates = [
        Candidate(label="same", prices=target * 3 + 50, scale=80),
        Candidate(label="inverted", prices=-target, scale=80),
    ]
    # 阈值很高时只留下高度相似的
    out = shape_search(query, candidates, top_k=2, min_score=0.8)
    assert [m.label for m in out] == ["same"]
