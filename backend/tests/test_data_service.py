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
    """验证前复权生效：sid=1 在 2024-01-15 前因子=1.0，之后=0.5

    期望：除权前 raw == adj；除权后 adj == raw * 0.5。
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
    # 除权前（2024-01-15 之前）：因子=1，原价应等于复权价
    cutoff = pd.Timestamp("2024-01-15")
    before_raw = raw.loc[raw.index < cutoff, "000001.SZ"]
    before_adj = adj.loc[adj.index < cutoff, "000001.SZ"]
    assert len(before_raw) > 0
    pd.testing.assert_series_equal(
        before_raw, before_adj, check_exact=False, rtol=1e-6
    )

    # 除权后：因子=0.5，复权价应 = 原价 * 0.5
    after_raw = raw.loc[raw.index >= cutoff, "000001.SZ"]
    after_adj = adj.loc[adj.index >= cutoff, "000001.SZ"]
    assert len(after_raw) > 0
    # 取第一个除权后的日期比对
    first_raw = after_raw.iloc[0]
    first_adj = after_adj.iloc[0]
    assert first_adj == pytest.approx(first_raw * 0.5, rel=1e-6)


@pytest.mark.integration
def test_qfq_adjustment_applies_to_ohlc(seed_bar_1d, seed_qfq_factor):
    """qfq 因子应作用于 open/high/low/close 四个价格字段，不影响 volume/amount_k。"""
    from backend.storage.data_service import DataService

    svc = DataService()
    raw = svc.load_bars(
        ["000001.SZ"], date(2024, 1, 2), date(2024, 1, 31), adjust="none"
    )["000001.SZ"]
    adj = svc.load_bars(
        ["000001.SZ"], date(2024, 1, 2), date(2024, 1, 31), adjust="qfq"
    )["000001.SZ"]
    cutoff = pd.Timestamp("2024-01-15")
    # 除权后所有 OHLC 字段都 × 0.5
    for col in ("open", "high", "low", "close"):
        r = raw.loc[raw.index >= cutoff, col].iloc[0]
        a = adj.loc[adj.index >= cutoff, col].iloc[0]
        assert a == pytest.approx(r * 0.5, rel=1e-6), f"字段 {col} 复权失败"
    # volume / amount_k 不应被因子影响
    r_vol = raw.loc[raw.index >= cutoff, "volume"].iloc[0]
    a_vol = adj.loc[adj.index >= cutoff, "volume"].iloc[0]
    assert r_vol == a_vol


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
