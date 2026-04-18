"""DataService 集成测试：覆盖 load_bars / load_panel / resolve_pool / qfq 复权。

依赖本地测试库，fixture 见 ``conftest.py``：
- ``seed_bar_1d``：填充 stock_bar_1d（5 symbol × 约 22 个交易日）
- ``seed_qfq_factor``：填充 fr_qfq_factor，其中 sid=1 在 2024-01-15 后因子=0.5
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest


@pytest.mark.integration
def test_load_bars_basic(seed_bar_1d):
    """load_bars 返回 dict[symbol, DataFrame]，index 是 DatetimeIndex，字段齐全。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    bars = svc.load_bars(
        ["000001.SZ", "000002.SZ"],
        date(2024, 1, 1),
        date(2024, 1, 31),
        adjust="none",
    )
    assert set(bars.keys()) == {"000001.SZ", "000002.SZ"}
    df1 = bars["000001.SZ"]
    assert isinstance(df1.index, pd.DatetimeIndex)
    assert set(df1.columns) >= {"open", "high", "low", "close", "volume", "amount_k"}
    assert df1.index.is_monotonic_increasing
    # seed: 每只股票 22 个交易日（2024-01-02..01-31 去掉周末）
    assert len(df1) >= 20
    # 价格为 base_price = 10 + sid_id → sid=1 → open=11.0
    assert df1["open"].iloc[0] == pytest.approx(11.0, rel=1e-6)


@pytest.mark.integration
def test_load_bars_returns_empty_for_unknown_symbols():
    """完全未知的 symbol 列表应返回空 dict，而不是报错。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    bars = svc.load_bars(["999998.XX", "999999.XX"], date(2024, 1, 1), date(2024, 1, 31))
    assert bars == {}


@pytest.mark.integration
def test_load_bars_rejects_non_daily_freq():
    """MVP 仅支持 1d；其它频率应显式抛 NotImplementedError 而非静默回退。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    with pytest.raises(NotImplementedError):
        svc.load_bars(["000001.SZ"], date(2024, 1, 1), date(2024, 1, 31), freq="1m")


@pytest.mark.integration
def test_load_panel_close(seed_bar_1d):
    """load_panel 返回宽表 DataFrame（index=date, columns=symbol），按日期升序。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    panel = svc.load_panel(
        ["000001.SZ", "000002.SZ"],
        date(2024, 1, 1),
        date(2024, 1, 31),
        field="close",
        adjust="none",
    )
    assert isinstance(panel, pd.DataFrame)
    assert {"000001.SZ", "000002.SZ"}.issubset(panel.columns)
    assert panel.index.is_monotonic_increasing
    # close = base_price + 0.1 = 11.1 / 12.1
    assert panel["000001.SZ"].iloc[0] == pytest.approx(11.1, rel=1e-6)
    assert panel["000002.SZ"].iloc[0] == pytest.approx(12.1, rel=1e-6)


@pytest.mark.integration
def test_load_panel_empty_when_no_data():
    """没数据时 load_panel 返回空 DataFrame，不抛异常。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    # 跨越到 2099 年肯定没数据
    panel = svc.load_panel(
        ["000001.SZ"], date(2099, 1, 1), date(2099, 12, 31), adjust="none"
    )
    assert isinstance(panel, pd.DataFrame)
    assert panel.empty


@pytest.mark.integration
def test_qfq_adjustment_applied(seed_bar_1d, seed_qfq_factor):
    """验证前复权：sid=1 在 2024-01-15 有一次 2:1 拆股事件（r=2.0）。

    cum_today=2.0，归一化后：
    - 2024-01-15 之前：qfq=1/2=0.5 → adj = raw * 0.5
    - 2024-01-15 及之后：qfq=1.0 → adj = raw（不变）
    """
    from backend.storage.data_service import DataService

    svc = DataService()
    raw = svc.load_panel(
        ["000001.SZ"],
        date(2024, 1, 2),
        date(2024, 1, 31),
        field="close",
        adjust="none",
    )
    adj = svc.load_panel(
        ["000001.SZ"],
        date(2024, 1, 2),
        date(2024, 1, 31),
        field="close",
        adjust="qfq",
    )
    cutoff = pd.Timestamp("2024-01-15")
    # 事件之前：复权价应 = 原价 * 0.5
    before_raw = raw.loc[raw.index < cutoff, "000001.SZ"]
    before_adj = adj.loc[adj.index < cutoff, "000001.SZ"]
    assert len(before_raw) > 0
    for ts in before_raw.index:
        assert before_adj.loc[ts] == pytest.approx(
            before_raw.loc[ts] * 0.5, rel=1e-6
        ), f"日期 {ts} 除权前复权价不等于 raw * 0.5"

    # 事件当日及之后：qfq=1，复权价 == 原价
    after_raw = raw.loc[raw.index >= cutoff, "000001.SZ"]
    after_adj = adj.loc[adj.index >= cutoff, "000001.SZ"]
    assert len(after_raw) > 0
    pd.testing.assert_series_equal(
        after_raw, after_adj, check_exact=False, rtol=1e-6
    )


