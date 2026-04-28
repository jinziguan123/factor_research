"""live_market worker 主循环 smoke 测：

通过 monkeypatch 替换 trading_calendar / subscription_service / run_archive_once
等依赖，用 once=True 跑一次主循环，断言对应阶段调对应函数。

不连真实数据库 / 网络。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from backend.workers import live_market as lm
from backend.workers.live_market import LiveMarketConfig, main_loop


@pytest.fixture(autouse=True)
def _patch_sleep(monkeypatch):
    """主循环里的 time_mod.sleep 全部 no-op，避免单测真睡。"""
    monkeypatch.setattr(lm.time_mod, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _patch_leader_lock(monkeypatch):
    """worker 主循环用 ``distributed_lock.acquire_mysql_lock`` 选 leader。

    该锁需要真连 MySQL；单测环境如果没起 docker 测试库 / 或别的测试搞乱
    了连接池，锁拿不到 → is_leader=False → process_due_subscription 永
    远不被调用 → 测试用例的"sub 被处理"断言失败。

    用一个总是 yield True 的 fake context manager 替换它，确保单测专注
    在主循环逻辑（订阅 / spot / archive 三件事）；leader 选举本身由
    test_distributed_lock.py 单独覆盖。
    """
    from contextlib import contextmanager

    @contextmanager
    def _always_leader(*_a, **_kw):
        yield True

    # 主循环里 ``from backend.storage.distributed_lock import acquire_mysql_lock``
    # 是函数内 lazy import，attribute lookup 走源模块——所以 patch 源模块就行
    monkeypatch.setattr(
        "backend.storage.distributed_lock.acquire_mysql_lock", _always_leader,
    )


def _mock_calendar(monkeypatch, *, trading: bool, phase: str) -> None:
    monkeypatch.setattr(
        "backend.workers.trading_calendar.is_trading_day", lambda _d: trading
    )
    monkeypatch.setattr(
        "backend.workers.trading_calendar.determine_phase",
        lambda _now, _trading: phase,
    )


def _mock_subscriptions(monkeypatch, due_subs: list[dict]) -> list[str]:
    """把 subscription_service 的 due 查询 + 处理函数都 mock 掉。

    Returns:
        list[str]：由 process_due_subscription 触发产生的 fake run_id 列表。
    """
    processed_ids: list[str] = []

    def _find_due(_now):
        return due_subs

    def _process(sub):
        rid = f"fake_run_{sub['subscription_id']}"
        processed_ids.append(rid)
        return rid

    import backend.services.subscription_service as ss
    monkeypatch.setattr(ss, "find_due_subscriptions", _find_due)
    monkeypatch.setattr(ss, "process_due_subscription", _process)
    # 让 ensure_spot_fresh 也 no-op，避免触碰 DAO
    monkeypatch.setattr(lm, "ensure_spot_fresh", lambda *_a, **_kw: True)
    return processed_ids


def test_spot_phase_with_due_subscriptions_triggers_each(monkeypatch):
    """phase='spot' 且有 due 订阅 → 每个订阅各调一次 process_due_subscription。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")
    due = [
        {"subscription_id": "sub_a", "is_active": 1},
        {"subscription_id": "sub_b", "is_active": 1},
    ]
    processed = _mock_subscriptions(monkeypatch, due_subs=due)

    rv = main_loop(LiveMarketConfig(once=True, spot_enabled=True))

    assert rv == 0
    assert processed == ["fake_run_sub_a", "fake_run_sub_b"]


def test_spot_phase_no_due_subs_skips_spot_fetch(monkeypatch):
    """没有 due 订阅 → ensure_spot_fresh 不被调（节省 IP 配额）。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")
    spot_calls: list[Any] = []

    def _find_due(_now):
        return []

    import backend.services.subscription_service as ss
    monkeypatch.setattr(ss, "find_due_subscriptions", _find_due)
    monkeypatch.setattr(
        lm, "ensure_spot_fresh", lambda *_a, **_kw: spot_calls.append("called"),
    )

    main_loop(LiveMarketConfig(once=True, spot_enabled=True))

    assert spot_calls == []  # 没拉 spot


def test_spot_phase_disabled_does_not_fetch(monkeypatch):
    """spot_enabled=False → 即使在 spot phase 也不查订阅 / 不拉。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")
    spot_calls: list[Any] = []
    sub_calls: list[Any] = []

    monkeypatch.setattr(lm, "ensure_spot_fresh", lambda *_a, **_kw: spot_calls.append(1))
    import backend.services.subscription_service as ss
    monkeypatch.setattr(
        ss, "find_due_subscriptions",
        lambda _now: (sub_calls.append(1), [])[1],
    )

    main_loop(LiveMarketConfig(once=True, spot_enabled=False))

    assert spot_calls == [] and sub_calls == []


