"""初始化 MySQL 和 ClickHouse schema。

- 幂等：所有 DDL 使用 ``CREATE ... IF NOT EXISTS``，可重复执行。
- 安全：``_safety_check`` 阻止误连生产库；只有设置 ``FR_ALLOW_PRODUCTION_INIT=1``
  才允许对非白名单 host（例如 ``172.30.26.12``）执行 DDL。
- 运行方式::

      cd backend && uv run python -m scripts.run_init

  或::

      uv run python -m backend.scripts.run_init
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 `python backend/scripts/run_init.py` 从项目根直接跑时也能找到 backend 包
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from clickhouse_driver import Client

from backend.config import settings
from backend.storage.mysql_client import mysql_conn

# 脚本自身所在目录；SQL 资源文件与本模块同目录。
SCRIPTS_DIR = Path(__file__).resolve().parent

# 本地/容器内部署时允许直接放行的 host；其它 host 视为生产，须显式 opt-in。
_SAFE_HOSTS: frozenset[str] = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        # docker-compose service 名常见别名
        "mysql",
        "clickhouse",
        "my-mysql",
        "quant-clickhouse",
    }
)

# 放行生产库 DDL 的环境变量开关（设置为 "1" 时跳过 host 白名单检查）。
_PRODUCTION_OPT_IN_ENV = "FR_ALLOW_PRODUCTION_INIT"


def _split_sql(sql: str) -> list[str]:
    """按 ``;`` 拆分 SQL，同时忽略引号内的分号。

    为什么需要自己拆：
    - ``pymysql`` 的 ``cursor.execute`` 不保证对多语句 SQL 的行为一致；
    - ``clickhouse-driver`` 的 ``Client.execute`` 必须一次一个语句。

    规则（足够支撑本项目的 DDL 文件）：
    - 遇到未转义的 ``'``、``"``、反引号 会进入/退出字符串模式；
    - 字符串模式下的 ``;`` 被视作普通字符；
    - 行注释 ``--`` 到行尾、块注释 ``/* ... */`` 内的内容也不触发拆分。

    返回保留原始空白的片段列表（调用方自行 strip + 过滤空串）。
    """
    stmts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    quote: str | None = None  # 当前进入的引号字符（None 表示不在字符串里）
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # 行内注释：从 -- 到换行结束
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        # 块注释：/* ... */
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue

        # 字符串内部：只看对应结束引号；支持反斜杠转义。
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                # 保留转义字符本身（在字符串字面量里）
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        # 不在注释也不在字符串，尝试识别注释开头 / 引号 / 分号
        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    # 残余内容（例如最后一句没以 ; 结尾）
    if buf:
        stmts.append("".join(buf))
    return stmts


def _safety_check() -> None:
    """阻止在未确认的生产库上执行 DDL。

    **只服务于 run_init.py**（DDL / CREATE TABLE 路径）。数据维护类任务
    （aggregate_bar_1d / 各 importers）不应调用本函数——写业务数据本身是合法用途，
    再走一遍 host 白名单等于把用户正常操作也挡住。

    - ``mysql_host`` 和 ``clickhouse_host`` 必须都在 ``_SAFE_HOSTS`` 白名单里；
    - 否则除非显式设置 ``FR_ALLOW_PRODUCTION_INIT=1``，抛 ``RuntimeError``。
      历史上这里是 ``sys.exit(1)``，但若被误调在 ASGI BackgroundTask 线程里会
      让 response hook 直接崩栈；改抛异常由上层捕获，更稳。
    """
    risky = []
    if settings.mysql_host not in _SAFE_HOSTS:
        risky.append(("MySQL", settings.mysql_host))
    if settings.clickhouse_host not in _SAFE_HOSTS:
        risky.append(("ClickHouse", settings.clickhouse_host))

    if not risky:
        return

    if os.environ.get(_PRODUCTION_OPT_IN_ENV) == "1":
        # 运维确实想在生产跑，留下显眼日志即可
        for label, host in risky:
            print(
                f"[run_init] WARNING: {label} host={host} 不在本地白名单，"
                f"但已设置 {_PRODUCTION_OPT_IN_ENV}=1，允许继续。",
                file=sys.stderr,
            )
        return

    # 默认：拒绝执行
    lines = ["检测到可能指向生产库的 host："]
    for label, host in risky:
        lines.append(f"  - {label}: {host}")
    lines.append("白名单 host: " + ", ".join(sorted(_SAFE_HOSTS)))
    lines.append(
        f"如确需在生产库执行，请显式设置环境变量：{_PRODUCTION_OPT_IN_ENV}=1 再重试。"
    )
    msg = "\n".join(lines)
    # stderr 打印给 CLI 用户看；同时抛异常让 import 它的上层能捕获。
    print("=" * 70, file=sys.stderr)
    print("[run_init] 拒绝执行：" + msg, file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    raise RuntimeError(msg)


def _run_mysql() -> None:
    """对 MySQL 逐句执行 ``init_mysql.sql``。

    用 ``storage.mysql_client.mysql_conn``（autocommit=False, DictCursor）
    确保连接参数与运行时一致；DDL 不关心 cursor 类型。
    """
    sql_text = (SCRIPTS_DIR / "init_mysql.sql").read_text(encoding="utf-8")
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql(sql_text):
                if stmt.strip():
                    cur.execute(stmt)
        conn.commit()


def _run_clickhouse() -> None:
    """对 ClickHouse 逐句执行 ``init_clickhouse.sql``。

    注意：``CREATE DATABASE`` 必须先执行，之后的 ``CREATE TABLE`` 才能走
    全限定名 ``quant_data.xxx``。这里**不能**复用 ``storage.clickhouse_client.ch_client``，
    因为它连接时就要求 ``database=quant_data`` 已经存在；run_init 的职责就是
    创建这个库。本脚本保留裸 ``Client`` 并省略 ``database`` 参数。
    """
    sql_text = (SCRIPTS_DIR / "init_clickhouse.sql").read_text(encoding="utf-8")
    ch = Client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    try:
        for stmt in _split_sql(sql_text):
            if stmt.strip():
                ch.execute(stmt)
    finally:
        ch.disconnect()


def main() -> None:
    # CLI 场景：把 _safety_check 抛出的 RuntimeError 转成干净的 exit(1)，避免
    # 用户直接看到 Python traceback（stderr 里的拦截原因已经在 _safety_check 打印过）。
    try:
        _safety_check()
    except RuntimeError:
        sys.exit(1)
    print("Initializing MySQL ...")
    _run_mysql()
    print("Initializing ClickHouse ...")
    _run_clickhouse()
    print("Done.")


if __name__ == "__main__":
    main()
