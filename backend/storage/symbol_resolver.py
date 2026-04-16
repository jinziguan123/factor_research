"""symbol ↔ symbol_id 互转，带进程内 LRU 缓存。

设计要点：
- ``stock_symbol`` 由生产链路（timing_driven）维护，本模块**只读**，不做插入；
- ``@lru_cache`` 绑定在**方法**上，``self`` 参与 hash key，因此缓存按实例隔离；
  实践中同一进程内 ``DataService`` 只持有一个 ``SymbolResolver``，缓存命中率依旧
  充足；若未来有频繁创建短生命周期 resolver 的场景，再重构为模块级缓存即可；
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

        实现说明：
        - 走一次批量 ``SELECT ... WHERE symbol IN (...)``，避免逐个走
          ``resolve_symbol_id`` 导致的 N 次单点查询（即使命中 LRU，冷启动仍会放大 IO）；
        - 不复用 ``resolve_symbol_id`` 的 ``lru_cache``，后者留给真正的单值场景；
        - 规范化采用 ``strip().upper()``，与 ``resolve_symbol_id`` 一致；
        - 返回 ``{原始 symbol: symbol_id}``：key 保留调用方原始写法，便于回查。
        """
        if not symbols:
            return {}
        normalized = {s: s.strip().upper() for s in symbols}
        norm_values = list(set(normalized.values()))
        placeholders = ",".join(["%s"] * len(norm_values))
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    f"SELECT symbol, symbol_id FROM stock_symbol WHERE symbol IN ({placeholders})",
                    norm_values,
                )
                rows = cur.fetchall()
        sym_to_id = {r["symbol"]: int(r["symbol_id"]) for r in rows}
        return {
            orig: sym_to_id[norm]
            for orig, norm in normalized.items()
            if norm in sym_to_id
        }