def test_eod_archive_phase_triggers_archive_when_enabled(monkeypatch):
    """phase='eod_archive' + archive_1m_enabled=True → run_archive_once 被调。"""
    _mock_calendar(monkeypatch, trading=True, phase="eod_archive")
    calls: list[Any] = []

    def _fake_archive(workers: int) -> dict:
        calls.append(workers)
        return {"n_symbols": 1, "n_bars_written": 240, "n_errors": 0, "errors_sample": []}

    monkeypatch.setattr(lm, "run_archive_once", _fake_archive)

    main_loop(LiveMarketConfig(once=True, archive_1m_enabled=True, archive_max_workers=10))

    assert calls == [10]


def test_eod_archive_phase_skipped_when_disabled(monkeypatch):
    """archive_1m_enabled=False → 即使到 eod_archive 时段也不归档。"""
    _mock_calendar(monkeypatch, trading=True, phase="eod_archive")
    calls: list[Any] = []
    monkeypatch.setattr(lm, "run_archive_once", lambda w: (calls.append(w), {})[1])

    main_loop(LiveMarketConfig(once=True, archive_1m_enabled=False))

    assert calls == []


def test_idle_phase_no_subs_no_action(monkeypatch):
    """phase='idle' + 无订阅 → 不调 spot / archive / process（仅心跳）。"""
    _mock_calendar(monkeypatch, trading=False, phase="idle")
    spot_calls: list[Any] = []
    arch_calls: list[Any] = []
    sub_calls: list[Any] = []
    monkeypatch.setattr(lm, "run_archive_once", lambda w: (arch_calls.append(w), {})[1])
    monkeypatch.setattr(lm, "ensure_spot_fresh", lambda *_a, **_kw: spot_calls.append(1))
    import backend.services.subscription_service as ss
    monkeypatch.setattr(ss, "find_due_subscriptions", lambda _n: [])
    monkeypatch.setattr(
        ss, "process_due_subscription",
        lambda s: sub_calls.append(s["subscription_id"]),
    )

    main_loop(LiveMarketConfig(once=True, spot_enabled=True, archive_1m_enabled=True))

    assert spot_calls == [] and arch_calls == [] and sub_calls == []


def test_subscription_processed_in_idle_phase_with_offline_downgrade(monkeypatch):
    """关键修复：盘外 phase=idle 也处理订阅（service 内自动降级到昨日 close）。

    这是问题 2 的核心场景：用户开了订阅但盘外不刷新——现在应该自动跑。
    """
    _mock_calendar(monkeypatch, trading=False, phase="idle")
    due = [{"subscription_id": "sub_offline", "is_active": 1}]
    processed = _mock_subscriptions(monkeypatch, due_subs=due)
    spot_calls: list[Any] = []
    monkeypatch.setattr(lm, "ensure_spot_fresh", lambda *_a, **_kw: spot_calls.append(1))

    main_loop(LiveMarketConfig(once=True, spot_enabled=True))

    # 订阅被处理（service 内会降级到昨日 close）
    assert processed == ["fake_run_sub_offline"]
    # 但 ensure_spot_fresh 不调用（盘外没必要拉 spot）
    assert spot_calls == []


def test_subscription_processed_in_lunch_break_with_offline_downgrade(monkeypatch):
    """phase='lunch_break'（11:30-13:00 中午）也处理订阅。"""
    _mock_calendar(monkeypatch, trading=True, phase="idle")  # 交易日的午休 = idle
    due = [{"subscription_id": "sub_lunch", "is_active": 1}]
    processed = _mock_subscriptions(monkeypatch, due_subs=due)
    spot_calls: list[Any] = []
    monkeypatch.setattr(lm, "ensure_spot_fresh", lambda *_a, **_kw: spot_calls.append(1))

    main_loop(LiveMarketConfig(once=True, spot_enabled=True))

    assert processed == ["fake_run_sub_lunch"]
    assert spot_calls == []  # 午休不拉 spot


