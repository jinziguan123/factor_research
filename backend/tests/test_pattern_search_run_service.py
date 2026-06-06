"""pattern_search_run_service 单测：用假 mysql 连接，不连真库。

验证状态机：running → (写结果) → success；异常 → failed；中断 → aborted。
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from backend.services import pattern_search_run_service as svc
from backend.services.abort_check import AbortedError


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, params))


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _FakeCursor(self._sink)

    def commit(self):
        pass


def _fake_mysql_conn_factory(sink):
    @contextmanager
    def _cm():
        yield _FakeConn(sink)
    return _cm


class _DummyData:
    pass


@pytest.fixture
def patched(monkeypatch):
    sink: list = []
    monkeypatch.setattr(svc, "mysql_conn", _fake_mysql_conn_factory(sink))
    monkeypatch.setattr(svc, "DataService", _DummyData)
    monkeypatch.setattr(svc, "check_abort", lambda kind, run_id: None)
    return sink


def _statuses(sink):
    """从记录的 SQL 里抽出所有 status 更新值。"""
    out = []
    for sql, params in sink:
        if "UPDATE fr_pattern_search_runs SET" in sql and "status=%s" in sql and params:
            out.append(params[0])
    return out


def test_run_success_writes_results_and_marks_success(patched, monkeypatch):
    monkeypatch.setattr(svc, "search_by_image", lambda data, **kw: {
        "query_curves": [[0.0, 1.0]],
        "matches": [{"label": "AAA.SZ", "score": 0.9}],
    })
    svc.run_pattern_search_by_image("rid1", {"pool_id": 1, "images": ["x"], "top_k": 5})

    assert _statuses(patched) == ["running", "success"]
    # 结果写入了 fr_pattern_search_results，matches 序列化正确
    result_writes = [(s, p) for s, p in patched if "fr_pattern_search_results" in s]
    assert len(result_writes) == 1
    _, params = result_writes[0]
    assert params[0] == "rid1"
    assert json.loads(params[2])[0]["label"] == "AAA.SZ"


def test_run_failure_marks_failed(patched, monkeypatch):
    def _boom(data, **kw):
        raise RuntimeError("LLM 挂了")
    monkeypatch.setattr(svc, "search_by_image", _boom)
    svc.run_pattern_search_by_image("rid2", {"pool_id": 1, "images": ["x"]})

    assert _statuses(patched)[-1] == "failed"
    # 没有结果写入
    assert not any("fr_pattern_search_results" in s for s, _ in patched)


def test_run_abort_marks_aborted(patched, monkeypatch):
    def _abort(kind, run_id):
        raise AbortedError("stop")
    monkeypatch.setattr(svc, "check_abort", _abort)
    monkeypatch.setattr(svc, "search_by_image", lambda data, **kw: {"query_curves": [], "matches": []})
    svc.run_pattern_search_by_image("rid3", {"pool_id": 1, "images": ["x"]})

    assert _statuses(patched)[-1] == "aborted"
