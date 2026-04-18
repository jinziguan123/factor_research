"""导入任务的状态/元数据读写（``stock_symbol`` + ``stock_bar_import_job``）。

与 timing_driven 共享同一套表：
- ``stock_symbol``：需要时 upsert（新股票自动落一条），返回 symbol_id。
- ``stock_bar_import_job``：每次 ``run_import`` 创建一条，完成后更新状态。
  前端 ``/api/admin/jobs`` 已经读这张表——因此 factor_research 的任务自动会出现
  在 admin 页的"最近任务"列表里，与 timing_driven 的任务共用同一个视图。

**刻意不做** ``stock_bar_1m_import_state``：该表在 factor_research 的测试/生产
mysql-init 中没有建过，作为研究端，我们用「ClickHouse MAX(trade_date)」代替
「state 表 last_bar_trade_date」做增量起点，减少一张维护表。

注意：下面的函数都接受外部传入的 pymysql 连接（``conn``），不做 connect/close；
调用方用 ``backend.storage.mysql_client.mysql_conn()`` 统一管理事务与连接生命周期。
"""
from __future__ import annotations

# 任务类型（与 timing_driven 的枚举对齐）：1=全量导入；2=增量同步。
JOB_TYPE_FULL = 1
JOB_TYPE_INCREMENTAL = 2

# 任务状态：1=running；2=success；3=partial_success（有失败但继续跑完）；4=failed。
JOB_STATUS_RUNNING = 1
JOB_STATUS_SUCCESS = 2
JOB_STATUS_PARTIAL = 3
JOB_STATUS_FAILED = 4

# A 股市场代码 → stock_symbol.market tinyint 值（与 timing_driven 一致）。
_MARKET_CODE_MAP = {"SH": 1, "SZ": 2, "BJ": 3}


def _decode_market(symbol: str) -> tuple[str, int, str]:
    """把 ``000001.SZ`` 解析成 ``(code, market_id, normalized)``。"""
    normalized = symbol.strip().upper()
    if "." not in normalized:
        raise ValueError(f"symbol must look like '000001.SZ', got {symbol!r}")
    code, market = normalized.split(".", 1)
    if market not in _MARKET_CODE_MAP:
        raise ValueError(f"unsupported market: {market} (symbol={symbol})")
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"invalid code: {code} (symbol={symbol})")
    return code, _MARKET_CODE_MAP[market], normalized


def upsert_symbol(
    conn,
    symbol: str,
    *,
    name: str | None = None,
    dat_path: str | None = None,
    is_active: int = 1,
) -> int:
    """按 symbol 写入 ``stock_symbol`` 并回取 symbol_id。

    使用 ``ON DUPLICATE KEY UPDATE symbol_id = LAST_INSERT_ID(symbol_id)`` 的经典
    惯用法，保证无论走 INSERT 分支还是 UPDATE 分支，``cursor.lastrowid`` 都能拿到
    稳定的 symbol_id。上层对同一只股票反复调用是幂等的。
    """
    code, market, normalized = _decode_market(symbol)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stock_symbol (symbol, code, market, name, dat_path, is_active)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                symbol_id = LAST_INSERT_ID(symbol_id),
                code = VALUES(code),
                market = VALUES(market),
                name = COALESCE(VALUES(name), name),
                dat_path = COALESCE(VALUES(dat_path), dat_path),
                is_active = VALUES(is_active)
            """,
            (normalized, code, market, name, dat_path, is_active),
        )
        symbol_id = int(cur.lastrowid or 0)
    if symbol_id <= 0:
        raise RuntimeError(f"upsert_symbol failed to return id: {normalized}")
    return symbol_id


def create_job(
    conn,
    *,
    job_type: int,
    symbol_count: int,
    note: str | None = None,
) -> int:
    """新建一条 ``stock_bar_import_job``，初始 status=running。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stock_bar_import_job (job_type, status, symbol_count, note)
            VALUES (%s, %s, %s, %s)
            """,
            (int(job_type), JOB_STATUS_RUNNING, int(symbol_count), note),
        )
        job_id = int(cur.lastrowid)
    if job_id <= 0:
        raise RuntimeError("create_job failed to return id")
    return job_id


def update_job(conn, job_id: int, **fields) -> None:
    """更新指定 job 的任意字段；常用字段见 ``stock_bar_import_job`` 表结构。

    使用拼接 SQL：key 是我们代码内枚举的字段名，不是用户输入，不存在注入风险。
    """
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [int(job_id)]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE stock_bar_import_job SET {set_clause} WHERE job_id = %s",
            tuple(values),
        )
