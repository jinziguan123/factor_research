"""跨实例分布式锁（基于 MySQL ``GET_LOCK`` / ``RELEASE_LOCK``）。

用途：当后端 / worker 在多台设备共享同一数据库时，防止"同一时刻多个实例
同时执行某个临界区"。典型场景：

1. **live_market worker leader 选举**：每个 tick 只让一个实例处理订阅刷新，
   其它实例 skip 本轮。leader 实例挂了下一 tick 自动接管。
2. **订阅创建串行化**：两台设备同时 POST 相同配置，应用层 SELECT-then-INSERT
   会产生竞态；用 hash 锁串行化 → 第一个写完，第二个看到再返回已存在。

设计要点：
- ``GET_LOCK(name, timeout)`` 是 MySQL 的会话级 advisory lock，会话断开自动释放
  （不需要担心 leader 实例异常退出后的死锁）；
- ``timeout=0``：立即返回（不等待）；用于 worker leader（拿不到就 skip）；
- ``timeout>0``：阻塞等待至多 N 秒；用于创建串行化（5s 通常足够）；
- 持锁与释放必须用 **同一连接**（GET_LOCK 是会话锁），所以本模块在 with 块
  内自己持有 ``mysql_conn``，调用方不必担心连接管理。
- ``yield bool``：True=拿到，False=没拿到/超时；调用方据此决定是否进入临界区。

注意：
- MySQL 5.7+ 支持单会话同时持有多个不同名称的锁。
- ``RELEASE_LOCK`` 失败一般是连接已断（锁会被服务端自动释放），可以忽略。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


@contextmanager
def acquire_mysql_lock(name: str, timeout: int = 0) -> Iterator[bool]:
    """尝试获取一把跨实例 MySQL advisory lock。

    Args:
        name: 锁名称（建议带前缀，如 ``"live_market_leader"`` 或
            ``"sub_create:<hash>"``）。
        timeout: 等待秒数；0=立即返回（拿不到即 False）。

    Yields:
        ``True``  — 已拿到锁，临界区独占；
        ``False`` — 未拿到（超时 / 被其它实例持有）；调用方应跳过临界区。

    退出 with 块时自动释放锁；连接异常时 MySQL 服务端会因会话断开自动释放，
    不会留下死锁。
    """
    with mysql_conn() as c:
        try:
            with c.cursor() as cur:
                cur.execute("SELECT GET_LOCK(%s, %s) AS got", (name, timeout))
                row = cur.fetchone()
        except Exception:
            log.exception("GET_LOCK(%s) 抛异常；视为未拿到", name)
            yield False
            return

        # GET_LOCK 返回 1=拿到 0=超时 NULL=错误（参数非法 / OOM）
        got = bool(row and row.get("got") == 1)
        try:
            yield got
        finally:
            if got:
                try:
                    with c.cursor() as cur:
                        cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
                except Exception:  # noqa: BLE001
                    # 连接已断 / RELEASE 失败：MySQL 服务端会自动清理，不抛
                    log.debug("RELEASE_LOCK(%s) 失败（通常是连接已断）", name, exc_info=True)
