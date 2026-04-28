"""realtime_dao 的单测：用 Fake ClickHouse client 跑，不依赖真实 CH 环境。

集成测试（@pytest.mark.integration）需要本地有可用的 ClickHouse + 已建好
``stock_spot_realtime`` / ``stock_bar_1m`` 表，按需运行。

覆盖（单测）：
- write_spot_snapshot 调用 ch.execute 时传入的 columnar SQL + 数据形状正确
- 字段缺失（bid1/ask1）时落 0.0 占位
- symbol 无法 resolve 时被丢弃
- 空 DataFrame 直接返回 0
- write_1m_bars 同上
- latest_spot_snapshot 反查 symbol_id → symbol，返回正确 DataFrame
- latest_spot_age_sec 计算时间差正确
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backend.storage.realtime_dao import (
    latest_spot_age_sec,
    latest_spot_snapshot,
    write_1m_bars,
    write_spot_snapshot,
)


# ---------------------------- Fakes ----------------------------


@dataclass
class FakeChClient:
    """最小 ClickHouse client 替身：记录 execute 调用 + 可预设查询返回值。"""
    executions: list[dict] = field(default_factory=list)
    # 预设：sql 关键字 → return_value
    canned_returns: dict[str, Any] = field(default_factory=dict)

    def execute(self, sql, params=None, columnar=False, with_column_types=False):
        self.executions.append(
            {"sql": sql, "params": params, "columnar": columnar,
             "with_column_types": with_column_types}
        )
        # 简单匹配：第一个匹配关键字的预设返回
        for key, val in self.canned_returns.items():
            if key in sql:
                return val
        return []  # 默认空


@dataclass
class FakeResolver:
    """符合 SymbolResolver 接口的最小替身。"""
    sym_to_id: dict[str, int] = field(default_factory=dict)
    id_to_sym: dict[int, str] = field(default_factory=dict)

    def resolve_many(self, symbols):
        return {s: self.sym_to_id[s] for s in symbols if s in self.sym_to_id}

    def resolve_symbol_id(self, symbol):
        return self.sym_to_id.get(symbol)

    def resolve_symbol(self, symbol_id):
        return self.id_to_sym.get(symbol_id)


def _make_resolver(syms_to_ids: dict[str, int]) -> FakeResolver:
    return FakeResolver(
        sym_to_id=syms_to_ids,
        id_to_sym={v: k for k, v in syms_to_ids.items()},
    )


# ---------------------------- write_spot_snapshot ----------------------------


def _spot_df_3rows() -> pd.DataFrame:
    """3 只票的规范化 spot DF（来自 fetch_spot_snapshot 输出口径）。"""
    return pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ", "300750.SZ"],
            "last_price": [1620.5, 12.3, 0.0],
            "open": [1605.0, 12.45, 0.0],
            "high": [1635.0, 12.55, 0.0],
            "low": [1602.0, 12.20, 0.0],
            "prev_close": [1601.3, 12.36, 12.00],
            "pct_chg": [0.0123, -0.005, 0.0],
            "volume": [123456, 9876543, 0],
            "amount": [2e8, 1.2e8, 0.0],
            "is_suspended": [0, 0, 1],
        }
    )


def test_write_spot_snapshot_basic_call_shape() -> None:
    df = _spot_df_3rows()
    snapshot_at = datetime(2026, 4, 27, 14, 30, 15)
    resolver = _make_resolver(
        {"600519.SH": 1001, "000001.SZ": 1002, "300750.SZ": 1003}
    )
    ch = FakeChClient()

    n = write_spot_snapshot(df, snapshot_at, resolver=resolver, ch=ch)

    assert n == 3
    assert len(ch.executions) == 1
    call = ch.executions[0]
    assert "INSERT INTO quant_data.stock_spot_realtime" in call["sql"]
    assert call["columnar"] is True
    cols = call["params"]
    assert isinstance(cols, list)
    assert len(cols) == 15  # _SPOT_COLUMNS 个数
    # symbol_id 列 dtype 必须 uint32
    assert cols[0].dtype == np.uint32
    np.testing.assert_array_equal(cols[0], np.array([1001, 1002, 1003], dtype=np.uint32))


def test_write_spot_snapshot_drops_unresolved_symbols() -> None:
    df = _spot_df_3rows()
    # 只 resolve 2 只
    resolver = _make_resolver({"600519.SH": 1001, "000001.SZ": 1002})
    ch = FakeChClient()

    n = write_spot_snapshot(df, datetime(2026, 4, 27, 14, 30), resolver=resolver, ch=ch)

    assert n == 2  # 300750.SZ 被丢
    cols = ch.executions[0]["params"]
    assert len(cols[0]) == 2


def test_write_spot_snapshot_handles_missing_bid1_ask1() -> None:
    """spot_em 不返回 bid1/ask1，DAO 应自动填 0.0 占位。"""
    df = _spot_df_3rows()  # 没有 bid1/ask1 列
    resolver = _make_resolver({"600519.SH": 1001, "000001.SZ": 1002, "300750.SZ": 1003})
    ch = FakeChClient()

    write_spot_snapshot(df, datetime(2026, 4, 27, 14, 30), resolver=resolver, ch=ch)

    cols = ch.executions[0]["params"]
    # bid1 (index 11), ask1 (index 12) 全 0
    np.testing.assert_array_equal(cols[11], np.zeros(3, dtype=np.float32))
    np.testing.assert_array_equal(cols[12], np.zeros(3, dtype=np.float32))


def test_write_spot_snapshot_empty_returns_zero() -> None:
    ch = FakeChClient()
    n = write_spot_snapshot(pd.DataFrame(), datetime(2026, 4, 27), ch=ch)
    assert n == 0
    assert ch.executions == []  # 没调用 execute


def test_write_spot_snapshot_all_unresolved_returns_zero() -> None:
    df = _spot_df_3rows()
    resolver = _make_resolver({})  # 全部 resolve 失败
    ch = FakeChClient()
    n = write_spot_snapshot(df, datetime(2026, 4, 27), resolver=resolver, ch=ch)
    assert n == 0


def test_write_spot_snapshot_uses_provided_bid1_ask1_if_present() -> None:
    """若 DataFrame 已含 bid1/ask1，DAO 应直接落库。"""
    df = _spot_df_3rows()
    df["bid1"] = [1620.0, 12.25, 0.0]
    df["ask1"] = [1620.5, 12.30, 0.0]
    resolver = _make_resolver({"600519.SH": 1001, "000001.SZ": 1002, "300750.SZ": 1003})
    ch = FakeChClient()

    write_spot_snapshot(df, datetime(2026, 4, 27, 14, 30), resolver=resolver, ch=ch)

    cols = ch.executions[0]["params"]
    np.testing.assert_array_almost_equal(
        cols[11], np.array([1620.0, 12.25, 0.0], dtype=np.float32)
    )
    np.testing.assert_array_almost_equal(
        cols[12], np.array([1620.5, 12.30, 0.0], dtype=np.float32)
    )


# ---------------------------- write_1m_bars ----------------------------


def _bars_df_3sym_2bar() -> pd.DataFrame:
    base = pd.Timestamp("2026-04-27 09:30:00")
    rows = []
    for sym in ["600519.SH", "000001.SZ", "300750.SZ"]:
        for i in range(2):
            rows.append(
                {
                    "symbol": sym,
                    "trade_time": base + pd.Timedelta(minutes=i),
                    "open": 10.0 + i * 0.1,
                    "high": 10.05 + i * 0.1,
                    "low": 9.98 + i * 0.1,
                    "close": 10.05 + i * 0.1,
                    "volume": 1000 + i * 100,
                    "amount": 10050.0 + i * 1010,
                }
            )
    return pd.DataFrame(rows)


def test_write_1m_bars_basic() -> None:
    df = _bars_df_3sym_2bar()
    resolver = _make_resolver({"600519.SH": 1001, "000001.SZ": 1002, "300750.SZ": 1003})
    ch = FakeChClient()

    n = write_1m_bars(df, resolver=resolver, ch=ch)

    assert n == 6  # 3 syms × 2 bars
    call = ch.executions[0]
    assert "INSERT INTO quant_data.stock_bar_1m" in call["sql"]
    cols = call["params"]
    assert len(cols) == 10  # _BAR_1M_COLUMNS 个数
    # symbol_id 已被 map
    np.testing.assert_array_equal(
        np.sort(np.unique(cols[0])), np.array([1001, 1002, 1003], dtype=np.uint32)
    )


def test_write_1m_bars_empty_returns_zero() -> None:
    ch = FakeChClient()
    assert write_1m_bars(pd.DataFrame(), ch=ch) == 0
    assert ch.executions == []


# ---------------------------- latest_spot_snapshot ----------------------------


def test_latest_spot_snapshot_returns_mapped_dataframe() -> None:
    resolver = _make_resolver({"600519.SH": 1001, "000001.SZ": 1002})
    # mock CH 返回值：(rows, column_types)
    mock_data = [
        (1001, datetime(2026, 4, 27, 14, 30, 15), 1620.5, 1605.0, 1635.0,
         1602.0, 1601.3, 0.0123, 123456, 2e8, 0),
        (1002, datetime(2026, 4, 27, 14, 30, 15), 12.3, 12.45, 12.55,
         12.20, 12.36, -0.005, 9876543, 1.2e8, 0),
    ]
    col_types = [
        ("symbol_id", "UInt32"),
        ("snapshot_at", "DateTime"),
        ("last_price", "Float32"),
        ("open", "Float32"),
        ("high", "Float32"),
        ("low", "Float32"),
        ("prev_close", "Float32"),
        ("pct_chg", "Float32"),
        ("volume", "UInt64"),
        ("amount", "Float64"),
        ("is_suspended", "UInt8"),
    ]
    ch = FakeChClient(canned_returns={"argMax": (mock_data, col_types)})

    df = latest_spot_snapshot(
        ["600519.SH", "000001.SZ"],
        trade_date=date(2026, 4, 27),
        resolver=resolver,
        ch=ch,
    )

    assert len(df) == 2
    # symbol_id 列已 drop，symbol 列已加
    assert "symbol_id" not in df.columns
    assert set(df["symbol"]) == {"600519.SH", "000001.SZ"}


def test_latest_spot_snapshot_empty_input() -> None:
    df = latest_spot_snapshot([], ch=FakeChClient())
    assert df.empty


def test_latest_spot_snapshot_no_resolvable_symbols() -> None:
    resolver = _make_resolver({})
    df = latest_spot_snapshot(["600519.SH"], resolver=resolver, ch=FakeChClient())
    assert df.empty


def test_latest_spot_snapshot_no_data_returns_empty() -> None:
    resolver = _make_resolver({"600519.SH": 1001})
    # CH 返回空 rows
    ch = FakeChClient(canned_returns={"argMax": ([], [])})
    df = latest_spot_snapshot(["600519.SH"], resolver=resolver, ch=ch)
    assert df.empty


# ---------------------------- latest_spot_age_sec ----------------------------


def test_latest_spot_age_sec_computes_seconds() -> None:
    """库里最新 snapshot 距 NOW() 的秒数。"""
    # 30 秒前
    last = datetime.now() - timedelta(seconds=30)
    ch = FakeChClient(canned_returns={"max(snapshot_at)": [(last,)]})
    age = latest_spot_age_sec(trade_date=date.today(), ch=ch)
    assert age is not None
    # 容许 1s 抖动
    assert 29 <= age <= 32


def test_latest_spot_age_sec_returns_none_when_empty() -> None:
    ch = FakeChClient(canned_returns={"max(snapshot_at)": [(None,)]})
    assert latest_spot_age_sec(ch=ch) is None


def test_latest_spot_age_sec_returns_none_when_no_rows() -> None:
    ch = FakeChClient(canned_returns={"max(snapshot_at)": []})
    assert latest_spot_age_sec(ch=ch) is None


# ---------------------------- 表不存在友好降级 ----------------------------


class _ChRaisingUnknownTable:
    """模拟 migration 008 未跑，CH 抛 ServerException(code=60)。"""
    def execute(self, *args, **kwargs):
        from clickhouse_driver.errors import ServerException
        raise ServerException(
            "Code: 60. DB::Exception: Unknown table expression identifier "
            "'quant_data.stock_spot_realtime'",
            code=60,
        )


class _ChRaisingOtherError:
    def execute(self, *args, **kwargs):
        from clickhouse_driver.errors import ServerException
        raise ServerException("connection refused", code=210)


def test_latest_spot_age_sec_returns_none_when_table_missing() -> None:
    """migration 008 未跑：表不存在，函数返 None 而非崩。"""
    assert latest_spot_age_sec(ch=_ChRaisingUnknownTable()) is None


def test_latest_spot_age_sec_propagates_other_errors() -> None:
    """非"表不存在"的错误应正常抛出（如网络 / 权限）。"""
    from clickhouse_driver.errors import ServerException
    with pytest.raises(ServerException) as exc_info:
        latest_spot_age_sec(ch=_ChRaisingOtherError())
    assert exc_info.value.code == 210


def test_latest_spot_snapshot_returns_empty_when_table_missing() -> None:
    """latest_spot_snapshot 同样降级到空 DataFrame。"""
    resolver = _make_resolver({"600519.SH": 1001})
    df = latest_spot_snapshot(
        ["600519.SH"], resolver=resolver, ch=_ChRaisingUnknownTable(),
    )
    assert df.empty


def test_latest_spot_snapshot_propagates_other_errors() -> None:
    from clickhouse_driver.errors import ServerException
    resolver = _make_resolver({"600519.SH": 1001})
    with pytest.raises(ServerException):
        latest_spot_snapshot(
            ["600519.SH"], resolver=resolver, ch=_ChRaisingOtherError(),
        )
