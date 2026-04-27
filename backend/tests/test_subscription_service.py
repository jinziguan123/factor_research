"""subscription_service 纯函数单测：is_subscription_due + subscription_to_signal_body。

DB 操作（create / set_active / delete / list）由 integration test 覆盖（暂略）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.services.subscription_service import (
    is_subscription_due,
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
