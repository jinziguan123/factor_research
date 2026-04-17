"""股票池 CRUD + 批量导入。

关键约束：
- 所有查询 / 更新都用 ``owner_key=settings.owner_key`` 过滤；
  ``stock_pool`` 是生产 + factor_research 共用表，必须做 owner 隔离，
  否则有可能误改 timing_driven 维护的池。
- ``soft delete``：``DELETE /api/pools/{pid}`` 只置 ``is_active=0``，保留历史记录；
  ``list_pools`` 过滤 ``is_active=1``。
- ``POST /api/pools/{pid}:import``：支持换行 / 空格 / 逗号 / 分号分隔；
  未知 symbol 静默跳过。
- 写入路径性能：``create / update / import`` 统一走"批量 resolve + 分块多值 INSERT"。
  原先每个 symbol 走一次 ``resolve_symbol_id`` 再单行 INSERT，"全量添加"~5000
  只股票时会产生 ~10000 次网络往返，在机房外部网络下可直接撑爆 FastAPI 默认
  超时（观察到 60s+ 超时）。改成批量化后是 1 次 SELECT IN + 若干次多值 INSERT。
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

# 批量 INSERT 每块的 symbol 数上限。单条 SQL 过长会撞 ``max_allowed_packet``
# （默认 16MB）和 server 端 parser 内存。1000 行 * 每行 ~40 字节 ≈ 40KB，
# 对 5000 只 A 股全量添加也只需 5 次 INSERT，整体 100ms 级。
_INSERT_BATCH_SIZE = 1000


def _bulk_insert_pool_symbols(
    cur, pool_id: int, symbols: list[str], base_sort_order: int = 0
) -> int:
    """把 symbols 批量写进 ``stock_pool_symbol``，返回实际 INSERT 的行数。

    Args:
        cur: 已打开的游标（由调用方负责 commit）。
        pool_id: 目标池 id。
        symbols: 原始 symbol 列表，顺序决定 ``sort_order``（起点由 base 指定）。
        base_sort_order: ``sort_order`` 起点，``import_symbols`` 场景要接在已有
            记录后面，传 ``MAX(sort_order)+1``；``create/update`` 从 0 起即可。

    Returns:
        累计 INSERT 的行数（已汇总各批 ``cur.rowcount``）；重复 symbol 由
        ``INSERT IGNORE`` 吞掉，不计入。

    实现说明：
    - ``SymbolResolver().resolve_many`` 做一次 ``SELECT ... WHERE symbol IN (...)``，
      5000 个 symbol 用一次查询搞定；未知 symbol 会被过滤，无需调用方再判 None。
    - ``sort_order`` 按**原始 symbols 列表里的位置**生成，过滤未知之后保留相对顺序；
      同一 symbol 重复出现只取首次位置（避免 ``INSERT IGNORE`` 吞第二次但 sort
      已跳号导致列表出现空洞）。
    - 分块拼 ``INSERT IGNORE ... VALUES (%s,%s,%s), (%s,%s,%s), ...``，一次
      execute 多行，避开 N 次网络往返。
    """
    if not symbols:
        return 0
    resolver = SymbolResolver()
    sym_to_id = resolver.resolve_many(symbols)
    if not sym_to_id:
        return 0

    # 按原始顺序生成三元组；同一 symbol 只保留首次出现，防 sort_order 跳号。
    seen: set[str] = set()
    rows: list[tuple[int, int, int]] = []
    for i, s in enumerate(symbols):
        if s in seen:
            continue
        seen.add(s)
        sid = sym_to_id.get(s)
        if sid is None:
            continue
        rows.append((pool_id, sid, base_sort_order + i))

    inserted = 0
    for start in range(0, len(rows), _INSERT_BATCH_SIZE):
        chunk = rows[start : start + _INSERT_BATCH_SIZE]
        placeholders = ",".join(["(%s,%s,%s)"] * len(chunk))
        flat: list[int] = [v for row in chunk for v in row]
        cur.execute(
            f"INSERT IGNORE INTO stock_pool_symbol "
            f"(pool_id, symbol_id, sort_order) VALUES {placeholders}",
            flat,
        )
        inserted += cur.rowcount
    return inserted


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
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO stock_pool (owner_key, pool_name, description) "
                "VALUES (%s, %s, %s)",
                (settings.owner_key, body.name, body.description),
            )
            pid = cur.lastrowid
            _bulk_insert_pool_symbols(cur, pid, body.symbols)
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
    """全量覆盖池 meta + 成员；成员按入参顺序重排 sort_order。

    注意：body.symbols 是 ``Optional``；只改 meta 时前端不传 symbols，这里不能
    把 "未传" 和 "传了空列表 []" 混淆 —— 前者保留成员，后者清空。
    """
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
            if body.symbols is not None:
                cur.execute(
                    "DELETE FROM stock_pool_symbol WHERE pool_id=%s", (pool_id,)
                )
                _bulk_insert_pool_symbols(cur, pool_id, body.symbols)
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


@router.delete("/{pool_id}/symbols/{symbol}")
def remove_symbol(pool_id: int, symbol: str) -> dict:
    """从池里移除一只股票（增量接口）。

    设计初衷：前端 PoolEditor 里点 × 删一只股票，旧路径会走 ``PUT /api/pools/{id}``
    把**剩余所有 symbol** 全量传回来，后端 ``DELETE + bulk INSERT`` 重建整个池。
    这条路径有两个痛点：
    1. 浪费：4999 只股票的池删一只 = DELETE 5000 行 + INSERT 4999 行，
       远程 MySQL 下轻松过秒级；
    2. 并发覆盖：两个人同时删不同股票，后提交者会基于自己的旧 snapshot 覆盖对方
       的修改（last-write-wins，无感丢数据）。

    走单只 DELETE 后：一次 ``WHERE pool_id=%s AND symbol_id=%s`` 命中复合主键，
    毫秒级返回；且两次并发删除互不干扰。

    幂等：目标 symbol 本就不在池里时返回 ``removed=0``，不抛 404（避免"池里没有
    但前端 cache 旧快照里有 → 用户看到 toast 报错"的 UX 问题）。
    """
    normalized = symbol.strip().upper()
    resolver = SymbolResolver()
    sid = resolver.resolve_symbol_id(normalized)
    if sid is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol}")
    with mysql_conn() as c:
        with c.cursor() as cur:
            # 先做 owner 归属校验，避免跨 owner 误删。
            cur.execute(
                "SELECT 1 FROM stock_pool "
                "WHERE pool_id=%s AND owner_key=%s",
                (pool_id, settings.owner_key),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="pool not found")
            cur.execute(
                "DELETE FROM stock_pool_symbol "
                "WHERE pool_id=%s AND symbol_id=%s",
                (pool_id, sid),
            )
            removed = cur.rowcount
        c.commit()
    return ok({"removed": removed})


@router.post("/{pool_id}:import")
def import_symbols(pool_id: int, body: PoolImportIn) -> dict:
    """批量把 ``text`` 中的 symbol 追加进池。

    Returns:
        ``{inserted, total_input}``。``inserted`` 只计本次真正 INSERT 的行数
        （``cur.rowcount`` 汇总），重复 symbol 不算；``total_input`` 是解析后的 token 总数。
    """
    tokens = [t for t in _IMPORT_TOKEN_RE.split(body.text) if t]
    with mysql_conn() as c:
        with c.cursor() as cur:
            # 新增的 sort_order 从当前最大值 + 1 起排，避免和已有顺序冲突。
            cur.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m "
                "FROM stock_pool_symbol WHERE pool_id=%s",
                (pool_id,),
            )
            base = (cur.fetchone() or {"m": -1})["m"] + 1
            inserted = _bulk_insert_pool_symbols(cur, pool_id, tokens, base)
        c.commit()
    return ok({"inserted": inserted, "total_input": len(tokens)})
