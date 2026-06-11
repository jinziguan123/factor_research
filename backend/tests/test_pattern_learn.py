"""pattern_learn 单测：特征提取 + 正反例训练 + 池内打分排序（不连库）。

构造两类明显可分的形态：
- 正例「涨一波→末端急跌跌穿」；反例「圆弧顶/温和回落」。
标 2 正 2 反训练，验证池里留出的"急跌型"股票得分明显高于"圆弧顶型"。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import pattern_learn as pl


def _breakdown(n=80):
    up = np.linspace(10, 20, int(n * 0.8))
    down = np.linspace(20, 14, n - up.size)
    return np.concatenate([up, down])


def _rounded(n=80):
    return 10 + 5 * np.sin(np.linspace(0, np.pi, n))


class _FakePool:
    def __init__(self, panels):
        self._panels = panels

    def resolve_pool(self, pool_id):
        return list(self._panels)

    def load_bars(self, symbols, start, end, freq="1d", adjust="qfq"):
        out = {}
        for s in symbols:
            df = pd.DataFrame({"close": self._panels[s]})
            df.index = pd.date_range("2025-01-01", periods=len(self._panels[s]), freq="B")
            df.index.name = "trade_date"
            out[s] = df
        return out


def test_extract_features_shape_and_short_guard():
    f = pl.extract_window_features(_breakdown(60))
    assert f is not None and f.ndim == 1 and f.size == 14 + pl._CURVE_FEATS
    assert pl.extract_window_features(np.array([1.0, 2.0])) is None  # 太短


def test_requires_both_classes():
    panels = {"P1.SZ": _breakdown(), "X.SZ": _rounded()}
    with pytest.raises(ValueError):
        # 只有正例，没有反例
        pl.search_by_learned(_FakePool(panels), labels=[{"symbol": "P1.SZ", "label": 1}], pool_id=1)


def test_learned_ranks_breakdown_above_rounded():
    panels = {
        # 标注用
        "POS1.SZ": _breakdown(), "POS2.SZ": _breakdown() * 1.1,
        "NEG1.SZ": _rounded(), "NEG2.SZ": _rounded() * 0.9 + 2,
        # 池里留出的（被打分）
        "BRK1.SZ": _breakdown() * 0.8 + 5, "BRK2.SZ": _breakdown() + 3,
        "RND1.SZ": _rounded() * 1.2, "RND2.SZ": _rounded() + 1,
    }
    labels = [
        {"symbol": "POS1.SZ", "label": 1}, {"symbol": "POS2.SZ", "label": 1},
        {"symbol": "NEG1.SZ", "label": 0}, {"symbol": "NEG2.SZ", "label": 0},
    ]
    res = pl.search_by_learned(_FakePool(panels), labels=labels, pool_id=1, top_k=10)
    assert len(res["query_curves"]) == 2          # 两个正例曲线
    scores = {m["label"]: m["score"] for m in res["matches"]}
    # 标注股票被排除
    assert "POS1.SZ" not in scores and "NEG1.SZ" not in scores
    # 留出的急跌型得分应高于圆弧顶型
    assert min(scores["BRK1.SZ"], scores["BRK2.SZ"]) > max(scores["RND1.SZ"], scores["RND2.SZ"])
    # 打分窗口长度跟随正例长度（正例无区间→最近60日），结果尺度应为 60，而非固定 30
    assert all(m["scale"] == 60 for m in res["matches"])


def test_window_length_follows_labeled_range():
    # 正例用一个 ~90 日的日期区间，结果窗口长度应≈90，不再固定 30。
    brk = _breakdown(150)
    rnd = _rounded(150)
    panels = {"P.SZ": brk, "N.SZ": rnd, "C.SZ": _breakdown(150) + 2}
    dates = pd.date_range("2025-01-01", periods=150, freq="B")
    start, end = dates[40].strftime("%Y-%m-%d"), dates[129].strftime("%Y-%m-%d")  # 90 个交易日
    labels = [
        {"symbol": "P.SZ", "start": start, "end": end, "label": 1},
        {"symbol": "N.SZ", "start": start, "end": end, "label": 0},
    ]
    res = pl.search_by_learned(_FakePool(panels), labels=labels, pool_id=1, top_k=5)
    assert res["matches"], "应有结果"
    assert all(m["scale"] == 90 for m in res["matches"])  # ≈标注区间长度
