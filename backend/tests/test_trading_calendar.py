"""trading_calendar 纯函数单测：

- determine_phase 覆盖所有边界（9:25 / 11:30 / 13:00 / 15:00 / 15:30）+ 非交易日
- is_trading_day 仅做逻辑分支测试（mock mysql_conn），不连真实库
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from backend.workers.trading_calendar import determine_phase, is_trading_day


# ---------------------------- determine_phase ----------------------------


def _dt(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 4, 27, h, m, s)


@pytest.mark.parametrize(
    "now,expected",
    [
        # 开盘前
        (_dt(0, 0), "idle"),
        (_dt(8, 0), "idle"),
        (_dt(9, 24, 59), "idle"),
        # 集合竞价开始边界
        (_dt(9, 25), "spot"),
        # 上午盘中
        (_dt(10, 30), "spot"),
        # 上午收盘边界
        (_dt(11, 30), "spot"),
        (_dt(11, 30, 1), "idle"),
        # 午休
        (_dt(12, 0), "idle"),
        (_dt(12, 59, 59), "idle"),
        # 下午开盘边界
        (_dt(13, 0), "spot"),
        # 下午盘中
        (_dt(14, 30), "spot"),
        # 收盘边界
        (_dt(15, 0), "spot"),
        (_dt(15, 0, 1), "eod_archive"),
        # 归档时段
        (_dt(15, 15), "eod_archive"),
        (_dt(15, 30), "eod_archive"),
        (_dt(15, 30, 1), "idle"),
        # 收盘后
        (_dt(20, 0), "idle"),
        (_dt(23, 59, 59), "idle"),
    ],
)
def test_determine_phase_trading_day(now: datetime, expected: str) -> None:
    assert determine_phase(now, today_is_trading_day=True) == expected


def test_determine_phase_non_trading_day_always_idle() -> None:
    """非交易日全天 idle（即便在交易时段内）。"""
    for h in range(0, 24):
        for m in [0, 30]:
            assert determine_phase(_dt(h, m), today_is_trading_day=False) == "idle"


# ---------------------------- is_trading_day ----------------------------


def test_is_trading_day_weekend_returns_false_without_db() -> None:
    """周六周日不查库直接 False。"""
    saturday = date(2026, 4, 25)  # Saturday
    sunday = date(2026, 4, 26)
    assert saturday.weekday() == 5
    assert sunday.weekday() == 6
    # 即便 mysql_conn 抛错也不会被触发（早返）
    with patch(
        "backend.workers.trading_calendar.mysql_conn",
        side_effect=AssertionError("不应被调用"),
    ):
        assert is_trading_day(saturday) is False
        assert is_trading_day(sunday) is False


def _make_mysql_mock(row):
    """构造一个返回指定 row 的 mysql_conn 上下文 mock。"""
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda s, *a: None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None
    cm = MagicMock()
    cm.__enter__ = lambda s: conn
    cm.__exit__ = lambda s, *a: None
    return MagicMock(return_value=cm)


def test_is_trading_day_weekday_with_open_record() -> None:
    """工作日 + fr_trade_calendar 标记 is_open=1 → True。"""
    monday = date(2026, 4, 27)
    assert monday.weekday() == 0
    with patch(
        "backend.workers.trading_calendar.mysql_conn",
        _make_mysql_mock({"is_open": 1}),
    ):
        assert is_trading_day(monday) is True


def test_is_trading_day_weekday_with_closed_record() -> None:
    """工作日 + 标记 is_open=0（如清明节落工作日）→ False。"""
    holiday_monday = date(2026, 4, 6)  # Monday，清明节调休（举例）
    with patch(
        "backend.workers.trading_calendar.mysql_conn",
        _make_mysql_mock({"is_open": 0}),
    ):
        assert is_trading_day(holiday_monday) is False


def test_is_trading_day_weekday_no_record_returns_false() -> None:
    """工作日但日历无记录（未同步未来日期）→ False（fail-safe，不空跑）。"""
    monday = date(2026, 4, 27)
    with patch(
        "backend.workers.trading_calendar.mysql_conn",
        _make_mysql_mock(None),
    ):
        assert is_trading_day(monday) is False