@pytest.mark.integration
def test_qfq_adjustment_applies_to_ohlc(seed_bar_1d, seed_qfq_factor):
    """qfq 乘子应作用于 open/high/low/close 四个字段，不影响 volume/amount_k。

    事件之前乘 0.5；事件之后不变。这里断言事件之前的 OHLC 都被压一半。
    """
    from backend.storage.data_service import DataService

    svc = DataService()
    raw = svc.load_bars(
        ["000001.SZ"], date(2024, 1, 2), date(2024, 1, 31), adjust="none"
    )["000001.SZ"]
    adj = svc.load_bars(
        ["000001.SZ"], date(2024, 1, 2), date(2024, 1, 31), adjust="qfq"
    )["000001.SZ"]
    cutoff = pd.Timestamp("2024-01-15")
    # 除权之前所有 OHLC 字段都 × 0.5
    for col in ("open", "high", "low", "close"):
        r = raw.loc[raw.index < cutoff, col].iloc[0]
        a = adj.loc[adj.index < cutoff, col].iloc[0]
        assert a == pytest.approx(r * 0.5, rel=1e-6), f"字段 {col} 除权前复权失败"
    # volume / amount_k 不应被因子影响
    r_vol = raw.loc[raw.index < cutoff, "volume"].iloc[0]
    a_vol = adj.loc[adj.index < cutoff, "volume"].iloc[0]
    assert r_vol == a_vol


@pytest.mark.integration
def test_qfq_handles_single_event_long_before_window(seed_bar_1d):
    """验证：唯一的事件落在查询窗口**之前**时，窗口内 qfq=1（因为 cum 已归一）。

    按新语义：cum_today=0.3，查询窗口每一天 cum=0.3，qfq=0.3/0.3=1 → 价格不变。
    （这里不再像旧实现那样把 0.3 直接当成乘子乘到价格上——那是错误语义。）

    注意：conftest._TEST_SYMBOL_IDS 里的 sid 与真实 ``stock_symbol`` 不一定一一对应，
    要用 resolver 拿到真实 sid 再往 fr_qfq_factor 里插，否则因子和 bar 对不上导致
    测试"误通过"。
    """
    from backend.storage.data_service import DataService
    from backend.storage.mysql_client import mysql_conn
    from backend.storage.symbol_resolver import SymbolResolver

    target_symbol = "000001.SZ"
    sid = SymbolResolver().resolve_many([target_symbol])[target_symbol]

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM fr_qfq_factor WHERE symbol_id=%s", (sid,))
            cur.execute(
                "INSERT INTO fr_qfq_factor "
                "(symbol_id, trade_date, factor, source_file_mtime) "
                "VALUES (%s, %s, %s, %s)",
                (sid, date(2023, 6, 1), 0.3, 1_700_000_000),
            )
        c.commit()
    try:
        svc = DataService()
        raw = svc.load_panel(
            [target_symbol], date(2024, 1, 2), date(2024, 1, 10),
            field="close", adjust="none",
        )
        adj = svc.load_panel(
            [target_symbol], date(2024, 1, 2), date(2024, 1, 10),
            field="close", adjust="qfq",
        )
        assert not raw.empty
        pd.testing.assert_series_equal(
            raw[target_symbol], adj[target_symbol], check_exact=False, rtol=1e-6
        )
    finally:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM fr_qfq_factor WHERE symbol_id=%s", (sid,))
            c.commit()


