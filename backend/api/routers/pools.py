"""股票池 CRUD + 批量导入。

关键约束：
- 所有查询 / 更新都用 ``owner_key=settings.owner_key`` 过滤；
  ``stock_pool`` 是生产 + factor_research 共用表，必须做 owner 隔离，
  否则有可能误改 timing_driven 维护的池。
- ``soft delete``：``DELETE /api/pools/{pid}`` 只置 ``is_active=0``，保留历史记录；
  ``list_pools`` 过滤 ``is_active=1``。
- ``POST /api/pools/{pid}:import``：支持换行 / 空格 / 逗号 / 分号分隔；
  未知 symbol 静默跳过（用 ``resolve_symbol_id`` 逐个查，None 即跳过）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from backend.api.schemas import PoolImportIn, PoolIn, ok
from backend.config import settings
from backend.storage.mysql_client import mysql_conn
from backend.storage.symbol_resolver import SymbolResolver

router = APIRouter(prefix="/api/pools", tags=["pools"])

# 统一的 token 分隔正则：空白 + 逗号 + 分号；多个连续分隔符合并为一次切分。
_IMPORT_TOKEN_RE = re.compile(r"[\s,;]+")


@router.get("")
def list_pools() -> dict:
    """列出本 owner 下所有 is_active 的股票池，按创建时间倒序。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT pool_id, pool_name, description, is_active, "
                "created_at, updated_at FROM stock_pool "
                "WHERE owner_key=%s AND is_active=1 "
                "ORDER BY created_at DESC, pool_id DESC",
                (settings.owner_key,),
            )
            return ok(cur.fetchall())


@router.post("")
def create_pool(body: PoolIn) -> dict:
    """建池 + 按入参顺序写入 ``stock_pool_symbol``。

    - 未知 symbol 静默跳过；
    - ``INSERT IGNORE`` 保证同 symbol 重复传入不抛 ``Duplicate entry``。
    """
    resolver = SymbolResolver()
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO stock_pool (owner_key, pool_name, description) "
                "VALUES (%s, %s, %s)",
                (settings.owner_key, body.name, body.description),
            )
            pid = cur.lastrowid
            for i, s in enumerate(body.symbols):
                sid = resolver.resolve_symbol_id(s)
                if sid is None:
                    continue
                cur.execute(
                    "INSERT IGNORE INTO stock_pool_symbol "
                    "(pool_id, symbol_id, sort_order) VALUES (%s, %s, %s)",
                    (pid, sid, i),
                )
        c.commit()
    return ok({"pool_id": pid})


@router.get("/{pool_id}")
def get_pool(pool_id: int) -> dict:
    """返回池详情 + 有序 symbol 列表。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT pool_id, pool_name, description, is_active, "
                "created_at, updated_at FROM stock_pool "
                "WHERE pool_id=%s AND owner_key=%s",
                (pool_id, settings.owner_key),
            )
            p = cur.fetchone()
            if not p:
                raise HTTPException(status_code=404, detail="pool not found")
            cur.execute(
                "SELECT b.symbol, b.name "
                "FROM stock_pool_symbol s "
                "JOIN stock_symbol b ON b.symbol_id = s.symbol_id "
                "WHERE s.pool_id=%s "
                "ORDER BY s.sort_order, s.symbol_id",
                (pool_id,),
            )
            p["symbols"] = cur.fetchall()
    return ok(p)


@router.put("/{pool_id}")
def update_pool(pool_id: int, body: PoolIn) -> dict:
    """全量覆盖池 meta + 成员；成员按入参顺序重排 sort_order。"""
    resolver = SymbolResolver()
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE stock_pool SET pool_name=%s, description=%s "
                "WHERE pool_id=%s AND owner_key=%s",
                (body.name, body.description, pool_id, settings.owner_key),
            )
            # 先确认受影响：非 owner / 不存在的 pool_id 应走 404。
            if cur.rowcount == 0:
                # 可能是 pool_id 不存在，也可能是 owner 不匹配——二者都该 404。
                # rowcount=0 也可能是 name/description 完全没变（MySQL 有时返回 0），
                # 再 SELECT 一次确认存在性。
                cur.execute(
                    "SELECT 1 FROM stock_pool "
                    "WHERE pool_id=%s AND owner_key=%s",
                    (pool_id, settings.owner_key),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="pool not found")
            cur.execute(
                "DELETE FROM stock_pool_symbol WHERE pool_id=%s", (pool_id,)
            )
            for i, s in enumerate(body.symbols):
                sid = resolver.resolve_symbol_id(s)
                if sid is None:
                    continue
                cur.execute(
                    "INSERT INTO stock_pool_symbol "
                    "(pool_id, symbol_id, sort_order) VALUES (%s, %s, %s)",
                    (pool_id, sid, i),
                )
        c.commit()
    return ok({"pool_id": pool_id})


@router.delete("/{pool_id}")
def delete_pool(pool_id: int) -> dict:
    """软删：``is_active=0``；不删 ``stock_pool_symbol`` 保留成员快照。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE stock_pool SET is_active=0 "
                "WHERE pool_id=%s AND owner_key=%s",
                (pool_id, settings.owner_key),
            )
            if cur.rowcount == 0:
                # 同 update_pool：rowcount=0 也可能是幂等二次删除（is_active 已是 0），
                # 补一次存在性查询再判。
                cur.execute(
                    "SELECT 1 FROM stock_pool "
                    "WHERE pool_id=%s AND owner_key=%s",
                    (pool_id, settings.owner_key),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="pool not found")
        c.commit()
    return ok({"pool_id": pool_id})


@router.post("/{pool_id}:import")
def import_symbols(pool_id: int, body: PoolImportIn) -> dict:
    """批量把 ``text`` 中的 symbol 追加进池。

    Returns:
        ``{inserted, total_input}``。``inserted`` 只计本次真正 INSERT 的行数
        （``cur.rowcount`` 汇总），重复 symbol 不算；``total_input`` 是解析后的 token 总数。
    """
    tokens = [t for t in _IMPORT_TOKEN_RE.split(body.text) if t]
    resolver = SymbolResolver()
    inserted = 0
    with mysql_conn() as c:
        with c.cursor() as cur:
            # 新增的 sort_order 从当前最大值 + 1 起排，避免和已有顺序冲突。
            cur.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m "
                "FROM stock_pool_symbol WHERE pool_id=%s",
                (pool_id,),
            )
            base = (cur.fetchone() or {"m": -1})["m"] + 1
            for i, s in enumerate(tokens):
                sid = resolver.resolve_symbol_id(s)
                if sid is None:
                    continue
                cur.execute(
                    "INSERT IGNORE INTO stock_pool_symbol "
                    "(pool_id, symbol_id, sort_order) VALUES (%s, %s, %s)",
                    (pool_id, sid, base + i),
                )
                inserted += cur.rowcount
        c.commit()
    return ok({"inserted": inserted, "total_input": len(tokens)})
