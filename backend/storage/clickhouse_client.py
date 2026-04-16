"""ClickHouse 客户端封装。

只做两件事：
1. 依据 ``backend.config.settings`` 构造 ``clickhouse_driver.Client`` 实例；
2. 以上下文管理器（``ch_client``）形式暴露，确保调用方不必关心 ``disconnect``。

默认开启 ``use_numpy`` 设置，后续批量插入 / 读取时 pandas ↔ ClickHouse 转换
可以直接用 numpy 数组，减少拷贝；但注意：该参数要求目标环境安装 numpy。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from clickhouse_driver import Client

from backend.config import settings


def get_client() -> Client:
    """构造一个 ClickHouse ``Client`` 实例。

    调用方可直接使用，但更推荐通过 :func:`ch_client` 上下文管理器获取，
    以保证连接被正确释放。
    """
    return Client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        settings={"use_numpy": True},
    )


@contextmanager
def ch_client() -> Iterator[Client]:
    """ClickHouse 客户端上下文管理器，离开 ``with`` 块时自动断开连接。"""
    cli = get_client()
    try:
        yield cli
    finally:
        cli.disconnect()
