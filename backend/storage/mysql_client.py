"""MySQL 客户端封装。

只做两件事：
1. 依据 ``backend.config.settings`` 构造 ``pymysql.Connection``；
2. 以上下文管理器（``mysql_conn``）形式暴露，保证连接自动 ``close``。

约定：
- ``autocommit=False``：所有写入需要调用方显式 ``commit()``，避免部分失败时脏写；
- 默认使用 ``DictCursor``，使 ``fetchone()`` / ``fetchall()`` 返回 dict，
  便于字段重命名 / 新增时代码保持稳健。
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from backend.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def get_connection() -> Connection:
    """构造一个原生 pymysql 连接。"""
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=int(settings.mysql_connect_timeout_s),
        read_timeout=int(settings.mysql_read_timeout_s),
        write_timeout=int(settings.mysql_read_timeout_s),
    )


@contextmanager
def mysql_conn() -> Iterator[Connection]:
    """MySQL 连接上下文管理器，离开 ``with`` 块时自动 ``close``。"""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _is_disconnect_error(exc: pymysql.err.OperationalError) -> bool:
    """判断是否为连接断开类错误（可重试）。"""
    code = exc.args[0] if exc.args else 0
    # 2006: MySQL server has gone away
    # 2013: Lost connection to MySQL server during query
    # 2014: Commands out of sync
    return code in (2006, 2013)


def execute_with_retry(
    func: Callable[[Connection], T],
    max_retries: int = 3,
) -> T:
    """执行 ``func(conn)``，遇到连接断开自动重试。

    适用于需要 MySQL 查询但不方便用 ``with mysql_conn()`` 的场景，
    或者需要对瞬时断连做容错的场景。

    用法::

        def query(c):
            with c.cursor() as cur:
                cur.execute("SELECT ...")
                return cur.fetchall()

        rows = execute_with_retry(query)
    """
    for attempt in range(1, max_retries + 1):
        try:
            with mysql_conn() as c:
                return func(c)
        except pymysql.err.OperationalError as e:
            if _is_disconnect_error(e) and attempt < max_retries:
                wait = attempt * 0.5
                logger.warning(
                    "MySQL 连接断开，第 %d/%d 次重试（%.1fs 后）",
                    attempt, max_retries, wait,
                )
                time.sleep(wait)
            else:
                raise
    # unreachable, but keeps type checker happy
    raise RuntimeError("execute_with_retry: exhausted retries")
