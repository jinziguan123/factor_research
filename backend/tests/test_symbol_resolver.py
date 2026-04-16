"""SymbolResolver 集成测试：验证 symbol ↔ symbol_id 互转。

依赖：本地测试库 MySQL (127.0.0.1:3306, myuser/mypassword/quant_data)
的 ``stock_symbol`` 表已种入 symbol_id 1..5 的测试数据（由部署脚本负责）。
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_resolve_symbol_roundtrip():
    """symbol → symbol_id → symbol 往返一致。"""
    from backend.storage.symbol_resolver import SymbolResolver

    r = SymbolResolver()
    # 测试库已 seed 5 只股票，symbol_id 为 1..5
    assert r.resolve_symbol_id("000001.SZ") == 1
    assert r.resolve_symbol(1) == "000001.SZ"


@pytest.mark.integration
def test_resolve_unknown_returns_none():
    """未知 symbol 应返回 None，而不是抛异常。"""
    from backend.storage.symbol_resolver import SymbolResolver

    r = SymbolResolver()
    assert r.resolve_symbol_id("999999.XX") is None


@pytest.mark.integration
def test_resolve_many_filters_unknown():
    """批量 resolve 时，未知 symbol 应被过滤掉而不是返回 None。"""
    from backend.storage.symbol_resolver import SymbolResolver

    r = SymbolResolver()
    m = r.resolve_many(["000001.SZ", "600519.SH", "999999.XX"])
    assert m == {"000001.SZ": 1, "600519.SH": 5}


@pytest.mark.integration
def test_resolve_symbol_id_case_and_whitespace():
    """symbol 应被 strip + upper 规范化后再查询。"""
    from backend.storage.symbol_resolver import SymbolResolver

    r = SymbolResolver()
    assert r.resolve_symbol_id("  000001.sz  ") == 1