@pytest.mark.integration
def test_qfq_multiple_events_cumprod(seed_bar_1d):
    """验证多事件累乘：2024-01-10 有 r=1.5，2024-01-20 有 r=2.0。

    cum_today = 1.5 * 2.0 = 3.0
    - 2024-01-09 及之前：cum=1 → qfq=1/3
    - [2024-01-10, 2024-01-20): cum=1.5 → qfq=0.5
    - 2024-01-20 及之后：cum=3 → qfq=1

    sid 同样用 resolver 从真实 stock_symbol 查，避免与 conftest 的 _TEST_SYMBOL_IDS
    假设错位。用 000001.SZ 但避开 seed_qfq_factor fixture（不依赖它）。
    """
    from backend.storage.data_service import DataService
    from backend.storage.mysql_client import mysql_conn
    from backend.storage.symbol_resolver import SymbolResolver

    target_symbol = "000001.SZ"
    sid = SymbolResolver().resolve_many([target_symbol])[target_symbol]

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM fr_qfq_factor WHERE symbol_id=%s", (sid,))
            cur.executemany(
                "INSERT INTO fr_qfq_factor "
                "(symbol_id, trade_date, factor, source_file_mtime) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (sid, date(2024, 1, 10), 1.5, 1_700_000_000),
                    (sid, date(2024, 1, 20), 2.0, 1_700_000_000),
                ],
            )
        c.commit()
    try:
        svc = DataService()
        raw = svc.load_panel(
            [target_symbol], date(2024, 1, 2), date(2024, 1, 31),
            field="close", adjust="none",
        )
        adj = svc.load_panel(
            [target_symbol], date(2024, 1, 2), date(2024, 1, 31),
            field="close", adjust="qfq",
        )
        assert not raw.empty
        cut1 = pd.Timestamp("2024-01-10")
        cut2 = pd.Timestamp("2024-01-20")

        pre = raw.index < cut1
        assert pre.any()
        for ts in raw.index[pre]:
            assert adj.loc[ts, target_symbol] == pytest.approx(
                raw.loc[ts, target_symbol] / 3.0, rel=1e-6
            )
        mid = (raw.index >= cut1) & (raw.index < cut2)
        assert mid.any()
        for ts in raw.index[mid]:
            assert adj.loc[ts, target_symbol] == pytest.approx(
                raw.loc[ts, target_symbol] * 0.5, rel=1e-6
            )
        post = raw.index >= cut2
        assert post.any()
        for ts in raw.index[post]:
            assert adj.loc[ts, target_symbol] == pytest.approx(
                raw.loc[ts, target_symbol], rel=1e-6
            )
    finally:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM fr_qfq_factor WHERE symbol_id=%s", (sid,))
            c.commit()


@pytest.mark.integration
def test_resolve_pool_returns_ordered_symbols():
    """resolve_pool 需按 stock_pool_symbol.sort_order 返回 symbol 列表。"""
    from backend.storage.data_service import DataService
    from backend.storage.mysql_client import mysql_conn

    # 临时创建一个 pool，带两个 symbol（sort_order 测试乱序）
    with mysql_conn() as c:
        with c.cursor() as cur:
            # 清理可能残留
            cur.execute(
                "DELETE FROM stock_pool_symbol "
                "WHERE pool_id IN (SELECT pool_id FROM stock_pool WHERE pool_name=%s)",
                ("__fr_test_pool__",),
            )
            cur.execute(
                "DELETE FROM stock_pool WHERE pool_name=%s", ("__fr_test_pool__",)
            )
            cur.execute(
                "INSERT INTO stock_pool (owner_key, pool_name) VALUES (%s, %s)",
                ("factor_research", "__fr_test_pool__"),
            )
            pool_id = cur.lastrowid
            # 故意 symbol_id=2 排在前（sort_order=0），验证排序是按 sort_order 而非 symbol_id
            cur.executemany(
                "INSERT INTO stock_pool_symbol (pool_id, symbol_id, sort_order) "
                "VALUES (%s, %s, %s)",
                [(pool_id, 2, 0), (pool_id, 1, 1)],
            )
        c.commit()
    try:
        svc = DataService()
        syms = svc.resolve_pool(pool_id)
        # sort_order=0 的 symbol_id=2（000002.SZ）应排在前
        assert syms == ["000002.SZ", "000001.SZ"]
    finally:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "DELETE FROM stock_pool_symbol WHERE pool_id=%s", (pool_id,)
                )
                cur.execute("DELETE FROM stock_pool WHERE pool_id=%s", (pool_id,))
            c.commit()
