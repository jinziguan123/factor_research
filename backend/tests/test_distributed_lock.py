"""distributed_lock 纯单测：mock mysql_conn，验证 GET_LOCK / RELEASE_LOCK 行为。

不依赖真实 MySQL；用 fake conn / cursor 验证 SQL 调用次数 + 参数。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.storage import distributed_lock


def _make_fake_conn(get_lock_returns):
    """构造一个 fake mysql_conn 上下文：execute 返回预设值。

    get_lock_returns: dict 或 list[dict]——execute SELECT GET_LOCK 时的 fetchone 返回。
    """
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda s, *a: None

    fetched = list(get_lock_returns) if isinstance(get_lock_returns, list) else [get_lock_returns]
    cursor.fetchone.side_effect = fetched

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    cm = MagicMock()
    cm.__enter__ = lambda s: conn
    cm.__exit__ = lambda s, *a: None
    return MagicMock(return_value=cm), cursor


def test_acquire_lock_yields_true_when_got(monkeypatch):
    """GET_LOCK 返回 1 (拿到) → yield True。"""
    fake, cursor = _make_fake_conn({"got": 1})
    monkeypatch.setattr(distributed_lock, "mysql_conn", fake)

    with distributed_lock.acquire_mysql_lock("lock_a") as got:
        assert got is True

    # 验证：执行了 GET_LOCK + RELEASE_LOCK
    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("GET_LOCK" in s for s in sqls)
    assert any("RELEASE_LOCK" in s for s in sqls)


def test_acquire_lock_yields_false_when_timeout(monkeypatch):
    """GET_LOCK 返回 0 (超时) → yield False，且不调 RELEASE_LOCK。"""
    fake, cursor = _make_fake_conn({"got": 0})
    monkeypatch.setattr(distributed_lock, "mysql_conn", fake)

    with distributed_lock.acquire_mysql_lock("lock_b", timeout=0) as got:
        assert got is False

    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("GET_LOCK" in s for s in sqls)
    # 没拿到锁不应释放（释放别人的锁）
    assert not any("RELEASE_LOCK" in s for s in sqls)


def test_acquire_lock_yields_false_when_null(monkeypatch):
    """GET_LOCK 返回 NULL（错误）→ yield False。"""
    fake, _ = _make_fake_conn({"got": None})
    monkeypatch.setattr(distributed_lock, "mysql_conn", fake)

    with distributed_lock.acquire_mysql_lock("lock_c") as got:
        assert got is False


def test_acquire_lock_passes_timeout_param(monkeypatch):
    """timeout 应作为 GET_LOCK 第二参数。"""
    fake, cursor = _make_fake_conn({"got": 1})
    monkeypatch.setattr(distributed_lock, "mysql_conn", fake)

    with distributed_lock.acquire_mysql_lock("lock_d", timeout=5):
        pass

    # 找到 GET_LOCK 调用
    get_lock_call = next(
        c for c in cursor.execute.call_args_list if "GET_LOCK" in c.args[0]
    )
    # args 是 (sql, params) 形式
    assert get_lock_call.args[1] == ("lock_d", 5)


def test_acquire_lock_releases_on_exception(monkeypatch):
    """with 块内抛异常时仍应 RELEASE_LOCK（finally 保护）。"""
    fake, cursor = _make_fake_conn({"got": 1})
    monkeypatch.setattr(distributed_lock, "mysql_conn", fake)

    with pytest.raises(RuntimeError, match="user code error"):
        with distributed_lock.acquire_mysql_lock("lock_e"):
            raise RuntimeError("user code error")

    # 即便用户代码抛错，RELEASE_LOCK 仍执行
    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("RELEASE_LOCK" in s for s in sqls)


def test_acquire_lock_handles_get_lock_exception(monkeypatch):
    """GET_LOCK 自身抛异常 → yield False（不让上层崩）。"""
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = lambda s, *a: None
    cursor.execute.side_effect = RuntimeError("connection lost")

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = lambda s, *a: None

    cm = MagicMock()
    cm.__enter__ = lambda s: conn
    cm.__exit__ = lambda s, *a: None
    fake = MagicMock(return_value=cm)
    monkeypatch.setattr(distributed_lock, "mysql_conn", fake)

    with distributed_lock.acquire_mysql_lock("lock_f") as got:
        assert got is False
