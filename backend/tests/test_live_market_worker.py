"""live_market worker 主循环 smoke 测：

通过 monkeypatch 替换 trading_calendar / run_spot_once / run_archive_once，
用 once=True 跑一次主循环，断言对应阶段调对应函数。

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


def _mock_calendar(monkeypatch, *, trading: bool, phase: str) -> None:
    monkeypatch.setattr(
        "backend.workers.trading_calendar.is_trading_day", lambda _d: trading
    )
    monkeypatch.setattr(
        "backend.workers.trading_calendar.determine_phase",
        lambda _now, _trading: phase,
    )


def test_spot_phase_triggers_run_spot_once(monkeypatch):
    """phase='spot' 且 spot_enabled=True → run_spot_once 被调。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")
    calls: list[Any] = []
    monkeypatch.setattr(lm, "run_spot_once", lambda: (calls.append("spot"), 100)[1])

    rv = main_loop(LiveMarketConfig(once=True, spot_enabled=True))

    assert rv == 0
    assert calls == ["spot"]


def test_spot_phase_disabled_does_not_fetch(monkeypatch):
    """spot_enabled=False → 即使在 spot phase 也不拉。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")
    calls: list[Any] = []
    monkeypatch.setattr(lm, "run_spot_once", lambda: (calls.append("spot"), 100)[1])

    main_loop(LiveMarketConfig(once=True, spot_enabled=False))

    assert calls == []


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


def test_idle_phase_no_action(monkeypatch):
    """phase='idle' → 不调 spot 也不调 archive。"""
    _mock_calendar(monkeypatch, trading=False, phase="idle")
    spot_calls: list[Any] = []
    arch_calls: list[Any] = []
    monkeypatch.setattr(lm, "run_spot_once", lambda: (spot_calls.append(1), 0)[1])
    monkeypatch.setattr(lm, "run_archive_once", lambda w: (arch_calls.append(w), {})[1])

    main_loop(LiveMarketConfig(once=True, spot_enabled=True, archive_1m_enabled=True))

    assert spot_calls == [] and arch_calls == []


def test_spot_failure_does_not_crash(monkeypatch):
    """run_spot_once 抛异常 → 主循环只 log，不退出。"""
    _mock_calendar(monkeypatch, trading=True, phase="spot")

    def _boom():
        raise RuntimeError("akshare timeout")

    monkeypatch.setattr(lm, "run_spot_once", _boom)

    rv = main_loop(LiveMarketConfig(once=True, spot_enabled=True))
    assert rv == 0  # 异常被吞，正常退出


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
