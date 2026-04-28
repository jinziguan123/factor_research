"""datasource_probe_service：把 4 个真实探测函数 mock 掉，验证聚合 + 容错。

不动真实 akshare / baostock / DB——这是服务的契约（每个 probe_xxx 自己抓异常
返回 (ok, msg, ms)）。这里测：
- _timed 在 fn 抛异常时返回 (False, "ErrType: msg", latency_ms)
- _timed 在 fn 正常返回时返回 (True, msg, latency_ms)
- probe_all 把 _PROBES 里所有项串行跑一遍，单项失败不影响后续
- probe_all 输出形态符合前端约定（name/status/latency_ms/message/tested_at）
"""
from __future__ import annotations

from unittest.mock import patch

from backend.services import datasource_probe_service as svc


# ---------------------------- _timed ----------------------------


def test_timed_returns_ok_with_message() -> None:
    """fn 正常返回 → (True, msg, latency_ms>=0)。"""
    ok, msg, ms = svc._timed(lambda: "5217 codes")
    assert ok is True
    assert msg == "5217 codes"
    assert ms >= 0


def test_timed_catches_exception_with_type_prefix() -> None:
    """fn 抛异常 → (False, "ErrType: <msg>", ms)，不向外抛。"""
    def _boom() -> str:
        raise RuntimeError("connection aborted")

    ok, msg, ms = svc._timed(_boom)
    assert ok is False
    assert msg == "RuntimeError: connection aborted"
    assert ms >= 0


def test_timed_catches_value_error() -> None:
    """不同异常类型前缀正确。"""
    def _bad() -> str:
        raise ValueError("nope")

    ok, msg, _ = svc._timed(_bad)
    assert ok is False
    assert msg.startswith("ValueError:")


# ---------------------------- probe_all ----------------------------


def test_probe_all_returns_one_entry_per_source() -> None:
    """每个 _PROBES 项都返回一条结果。"""
    fake_probes = [
        ("a", lambda: (True, "a-ok", 10)),
        ("b", lambda: (True, "b-ok", 20)),
    ]
    with patch.object(svc, "_PROBES", fake_probes):
        results = svc.probe_all()
    assert len(results) == 2
    assert [r["name"] for r in results] == ["a", "b"]


def test_probe_all_one_failure_does_not_block_others() -> None:
    """中间一项失败时后面的探测仍然跑。"""
    fake_probes = [
        ("a", lambda: (True, "ok", 10)),
        ("b", lambda: (False, "RuntimeError: x", 20)),
        ("c", lambda: (True, "ok", 30)),
    ]
    with patch.object(svc, "_PROBES", fake_probes):
        results = svc.probe_all()
    assert [r["status"] for r in results] == ["ok", "error", "ok"]


def test_probe_all_result_shape_matches_frontend_contract() -> None:
    """每条结果包含前端依赖的字段：name/status/latency_ms/message/tested_at。"""
    fake_probes = [("ds", lambda: (True, "fine", 42))]
    with patch.object(svc, "_PROBES", fake_probes):
        [row] = svc.probe_all()
    assert set(row.keys()) == {"name", "status", "latency_ms", "message", "tested_at"}
    assert row["name"] == "ds"
    assert row["status"] == "ok"
    assert row["latency_ms"] == 42
    assert row["message"] == "fine"
    # tested_at 是 ISO 8601 (秒级) 字符串
    assert "T" in row["tested_at"]
    assert len(row["tested_at"]) == 19  # YYYY-MM-DDTHH:MM:SS


def test_probe_all_status_field_is_string_ok_or_error() -> None:
    fake_probes = [
        ("good", lambda: (True, "y", 1)),
        ("bad", lambda: (False, "n", 2)),
    ]
    with patch.object(svc, "_PROBES", fake_probes):
        results = svc.probe_all()
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"


def test_default_probes_registered_in_order() -> None:
    """生产 _PROBES 顺序固定（前端依赖此顺序展示）。

    akshare 与 akshare-spot 是两个独立故障域：前者走 stock_info_a_code_name
    （代码字典），后者走 stock_zh_a_spot_em（push2 行情），后者更脆弱。
    """
    names = [name for name, _ in svc._PROBES]
    assert names == [
        "akshare", "akshare-spot", "baostock", "mysql", "clickhouse",
    ]
