"""subscription_service 纯函数单测：is_subscription_due + subscription_to_signal_body。

DB 操作（create / set_active / delete / list）由 integration test 覆盖（暂略）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from unittest.mock import MagicMock, patch

import pytest

from backend.services.subscription_service import (
    compute_config_hash,
    find_matching_active_subscription,
    is_subscription_due,
    prepare_subscription_refresh,
    subscription_to_signal_body,
)


def _make_sub(**kwargs) -> dict:
    """构造一个最小 sub dict，便于覆写关键字段。"""
    base = {
        "subscription_id": "S1",
        "is_active": 1,
        "last_refresh_at": None,
        "refresh_interval_sec": 300,
    }
    base.update(kwargs)
    return base


# ---------------------------- is_subscription_due ----------------------------


def test_inactive_subscription_never_due() -> None:
    sub = _make_sub(is_active=0, last_refresh_at=None)
    assert is_subscription_due(sub, datetime.now()) is False


def test_first_run_no_last_refresh_is_due() -> None:
    """从未跑过的订阅 → 立即 due。"""
    sub = _make_sub(is_active=1, last_refresh_at=None)
    assert is_subscription_due(sub, datetime.now()) is True


def test_within_interval_not_due() -> None:
    """距上次刷新不足 interval → 不 due。"""
    now = datetime(2026, 4, 27, 10, 0, 0)
    sub = _make_sub(
        is_active=1,
        last_refresh_at=now - timedelta(seconds=200),
        refresh_interval_sec=300,
    )
    assert is_subscription_due(sub, now) is False


def test_at_interval_boundary_is_due() -> None:
    """恰好等于 interval → due（>= 关系）。"""
    now = datetime(2026, 4, 27, 10, 0, 0)
    sub = _make_sub(
        is_active=1,
        last_refresh_at=now - timedelta(seconds=300),
        refresh_interval_sec=300,
    )
    assert is_subscription_due(sub, now) is True


def test_past_interval_is_due() -> None:
    now = datetime(2026, 4, 27, 10, 0, 0)
    sub = _make_sub(
        is_active=1,
        last_refresh_at=now - timedelta(seconds=600),
        refresh_interval_sec=300,
    )
    assert is_subscription_due(sub, now) is True


def test_floor_protects_against_too_small_interval() -> None:
    """配置 refresh_interval_sec=1 → 实际最小 30s 兜底，避免雪崩。"""
    now = datetime(2026, 4, 27, 10, 0, 0)
    sub = _make_sub(
        is_active=1,
        last_refresh_at=now - timedelta(seconds=10),  # 距上次仅 10s
        refresh_interval_sec=1,
    )
    # 配置 1s 下"距 10s" 应 due，但 floor=30 顶住 → 不 due
    assert is_subscription_due(sub, now, min_interval_floor_sec=30) is False
    # 距 31s 后才 due
    sub["last_refresh_at"] = now - timedelta(seconds=31)
    assert is_subscription_due(sub, now, min_interval_floor_sec=30) is True


# ---------------------------- subscription_to_signal_body ----------------------------


def test_subscription_to_signal_body_full_mapping() -> None:
    sub = {
        "factor_items": [{"factor_id": "momentum_n", "params": {"window": 20}}],
        "method": "ic_weighted",
        "pool_id": 5,
        "n_groups": 10,
        "ic_lookback_days": 90,
        "filter_price_limit": 1,
        "top_n": 50,
    }
    body = subscription_to_signal_body(sub)
    assert body["factor_items"] == [{"factor_id": "momentum_n", "params": {"window": 20}}]
    assert body["method"] == "ic_weighted"
    assert body["pool_id"] == 5
    assert body["n_groups"] == 10
    assert body["ic_lookback_days"] == 90
    assert body["use_realtime"] is True  # 订阅永远实时
    assert body["filter_price_limit"] is True
    assert body["top_n"] == 50


def test_subscription_to_signal_body_use_realtime_always_true() -> None:
    """订阅就是为了监控实时变化，use_realtime 永远 True 不允许覆盖。"""
    sub = {"factor_items": [], "pool_id": 5, "filter_price_limit": 0}
    body = subscription_to_signal_body(sub)
    assert body["use_realtime"] is True


def test_subscription_to_signal_body_handles_missing_optional_fields() -> None:
    """只有必填字段（pool_id）时其它走默认。"""
    sub = {"pool_id": 5}
    body = subscription_to_signal_body(sub)
    assert body["method"] == "equal"
    assert body["n_groups"] == 5
    assert body["ic_lookback_days"] == 60
    assert body["top_n"] is None
    assert body["filter_price_limit"] is True
    assert body["factor_items"] == []


# ---------------------------- find_matching_active_subscription ----------------------------


def _full_sub(**overrides) -> dict:
    """构造一个完整的 active subscription dict（用于 patch list 返回）。"""
    base = {
        "subscription_id": "S1",
        "factor_items": [{"factor_id": "momentum_n", "params": None}],
        "method": "single",
        "pool_id": 5,
        "n_groups": 5,
        "ic_lookback_days": 60,
        "filter_price_limit": 1,
        "top_n": 20,
        "is_active": 1,
        "last_run_id": None,
        "last_refresh_at": None,
        "refresh_interval_sec": 300,
        "created_at": "2026-04-28 10:00:00",
        "updated_at": "2026-04-28 10:00:00",
    }
    base.update(overrides)
    return base


def _body_matching(sub: dict) -> dict:
    """从 sub 构造一个完全匹配的 create body。"""
    return {
        "factor_items": sub["factor_items"],
        "method": sub["method"],
        "pool_id": sub["pool_id"],
        "n_groups": sub["n_groups"],
        "ic_lookback_days": sub["ic_lookback_days"],
        "filter_price_limit": bool(sub["filter_price_limit"]),
        "top_n": sub["top_n"],
    }


def test_find_matching_returns_subscription_when_exact_match() -> None:
    """完全相同配置 → 找到。"""
    sub = _full_sub()
    body = _body_matching(sub)
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[sub],
    ):
        result = find_matching_active_subscription(body)
    assert result is not None
    assert result["subscription_id"] == "S1"


def test_find_matching_returns_none_when_no_match() -> None:
    """没有任何 active 订阅 → 返 None。"""
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[],
    ):
        result = find_matching_active_subscription(_body_matching(_full_sub()))
    assert result is None


def test_find_matching_returns_none_on_pool_diff() -> None:
    sub = _full_sub(pool_id=5)
    body = _body_matching(sub)
    body["pool_id"] = 6
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[sub],
    ):
        assert find_matching_active_subscription(body) is None


def test_find_matching_returns_none_on_top_n_diff() -> None:
    sub = _full_sub(top_n=20)
    body = _body_matching(sub)
    body["top_n"] = 50
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[sub],
    ):
        assert find_matching_active_subscription(body) is None


def test_find_matching_returns_none_on_top_n_null_vs_value() -> None:
    """top_n=None 与 top_n=20 不算相同（None 是"全部"，20 是"top 20"）。"""
    sub = _full_sub(top_n=None)
    body = _body_matching(sub)
    body["top_n"] = 20
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[sub],
    ):
        assert find_matching_active_subscription(body) is None


def test_find_matching_returns_none_on_factor_items_diff() -> None:
    """因子顺序不同 → 不匹配（顺序敏感，因为 orthogonal_equal 等方法对顺序敏感）。"""
    sub = _full_sub(factor_items=[
        {"factor_id": "momentum_n"},
        {"factor_id": "bbic"},
    ])
    body = _body_matching(sub)
    body["factor_items"] = [
        {"factor_id": "bbic"},
        {"factor_id": "momentum_n"},
    ]
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[sub],
    ):
        assert find_matching_active_subscription(body) is None


def test_find_matching_picks_oldest_when_multiple_match() -> None:
    """多条匹配时返回 created_at 最早的（最稳定）。"""
    older = _full_sub(subscription_id="S_old", created_at="2026-04-25 10:00:00")
    newer = _full_sub(subscription_id="S_new", created_at="2026-04-28 10:00:00")
    body = _body_matching(older)
    with patch(
        "backend.services.subscription_service.list_subscriptions",
        return_value=[newer, older],  # 故意乱序传入
    ):
        result = find_matching_active_subscription(body)
    assert result is not None
    assert result["subscription_id"] == "S_old"


# ---------------------------- compute_config_hash ----------------------------


def test_config_hash_stable_for_same_body() -> None:
    """相同 body 必须产生相同 hash（跨调用稳定）。"""
    body = _body_matching(_full_sub())
    assert compute_config_hash(body) == compute_config_hash(body)


def test_config_hash_independent_of_dict_key_order() -> None:
    """body 字段顺序不同不应影响 hash（dict 序列化排序）。"""
    body1 = {
        "factor_items": [{"factor_id": "a", "params": {"y": 2, "x": 1}}],
        "method": "single", "pool_id": 5,
        "n_groups": 5, "ic_lookback_days": 60,
        "filter_price_limit": True, "top_n": 20,
    }
    body2 = {
        "top_n": 20, "filter_price_limit": True,
        "ic_lookback_days": 60, "n_groups": 5,
        "pool_id": 5, "method": "single",
        # params 内字段顺序也调换
        "factor_items": [{"factor_id": "a", "params": {"x": 1, "y": 2}}],
    }
    assert compute_config_hash(body1) == compute_config_hash(body2)


def test_config_hash_sensitive_to_factor_items_order() -> None:
    """factor_items 顺序变化 → hash 变（orthogonal_equal 对顺序敏感的语义要求）。"""
    body1 = {
        "factor_items": [{"factor_id": "a"}, {"factor_id": "b"}],
        "method": "single", "pool_id": 5,
    }
    body2 = {
        "factor_items": [{"factor_id": "b"}, {"factor_id": "a"}],
        "method": "single", "pool_id": 5,
    }
    assert compute_config_hash(body1) != compute_config_hash(body2)


def test_config_hash_sensitive_to_top_n() -> None:
    """top_n=None 与 top_n=20 必须不同 hash（语义不同）。"""
    body_none = {"factor_items": [{"factor_id": "a"}], "method": "single",
                 "pool_id": 5, "top_n": None}
    body_20 = {"factor_items": [{"factor_id": "a"}], "method": "single",
               "pool_id": 5, "top_n": 20}
    assert compute_config_hash(body_none) != compute_config_hash(body_20)


def test_config_hash_ignores_refresh_interval_sec() -> None:
    """refresh_interval_sec 不参与 hash（同配置不同间隔仍是同订阅）。"""
    body1 = {"factor_items": [{"factor_id": "a"}], "method": "single",
             "pool_id": 5, "refresh_interval_sec": 60}
    body2 = {"factor_items": [{"factor_id": "a"}], "method": "single",
             "pool_id": 5, "refresh_interval_sec": 600}
    assert compute_config_hash(body1) == compute_config_hash(body2)


def test_config_hash_returns_40_char_sha1() -> None:
    """hash 输出为 40 字符（SHA-1 hex）。"""
    h = compute_config_hash({"factor_items": [], "pool_id": 1})
    assert len(h) == 40
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------- prepare_subscription_refresh ----------------------------


def _make_prepare_mocks(run_id_lookups: dict[str, bool]):
    """构造 mysql_conn 假 context；fetchone 按 run_id 参数返回是否存在。

    run_id_lookups: {run_id: True/False}—— SELECT FROM fr_signal_runs WHERE run_id=%s
    时 fetchone 的返回（True → 返回 dict 模拟存在；False → None）。
    """
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda s, *a: None
    executed = []

    def _fake_execute(sql, params=()):
        executed.append((sql, params))
        # SELECT run_id ... 时 cursor.fetchone() 由下面 side_effect 决定
        if "SELECT run_id FROM fr_signal_runs" in sql:
            looked_up_id = params[0] if params else None
            cursor._next_fetchone = (
                {"run_id": looked_up_id}
                if run_id_lookups.get(looked_up_id, False)
                else None
            )
        else:
            cursor._next_fetchone = None
        return None

    cursor.execute.side_effect = _fake_execute
    cursor.fetchone.side_effect = lambda: cursor._next_fetchone

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    cm = MagicMock()
    cm.__enter__ = lambda s: conn
    cm.__exit__ = lambda s, *a: None
    return MagicMock(return_value=cm), executed


def test_prepare_uses_target_run_id_when_provided(monkeypatch) -> None:
    """传 target_run_id（存在）→ UPDATE 它；忽略 last_run_id。"""
    sub = _full_sub(last_run_id="OLD_RUN", pool_id=5)
    fake_conn, executed = _make_prepare_mocks({"TARGET_RUN": True})
    monkeypatch.setattr(
        "backend.services.subscription_service.mysql_conn", fake_conn,
    )
    with patch(
        "backend.services.subscription_service.mark_refreshed"
    ) as mark:
        run_id, body = prepare_subscription_refresh(sub, target_run_id="TARGET_RUN")

    assert run_id == "TARGET_RUN"
    # 必须 UPDATE TARGET_RUN，不能 INSERT
    sqls = [s for s, _ in executed]
    assert any("UPDATE fr_signal_runs" in s for s in sqls)
    assert not any("INSERT INTO fr_signal_runs" in s for s in sqls)
    # mark_refreshed 把 last_run_id 重指向 TARGET_RUN
    mark.assert_called_once()
    assert mark.call_args.args[1] == "TARGET_RUN"


def test_prepare_raises_when_target_run_id_missing(monkeypatch) -> None:
    """target_run_id 在表里不存在 → ValueError（前端会展示 400）。"""
    sub = _full_sub(last_run_id="OLD")
    fake_conn, _ = _make_prepare_mocks({"GHOST_RUN": False})
    monkeypatch.setattr(
        "backend.services.subscription_service.mysql_conn", fake_conn,
    )
    with patch("backend.services.subscription_service.mark_refreshed"):
        with pytest.raises(ValueError, match="GHOST_RUN.*不存在"):
            prepare_subscription_refresh(sub, target_run_id="GHOST_RUN")


def test_prepare_falls_back_to_last_run_id(monkeypatch) -> None:
    """不传 target；sub.last_run_id 存在 → UPDATE last_run_id（worker 路径）。"""
    sub = _full_sub(last_run_id="LAST_RUN")
    fake_conn, executed = _make_prepare_mocks({"LAST_RUN": True})
    monkeypatch.setattr(
        "backend.services.subscription_service.mysql_conn", fake_conn,
    )
    with patch("backend.services.subscription_service.mark_refreshed") as mark:
        run_id, _ = prepare_subscription_refresh(sub)

    assert run_id == "LAST_RUN"
    sqls = [s for s, _ in executed]
    assert any("UPDATE fr_signal_runs" in s for s in sqls)
    assert not any("INSERT INTO fr_signal_runs" in s for s in sqls)
    assert mark.call_args.args[1] == "LAST_RUN"


def test_prepare_inserts_when_no_last_run_and_no_target(monkeypatch) -> None:
    """不传 target；sub.last_run_id=None → INSERT 新 run（首次刷新）。"""
    sub = _full_sub(last_run_id=None)
    fake_conn, executed = _make_prepare_mocks({})
    monkeypatch.setattr(
        "backend.services.subscription_service.mysql_conn", fake_conn,
    )
    with patch("backend.services.subscription_service.mark_refreshed"):
        run_id, _ = prepare_subscription_refresh(sub)

    sqls = [s for s, _ in executed]
    assert any("INSERT INTO fr_signal_runs" in s for s in sqls)
    assert not any("UPDATE fr_signal_runs" in s for s in sqls)
    # 新 run_id 是 32 位 hex (uuid4().hex)
    assert len(run_id) == 32


def test_prepare_inserts_when_last_run_id_was_deleted(monkeypatch) -> None:
    """sub.last_run_id 在表里找不到（用户手动删了）→ fall back INSERT 新 run。"""
    sub = _full_sub(last_run_id="DELETED_RUN")
    fake_conn, executed = _make_prepare_mocks({"DELETED_RUN": False})
    monkeypatch.setattr(
        "backend.services.subscription_service.mysql_conn", fake_conn,
    )
    with patch("backend.services.subscription_service.mark_refreshed"):
        run_id, _ = prepare_subscription_refresh(sub)

    sqls = [s for s, _ in executed]
    assert any("INSERT INTO fr_signal_runs" in s for s in sqls)
    assert run_id != "DELETED_RUN"
    assert len(run_id) == 32
