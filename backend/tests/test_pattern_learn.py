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
    assert f is not None and f.ndim == 1 and f.size == 14 + pl._CURVE_FEATS + pl._CONTEXT_FEATS
    assert pl.extract_window_features(np.array([1.0, 2.0])) is None  # 太短


def test_context_features_nonzero_with_pre_close():
    pre = np.linspace(10, 15, 40)
    f = pl.extract_window_features(_breakdown(60), pre_close=pre)
    ctx = f[-(pl._CONTEXT_FEATS):]
    assert not np.allclose(ctx, 0), "有 pre_close 时上下文特征不该全零"
    f_no_ctx = pl.extract_window_features(_breakdown(60))
    ctx_no = f_no_ctx[-(pl._CONTEXT_FEATS):]
    assert np.allclose(ctx_no, 0), "无 pre_close 时上下文特征应全零"


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
    # 回归：分数不能全挤在一起（之前 GBM 过拟合导致"全 99.9%"）
    vals = list(scores.values())
    assert max(vals) - min(vals) > 0.05


def test_history_mode_runs_and_ranks():
    panels = {
        "POS1.SZ": _breakdown(), "NEG1.SZ": _rounded(),
        "BRK1.SZ": _breakdown() + 3, "RND1.SZ": _rounded() * 1.1,
    }
    labels = [{"symbol": "POS1.SZ", "label": 1}, {"symbol": "NEG1.SZ", "label": 0}]
    res = pl.search_by_learned(
        _FakePool(panels), labels=labels, pool_id=1, top_k=10,
        mode="history", step=5, history_days=1000,
    )
    assert res["matches"], "history 模式应返回结果"
    sc = {m["label"]: m["score"] for m in res["matches"]}
    assert sc["BRK1.SZ"] > sc["RND1.SZ"]


def test_context_discriminates_uptrend_vs_downtrend():
    """同一形状，上升趋势标正例 / 下降趋势标反例——模型能区分趋势环境。"""
    shape = _breakdown(60)
    up_pre = np.linspace(8, 15, 60)
    dn_pre = np.linspace(15, 8, 60)
    panels = {
        "POS.SZ": np.concatenate([up_pre, shape]),
        "NEG.SZ": np.concatenate([dn_pre, shape]),
        "UP_C.SZ": np.concatenate([np.linspace(7, 14, 60), _breakdown(60)]),
        "DN_C.SZ": np.concatenate([np.linspace(14, 7, 60), _breakdown(60)]),
    }
    dates = pd.date_range("2025-01-01", periods=120, freq="B")
    s, e = dates[60].strftime("%Y-%m-%d"), dates[119].strftime("%Y-%m-%d")
    labels = [
        {"symbol": "POS.SZ", "start_date": s, "end_date": e, "label": 1},
        {"symbol": "NEG.SZ", "start_date": s, "end_date": e, "label": 0},
    ]
    res = pl.search_by_learned(_FakePool(panels), labels=labels, pool_id=1, top_k=10)
    scores = {m["label"]: m["score"] for m in res["matches"]}
    assert scores["UP_C.SZ"] > scores["DN_C.SZ"], \
        "上升趋势上下文应得分更高"


def test_window_length_follows_labeled_range():
    # 正例用一个 ~90 日的日期区间，结果窗口长度应≈90，不再固定 30。
    brk = _breakdown(150)
    rnd = _rounded(150)
    panels = {"P.SZ": brk, "N.SZ": rnd, "C.SZ": _breakdown(150) + 2}
    dates = pd.date_range("2025-01-01", periods=150, freq="B")
    start, end = dates[40].strftime("%Y-%m-%d"), dates[129].strftime("%Y-%m-%d")  # 90 个交易日
    # 用 DB 真实列名 start_date/end_date（回归：曾因读成 start/end 导致区间被忽略→全60日）
    labels = [
        {"symbol": "P.SZ", "start_date": start, "end_date": end, "label": 1},
        {"symbol": "N.SZ", "start_date": start, "end_date": end, "label": 0},
    ]
    res = pl.search_by_learned(_FakePool(panels), labels=labels, pool_id=1, top_k=5)
    assert res["matches"], "应有结果"
    assert all(m["scale"] == 90 for m in res["matches"])  # ≈标注区间长度，证明区间被正确应用
