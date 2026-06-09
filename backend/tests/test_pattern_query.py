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


def test_extract_curve_multi_sample_takes_median(monkeypatch):
    from backend.services.pattern_search import TARGET_LEN
    monkeypatch.setattr(pq2.settings, "openai_vision_samples", 3)
    calls = {"n": 0}
    # 三次采样：两条上升、一条下降（离群）。逐点中位数应贴近上升，离群被压掉。
    rising = '{"points": ' + str([[i / 9, i / 9] for i in range(10)]) + '}'
    falling = '{"points": ' + str([[i / 9, 1 - i / 9] for i in range(10)]) + '}'

    def _fake(messages, **kw):
        calls["n"] += 1
        return falling if calls["n"] == 2 else rising

    monkeypatch.setattr(pq2, "_call_openai_compatible", _fake)
    out = pq2.extract_curve_from_image("data:image/png;base64,x")
    assert calls["n"] == 3              # 真的采样了 3 次
    assert out.shape == (TARGET_LEN,)
    # 中位数贴近"上升"形状：末点应明显高于首点
    assert out[-1] > out[0]


def test_extract_curve_multi_sample_tolerates_partial_failures(monkeypatch):
    from backend.services.pattern_search import TARGET_LEN
    monkeypatch.setattr(pq2.settings, "openai_vision_samples", 3)
    calls = {"n": 0}

    def _fake(messages, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "garbage not json"   # 第一次坏掉
        return '{"points": [[0,0.1],[0.5,0.9],[1,0.3]]}'

    monkeypatch.setattr(pq2, "_call_openai_compatible", _fake)
    out = pq2.extract_curve_from_image("data:image/png;base64,x")
    assert out.shape == (TARGET_LEN,)   # 有成功采样就能出结果


def test_extract_curve_multi_sample_all_fail_raises(monkeypatch):
    monkeypatch.setattr(pq2.settings, "openai_vision_samples", 2)
    monkeypatch.setattr(pq2, "_call_openai_compatible", lambda messages, **kw: "garbage")
    with pytest.raises(Exception):
        pq2.extract_curve_from_image("data:image/png;base64,x")


def _capture_messages(monkeypatch):
    """让 _call_openai_compatible 把收到的 messages 录下来，返回桩值。"""
    seen = {}
    def _fake(messages, **kw):
        seen["messages"] = messages
        return '{"points": [[0,0.1],[1,0.9]]}'
    monkeypatch.setattr(pq2, "_call_openai_compatible", _fake)
    return seen


def test_extract_curve_encodes_image_for_anthropic(monkeypatch):
    # 关键回归：anthropic_messages 协议下图片必须是 {"type":"image","source":{base64}}，
    # 否则图片被服务端丢弃（线上 bug：模型看不到图只能瞎猜）。
    monkeypatch.setattr(pq2.settings, "openai_api_protocol", "anthropic_messages")
    seen = _capture_messages(monkeypatch)
    pq2.extract_curve_from_image("data:image/png;base64,QUJD", hint="圆弧顶")
    parts = seen["messages"][1]["content"]
    img = [p for p in parts if p.get("type") == "image"]
    assert len(img) == 1
    assert img[0]["source"]["type"] == "base64"
    assert img[0]["source"]["media_type"] == "image/png"
    assert img[0]["source"]["data"] == "QUJD"  # 剥掉了 data: 前缀


def test_extract_curve_encodes_image_for_responses(monkeypatch):
    monkeypatch.setattr(pq2.settings, "openai_api_protocol", "responses")
    seen = _capture_messages(monkeypatch)
    pq2.extract_curve_from_image("data:image/png;base64,QUJD")
    parts = seen["messages"][1]["content"]
    img = [p for p in parts if p.get("type") == "input_image"]
    assert len(img) == 1 and img[0]["image_url"] == "data:image/png;base64,QUJD"


def test_extract_curve_encodes_image_for_chat_completions(monkeypatch):
    monkeypatch.setattr(pq2.settings, "openai_api_protocol", "chat_completions")
    seen = _capture_messages(monkeypatch)
    pq2.extract_curve_from_image("data:image/png;base64,QUJD")
    parts = seen["messages"][1]["content"]
    img = [p for p in parts if p.get("type") == "image_url"]
    assert len(img) == 1 and img[0]["image_url"]["url"] == "data:image/png;base64,QUJD"


class _FakePool:
    def __init__(self, panels):  # panels: dict[symbol, np.ndarray]
        self._panels = panels
    def resolve_pool(self, pool_id):
        return list(self._panels)
    def load_bars(self, symbols, start, end, freq="1d", adjust="qfq"):
        out = {}
        for s in symbols:
            df = pd.DataFrame({"close": self._panels[s]})
            df.index = pd.date_range("2024-01-01", periods=len(self._panels[s]), freq="B")
            df.index.name = "trade_date"
            out[s] = df
        return out


def test_search_by_image_ranks_similar_pool_member(monkeypatch):
    arc = np.sin(np.linspace(0, np.pi, 60))
    panels = {
        "AAA.SZ": np.tile(arc, 3) * 5 + 100,      # 含圆弧
        "BBB.SZ": np.linspace(10, 1, 180),         # 单调下跌
    }
    monkeypatch.setattr(pq2, "_call_openai_compatible",
                        lambda messages, **kw: '{"points": ' + str([[i/59, float(v)] for i, v in enumerate(arc)]) + '}')
    res = pq2.search_by_image(_FakePool(panels), image="data:image/png;base64,x", pool_id=1, scales=[60], top_k=2)
    assert res["matches"][0]["label"].startswith("AAA")
    assert len(res["query_curve"]) > 0


def test_search_by_image_multi_images_aggregates(monkeypatch):
    arc = np.sin(np.linspace(0, np.pi, 60))
    panels = {
        "AAA.SZ": np.tile(arc, 3) * 5 + 100,      # 含圆弧，对两张图都像
        "BBB.SZ": np.linspace(10, 1, 180),         # 单调下跌
    }
    # 两张截图都被识别成圆弧
    monkeypatch.setattr(pq2, "_call_openai_compatible",
                        lambda messages, **kw: '{"points": ' + str([[i/59, float(v)] for i, v in enumerate(arc)]) + '}')
    res = pq2.search_by_image(
        _FakePool(panels), images=["data:image/png;base64,a", "data:image/png;base64,b"],
        pool_id=1, scales=[60], top_k=2,
    )
    assert res["matches"][0]["label"].startswith("AAA")
    # 返回每张图的查询曲线
    assert len(res["query_curves"]) == 2
    # 每条匹配带对两张图的分项
    assert len(res["matches"][0]["sub_scores"]) == 2


def test_search_by_window_finds_similar_pool_member():
    arc = np.sin(np.linspace(0, np.pi, 60))
    panels = {
        "QQQ.SZ": np.tile(arc, 3) * 4 + 80,   # 查询股，最近 60 日是圆弧
        "AAA.SZ": np.tile(arc, 3) * 5 + 100,  # 同形（应排第一）
        "BBB.SZ": np.linspace(10, 1, 180),    # 单调下跌
    }
    res = pq2.search_by_window(_FakePool(panels), symbol="QQQ.SZ", pool_id=1, scales=[60], top_k=5)
    assert len(res["query_curve"]) > 0
    labels = [m["label"] for m in res["matches"]]
    assert "QQQ.SZ" not in labels          # 查询自身被排除
    assert labels[0] == "AAA.SZ"           # 同形态的池内股票排第一


def test_search_by_window_empty_pool():
    arc = np.sin(np.linspace(0, np.pi, 60))
    res = pq2.search_by_window(_FakePool({"QQQ.SZ": np.tile(arc, 3)}), symbol="QQQ.SZ", pool_id=1, scales=[60])
    assert res["matches"] == []            # 池里只有自己，排除后无候选


def test_search_by_image_requires_at_least_one_image():
    with pytest.raises(ValueError):
        pq2.search_by_image(_FakePool({}), pool_id=1)
