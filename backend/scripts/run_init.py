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

import pymysql
from clickhouse_driver import Client

from backend.config import settings

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

    - ``mysql_host`` 和 ``clickhouse_host`` 必须都在 ``_SAFE_HOSTS`` 白名单里；
    - 否则除非显式设置 ``FR_ALLOW_PRODUCTION_INIT=1``，立即 ``sys.exit(1)``。

    设计动机：Task 1 code review 明确指出，开发者若误把生产 IP 写进 .env 跑
    ``run_init``，脚本会真的在生产库执行 DDL。幂等 DDL 虽然不会破坏数据，
    但依然是一次不期而至的变更事件，且容易掩盖误配置。
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
    print("=" * 70, file=sys.stderr)
    print(
        "[run_init] 拒绝执行：检测到可能指向生产库的 host：",
        file=sys.stderr,
    )
    for label, host in risky:
        print(f"  - {label}: {host}", file=sys.stderr)
    print(
        "\n白名单 host: " + ", ".join(sorted(_SAFE_HOSTS)),
        file=sys.stderr,
    )
    print(
        f"\n如确需在生产库执行，请显式设置环境变量：{_PRODUCTION_OPT_IN_ENV}=1 再重试。",
        file=sys.stderr,
    )
    print("=" * 70, file=sys.stderr)
    sys.exit(1)


def _run_mysql() -> None:
    """对 MySQL 逐句执行 ``init_mysql.sql``。"""
    sql_text = (SCRIPTS_DIR / "init_mysql.sql").read_text(encoding="utf-8")
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            for stmt in _split_sql(sql_text):
                if stmt.strip():
                    cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _run_clickhouse() -> None:
    """对 ClickHouse 逐句执行 ``init_clickhouse.sql``。

    注意：``CREATE DATABASE`` 必须先执行，之后的 ``CREATE TABLE`` 才能走
    全限定名 ``quant_data.xxx``。我们用一个全局 Client（不指定 database），
    让每条语句都显式带库名。
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
    _safety_check()
    print("Initializing MySQL ...")
    _run_mysql()
    print("Initializing ClickHouse ...")
    _run_clickhouse()
    print("Done.")


if __name__ == "__main__":
    main()
