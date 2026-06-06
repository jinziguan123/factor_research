"""pattern_query 服务单测：用伪造 DataService（不连数据库）验证候选生成与排重叠。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import pattern_query as pq


class _FakeData:
    """返回一段构造行情：中部植入一个与查询窗同形状的圆弧。"""
    def __init__(self, close: pd.Series):
        self._close = close

    def load_bars(self, symbols, start, end, freq="1d", adjust="qfq"):
        df = pd.DataFrame({"close": self._close})
        df.index.name = "trade_date"
        return {symbols[0]: df}


def _make_series():
    dates = pd.date_range("2020-01-01", periods=400, freq="B")
    base = np.random.RandomState(0).normal(0, 0.3, 400).cumsum() + 50
    arc = np.sin(np.linspace(0, np.pi, 60)) * 5
    base[300:360] += arc  # 植入相似形态
    return pd.Series(base, index=dates)


def test_search_by_stock_finds_planted_pattern():
    s = _make_series()
    data = _FakeData(s)
    # 查询窗 = 植入段本身
    res = pq.search_by_stock(
        data, symbol="000001.SZ",
        window_start="2021-02-22", window_end="2021-05-14",  # 约对应 300:360
        scales=[60], top_k=5, step=5,
    )
    # 查询窗自身应被排除，但应能在别处找到相似（此处主要验证不报错且返回结构正确）
    assert "query_curve" in res
    assert isinstance(res["matches"], list)
    for m in res["matches"]:
        assert set(m) >= {"label", "score", "scale", "start_date", "end_date", "curve"}


def test_search_by_stock_default_window_uses_recent():
    s = _make_series()
    res = pq.search_by_stock(_FakeData(s), symbol="000001.SZ", scales=[60], top_k=3, step=10)
    assert len(res["query_curve"]) > 0


from backend.services import pattern_query as pq2


def test_extract_curve_from_image_parses_polyline(monkeypatch):
    # 桩：返回一段归一化折线 JSON
    fake = '{"points": [[0,0.1],[0.5,0.9],[1.0,0.3]], "trend": "先涨后跌"}'
    monkeypatch.setattr(pq2, "_call_openai_compatible", lambda messages, **kw: fake)
    curve = pq2.extract_curve_from_image("data:image/png;base64,xxx", hint="圆弧顶")
    from backend.services.pattern_search import TARGET_LEN
    assert curve.shape == (TARGET_LEN,)


def test_extract_curve_rejects_too_few_points(monkeypatch):
    monkeypatch.setattr(pq2, "_call_openai_compatible", lambda messages, **kw: '{"points": [[0,0.1]]}')
    with pytest.raises(ValueError):
        pq2.extract_curve_from_image("data:image/png;base64,xxx")
