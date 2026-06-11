"""MySQL 客户端封装 + 连接池。

职责：
1. 依据 ``backend.config.settings`` 构造 ``pymysql.Connection``；
2. 维护一个线程安全的连接池，``mysql_conn()`` 从池中借出 / 归还；
3. 借出前 ``ping()`` 探活，死连接自动丢弃并新建。

约定：
- ``autocommit=False``：所有写入需要调用方显式 ``commit()``，避免部分失败时脏写；
- 默认使用 ``DictCursor``，使 ``fetchone()`` / ``fetchall()`` 返回 dict，
  便于字段重命名 / 新增时代码保持稳健。
- ``mysql_conn()`` 签名不变，150+ 处调用零改动。
"""
from __future__ import annotations

import logging
import queue
import threading
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


# ---------------------------------------------------------------------------
# 连接池
# ---------------------------------------------------------------------------

class MySQLPool:
    """线程安全的 MySQL 连接池。

    - ``borrow()``：借出一个连接（自动探活，死连接丢弃重建）；
    - ``release(conn)``：归还连接到池（先 rollback 清理事务）；
    - ``discard(conn)``：丢弃死连接（异常路径用）。
    """

    def __init__(self, min_size: int = 5, max_size: int = 10) -> None:
        self._min = min_size
        self._max = max_size
        # 空闲连接队列
        self._idle: queue.Queue[Connection] = queue.Queue(maxsize=max_size)
        # 当前已创建的连接总数（空闲 + 借出）
        self._total = 0
        self._lock = threading.Lock()
        # 预热：创建 min_size 个连接
        for _ in range(min_size):
            conn = self._new_conn()
            if conn is not None:
                self._idle.put(conn)

    def _new_conn(self) -> Connection | None:
        """新建连接，不超过上限返回 ``None``。"""
        with self._lock:
            if self._total >= self._max:
                return None
            self._total += 1
        try:
            return get_connection()
        except Exception:
            with self._lock:
                self._total -= 1
            raise

    def _validate(self, conn: Connection) -> bool:
        """验证连接是否可用。"""
        try:
            conn.ping(reconnect=False)
            return True
        except Exception:
            return False

    def borrow(self, timeout: float = 10.0) -> Connection:
        """借出一个可用连接。

        优先从空闲队列取；队列空则新建（不超过上限）；
        上限已满则阻塞等待（最多 ``timeout`` 秒）。
        """
        deadline = time.monotonic() + timeout

        while True:
            # 1. 尝试从空闲队列取
            try:
                conn = self._idle.get_nowait()
            except queue.Empty:
                # 队列空，尝试新建
                conn = self._new_conn()
                if conn is not None:
                    return conn
                # 上限已满，阻塞等待归还
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"MySQL 连接池已满（{self._max}），等待 {timeout}s 超时"
                    )
                try:
                    conn = self._idle.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    continue  # 再试一轮
            else:
                # 2. 验证连接
                if self._validate(conn):
                    return conn
                # 死连接：丢弃，计数 -1，继续取下一个
                self._close_one(conn)
                continue

    def release(self, conn: Connection) -> None:
        """归还连接到池。

        归还前 ``rollback()`` 清掉调用方可能遗留的未提交事务（autocommit=False），
        避免下一个借用者继承到脏事务状态。``rollback`` 本身也是一次轻量探活——
        连接已坏会抛异常，落到 except 分支直接丢弃，省去额外 ``ping`` 往返。
        """
        try:
            conn.rollback()
        except Exception:
            self._close_one(conn)
            return
        try:
            self._idle.put_nowait(conn)
        except queue.Full:
            self._close_one(conn)

    def discard(self, conn: Connection) -> None:
        """丢弃连接（异常路径：连接可能已坏）。"""
        self._close_one(conn)

    def _close_one(self, conn: Connection) -> None:
        """关闭一个连接并更新计数。"""
        with self._lock:
            self._total = max(0, self._total - 1)
        try:
            conn.close()
        except Exception:
            pass

    def close_all(self) -> None:
        """关闭所有空闲连接（进程退出时调用）。"""
        while True:
            try:
                conn = self._idle.get_nowait()
                try:
                    conn.close()
                except Exception:
                    pass
            except queue.Empty:
                break
        with self._lock:
            self._total = 0

    @property
    def status(self) -> dict[str, int]:
        """当前池状态（调试用）。"""
        return {
            "idle": self._idle.qsize(),
            "total": self._total,
            "min": self._min,
            "max": self._max,
        }


# 模块级单例
_pool: MySQLPool | None = None


def get_pool() -> MySQLPool:
    """获取/懒初始化连接池单例。"""
    global _pool
    if _pool is None:
        _pool = MySQLPool(
            min_size=settings.mysql_pool_size,
            max_size=settings.mysql_pool_max,
        )
        logger.info(
            "MySQL 连接池已初始化 (min=%d, max=%d)",
            settings.mysql_pool_size,
            settings.mysql_pool_max,
        )
    return _pool


# ---------------------------------------------------------------------------
# 对外接口（签名不变，150+ 处调用零改动）
# ---------------------------------------------------------------------------

@contextmanager
def mysql_conn() -> Iterator[Connection]:
    """MySQL 连接上下文管理器：从池中借出，用完归还。

    正常退出 → 归还；异常退出 → 丢弃（连接可能已坏）。
    """
    pool = get_pool()
    conn = pool.borrow()
    try:
        yield conn
    except Exception:
        pool.discard(conn)
        raise
    else:
        pool.release(conn)


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
