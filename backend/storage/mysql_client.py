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

from contextlib import contextmanager
from typing import Iterator

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from backend.config import settings


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
    )


@contextmanager
def mysql_conn() -> Iterator[Connection]:
    """MySQL 连接上下文管理器，离开 ``with`` 块时自动 ``close``。"""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
