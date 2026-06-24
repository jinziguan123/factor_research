"""可观测性指标单测：Counter / Gauge / Histogram 文本格式 + observe_task。

    uv run pytest backend/tests/test_observability.py -v
"""
from __future__ import annotations

from backend.observability import metrics as om


def test_counter_render_with_labels():
    c = om.Counter("fr_x_total", "测试计数", ["kind"])
    c.inc(kind="eval")
    c.inc(2.0, kind="eval")
    c.inc(kind="backtest")
    lines = c.render()
    assert "# TYPE fr_x_total counter" in lines
    assert 'fr_x_total{kind="eval"} 3' in lines
    assert 'fr_x_total{kind="backtest"} 1' in lines


def test_gauge_set_overwrites():
    g = om.Gauge("fr_g", "健康", ["check"])
    g.set(1, check="db")
    g.set(0, check="db")  # 覆盖
    lines = g.render()
    assert 'fr_g{check="db"} 0' in lines


def test_histogram_cumulative_buckets_and_count():
    h = om.Histogram("fr_h", "时延", [], buckets=[1, 5, 10])
    for v in (0.5, 2, 7, 100):  # 落点：≤1=1, ≤5=2, ≤10=3, +Inf=4
        h.observe(v)
    lines = h.render()
    assert 'fr_h_bucket{le="1"} 1' in lines
    assert 'fr_h_bucket{le="5"} 2' in lines
    assert 'fr_h_bucket{le="10"} 3' in lines
    assert 'fr_h_bucket{le="+Inf"} 4' in lines
    assert "fr_h_count 4" in lines
    assert "fr_h_sum 109.5" in lines


def test_registry_render_concatenates():
    reg = om.MetricsRegistry()
    c = reg.counter("fr_a_total", "a")
    reg.gauge("fr_b", "b")
    c.inc()
    text = reg.render()
    assert "fr_a_total" in text and "fr_b" in text
    assert text.endswith("\n")


def test_label_escaping():
    c = om.Counter("fr_e_total", "esc", ["msg"])
    c.inc(msg='a"b\\c')
    line = [x for x in c.render() if x.startswith("fr_e_total{")][0]
    assert '\\"' in line and "\\\\" in line
