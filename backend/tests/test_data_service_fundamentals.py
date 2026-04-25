"""DataService.load_fundamental_panel：PIT 财报展平到日频。

不依赖真实 DB；通过 monkeypatch 替换 mysql_conn 注入 mock 行。验证：
- 仅 announcement_date <= 当日的财报会被使用（PIT 不前视）
- 同 symbol 多期数据按 announcement_date 排序后 ffill
- 交易日历缺口被填上、非交易日不进 index
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pandas as pd
import pytest

from backend.storage.data_service import DataService


class _FakeCursor:
    def __init__(self, payloads: list[list[dict]]):
        self._payloads = list(payloads)
        self._current: list[dict] = []

    def execute(self, sql, params=None):
        self._current = self._payloads.pop(0) if self._payloads else []

    def fetchall(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, payloads):
        self._payloads = payloads

    def cursor(self):
        return _FakeCursor(self._payloads)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextmanager
def _fake_mysql_conn(payloads):
    yield _FakeConn(payloads)


def test_load_fundamental_panel_ffills_announcement_date_to_trading_days(monkeypatch):
    cal_rows = [{"trade_date": dt.date(2026, 1, d)} for d in (5, 6, 7, 8, 9)]
    profit_rows = [
        {"symbol": "000001.SZ", "announcement_date": dt.date(2026, 1, 6), "v": 0.1},
        {"symbol": "600000.SH", "announcement_date": dt.date(2026, 1, 8), "v": 0.2},
    ]
    monkeypatch.setattr(
        "backend.storage.data_service.mysql_conn",
        lambda: _fake_mysql_conn([cal_rows, profit_rows]),
    )

    svc = DataService()
    panel = svc.load_fundamental_panel(
        symbols=["000001.SZ", "600000.SH"],
        start=dt.date(2026, 1, 5),
        end=dt.date(2026, 1, 9),
        field="roe_avg",
    )

    assert list(panel.index) == [pd.Timestamp(2026, 1, d) for d in (5, 6, 7, 8, 9)]
    assert set(panel.columns) == {"000001.SZ", "600000.SH"}
    a = panel["000001.SZ"]
    assert pd.isna(a.loc["2026-01-05"])
    assert a.loc["2026-01-06"] == pytest.approx(0.1)
    assert a.loc["2026-01-09"] == pytest.approx(0.1)
    b = panel["600000.SH"]
    assert pd.isna(b.loc["2026-01-07"])
    assert b.loc["2026-01-08"] == pytest.approx(0.2)


def test_load_fundamental_panel_empty_when_no_disclosures(monkeypatch):
    cal_rows = [{"trade_date": dt.date(2026, 1, 5)}]
    monkeypatch.setattr(
        "backend.storage.data_service.mysql_conn",
        lambda: _fake_mysql_conn([cal_rows, []]),
    )
    svc = DataService()
    panel = svc.load_fundamental_panel(
        symbols=["000001.SZ"], start=dt.date(2026, 1, 5), end=dt.date(2026, 1, 5),
    )
    assert panel.empty


def test_load_fundamental_panel_rejects_non_whitelisted_field():
    """非白名单字段必须直接抛 ValueError，不进 SQL。"""
    with pytest.raises(ValueError, match="白名单"):
        DataService().load_fundamental_panel(
            symbols=["000001.SZ"],
            start=dt.date(2026, 1, 5),
            end=dt.date(2026, 1, 5),
            field="report_date",
        )


def test_load_fundamental_panel_rejects_non_profit_table():
    """目前只支持 fr_fundamental_profit；其它表必须抛 NotImplementedError。"""
    with pytest.raises(NotImplementedError):
        DataService().load_fundamental_panel(
            symbols=["000001.SZ"],
            start=dt.date(2026, 1, 5),
            end=dt.date(2026, 1, 5),
            table="fr_fundamental_balance",
        )


def test_load_fundamental_panel_left_seed_propagates_into_window(monkeypatch):
    """披露日早于窗口起点（左 seed）的财报值必须 ffill 进窗口第一天。

    没有这条断言，把 union(cal_index) 重构成只 reindex(cal_index) 时测试还会绿，
    但生产里所有"窗口起点前已披露"的股票第一天会变 NaN，影响所有 PIT 因子的评估。
    """
    cal_rows = [{"trade_date": dt.date(2026, 1, d)} for d in (8, 9, 12)]
    profit_rows = [
        # 披露日 1-06 早于窗口起点 1-08
        {"symbol": "000001.SZ", "announcement_date": dt.date(2026, 1, 6), "v": 0.1},
    ]
    monkeypatch.setattr(
        "backend.storage.data_service.mysql_conn",
        lambda: _fake_mysql_conn([cal_rows, profit_rows]),
    )

    panel = DataService().load_fundamental_panel(
        symbols=["000001.SZ"],
        start=dt.date(2026, 1, 8),
        end=dt.date(2026, 1, 12),
        field="roe_avg",
    )

    # 窗口第一天就该拿到左 seed 的值，不是 NaN
    assert panel["000001.SZ"].loc["2026-01-08"] == pytest.approx(0.1)
    assert panel["000001.SZ"].loc["2026-01-12"] == pytest.approx(0.1)