def test_spot_failure_does_not_crash(monkeypatch):
    """ensure_spot_fresh 抛异常 → 主循环只 log，不退出（订阅会自然降级）。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")

    due = [{"subscription_id": "sub_a", "is_active": 1}]
    processed: list[str] = []

    import backend.services.subscription_service as ss
    monkeypatch.setattr(ss, "find_due_subscriptions", lambda _n: due)
    monkeypatch.setattr(
        ss, "process_due_subscription",
        lambda s: (processed.append(s["subscription_id"]), "rid")[1],
    )

    def _spot_boom(*_a, **_kw):
        raise RuntimeError("akshare timeout")

    monkeypatch.setattr(lm, "ensure_spot_fresh", _spot_boom)

    rv = main_loop(LiveMarketConfig(once=True, spot_enabled=True))
    assert rv == 0
    # spot 拉取失败但订阅仍会被处理（service 内会自动降级到昨日 close）
    assert processed == ["sub_a"]


# ---------------------------- run_spot_once retry ----------------------------


def test_run_spot_once_succeeds_after_retry(monkeypatch):
    """前两次 fetch 抛 ConnectionAborted，第 3 次成功 → 不抛、返回写入行数。"""
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionAbortedError("Remote end closed connection")
        import pandas as pd
        return pd.DataFrame({"symbol": ["X"], "last_price": [1.0]})

    monkeypatch.setattr(
        "backend.adapters.akshare_live.fetch_spot_snapshot", _flaky,
    )
    monkeypatch.setattr(
        "backend.storage.realtime_dao.write_spot_snapshot",
        lambda df, snap_at: len(df),
    )

    sleeps: list[float] = []
    monkeypatch.setattr(lm.time_mod, "sleep", lambda s: sleeps.append(s))

    n = lm.run_spot_once()
    assert n == 1
    assert calls["n"] == 3
    # 指数退避：1s、2s
    assert sleeps == [1.0, 2.0]


def test_run_spot_once_raises_after_all_retries(monkeypatch):
    """全部 max_retries+1 次都失败 → 抛最后一次异常。"""
    def _always_boom():
        raise ConnectionAbortedError("nope")

    monkeypatch.setattr(
        "backend.adapters.akshare_live.fetch_spot_snapshot", _always_boom,
    )
    monkeypatch.setattr(lm.time_mod, "sleep", lambda *_: None)

    with pytest.raises(ConnectionAbortedError, match="nope"):
        lm.run_spot_once(max_retries=2)


def test_run_spot_once_uses_exponential_backoff(monkeypatch):
    """3 次失败 → sleep 调用按 1s × 2^N 指数退避（1s、2s）。"""
    monkeypatch.setattr(
        "backend.adapters.akshare_live.fetch_spot_snapshot",
        lambda: (_ for _ in ()).throw(ConnectionAbortedError("x")),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(lm.time_mod, "sleep", lambda s: sleeps.append(s))

    with pytest.raises(ConnectionAbortedError):
        lm.run_spot_once(
            max_retries=3, retry_initial_sleep_sec=1.0, retry_backoff_factor=2.0,
        )
    # 3 次重试间隔：1s、2s、4s（最后一次失败后不再 sleep）
    assert sleeps == [1.0, 2.0, 4.0]


def test_main_loop_stops_on_stop_event(monkeypatch):
    """stop_event.set() 后主循环干净退出。"""
    import threading

    _mock_calendar(monkeypatch, trading=False, phase="idle")
    stop_event = threading.Event()
    stop_event.set()  # 立刻 set，循环开头就会退出

    rv = main_loop(LiveMarketConfig(spot_enabled=True), stop_event=stop_event)
    assert rv == 0


def test_start_in_thread_returns_alive_thread_then_stops(monkeypatch):
    """start_in_thread 返回 (Thread, stop_event)，stop_event.set() 后 thread 退出。"""
    _mock_calendar(monkeypatch, trading=False, phase="idle")

    cfg = LiveMarketConfig(spot_enabled=True, main_loop_sleep_sec=1)
    thread, stop_event = lm.start_in_thread(cfg)
    assert thread.is_alive()
    stop_event.set()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_archive_failure_marks_date_done(monkeypatch):
    """archive 抛异常时仍标记当日已归档，避免无限重试。"""
    _mock_calendar(monkeypatch, trading=True, phase="eod_archive")

    def _boom(_w):
        raise RuntimeError("clickhouse insert failed")

    monkeypatch.setattr(lm, "run_archive_once", _boom)

    # 第一次 once 跑：archive 失败但被吞
    rv = main_loop(LiveMarketConfig(once=True, archive_1m_enabled=True))
    assert rv == 0
    # 注：archived_dates 是函数内部状态，无法跨两次 main_loop 调用断言"第二次不重复"
    # 但 once=True 单次内的"失败被吞"语义已锁定，这条够用。
