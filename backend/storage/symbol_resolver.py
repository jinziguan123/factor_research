"""symbol ↔ symbol_id 互转，带进程内 LRU 缓存。

设计要点：
- ``stock_symbol`` 由生产链路（timing_driven）维护，本模块**只读**，不做插入；
- ``@lru_cache`` 绑定在方法上，同一进程内多 ``SymbolResolver`` 实例共享缓存，
  这里故意保留——因为 symbol_id 是稳定不变的业务主键，多实例共享缓存不会
  产生不一致；
- symbol 统一 ``strip().upper()`` 后查询，避免调用方传入小写/带空格时查不到。
"""
from __future__ import annotations

from functools import lru_cache

from backend.storage.mysql_client import mysql_conn


class SymbolResolver:
    """symbol（如 ``000001.SZ``）与 symbol_id（整型主键）的互转工具。"""

    @lru_cache(maxsize=8192)
    def resolve_symbol_id(self, symbol: str) -> int | None:
        """symbol → symbol_id；未知 symbol 返回 ``None``。"""
        normalized = symbol.strip().upper()
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT symbol_id FROM stock_symbol WHERE symbol=%s",
                    (normalized,),
                )
                row = cur.fetchone()
        return int(row["symbol_id"]) if row else None

    @lru_cache(maxsize=8192)
    def resolve_symbol(self, symbol_id: int) -> str | None:
        """symbol_id → symbol；未知 id 返回 ``None``。"""
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT symbol FROM stock_symbol WHERE symbol_id=%s",
                    (symbol_id,),
                )
                row = cur.fetchone()
        return row["symbol"] if row else None

    def resolve_many(self, symbols: list[str]) -> dict[str, int]:
        """批量 resolve；**未知 symbol 会被过滤掉**，而不是返回 ``None``。

        返回 ``{原始 symbol: symbol_id}`` 映射。注意 key 保留调用方传入的
        原始写法（不 strip/upper），便于后续按用户输入回查；值统一为 int。
        """
        out: dict[str, int] = {}
        for s in symbols:
            sid = self.resolve_symbol_id(s)
            if sid is not None:
                out[s] = sid
        return out
