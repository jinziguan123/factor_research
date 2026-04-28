"""数据源可用性探测：用最小代价验证 akshare / baostock / MySQL / ClickHouse 通否。

设计要点：
- 每个探测函数自封装异常 → 返回统一 ``(ok, message, latency_ms)`` 三元组，
  路由层只做收集 + 聚合，不再 try/except；
- **串行**执行（4 个源 × 不超过几秒，串行简单可靠；并发用线程池意义不大且
  baostock login 拿全局 session，不并发友好）；
- 探测动作全部"轻量但真实"：
  - akshare 用 ``stock_info_a_code_name``（拉一次代码列表，比 spot 轻），不抓数据
    内容只看接口返回非空；
  - baostock 用 ``baostock_session`` 上下文 + 一次 ``query_trade_dates`` 当日；
  - MySQL / ClickHouse 用 ``SELECT 1``。

不做的事：
- 不写库；
- 不缓存（用户点一次就跑一次，别误把"以前能用"当成"现在能用"）；
- 不并发（详上）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------- 工具 ----------------------------


def _timed(fn) -> tuple[bool, str, int]:
    """执行 fn 并捕获异常；返回 ``(ok, message, latency_ms)``。

    fn 必须返回字符串作为 ok 时的 message（如"5217 codes returned"）；抛异常
    时把 ``str(e)`` 作为 error message。
    """
    t0 = time.perf_counter()
    try:
        msg = fn()
    except Exception as e:  # noqa: BLE001 - 探测层兜底所有错
        latency = int((time.perf_counter() - t0) * 1000)
        return False, f"{type(e).__name__}: {e}", latency
    latency = int((time.perf_counter() - t0) * 1000)
    return True, str(msg), latency


# ---------------------------- 各数据源探测 ----------------------------


def probe_akshare() -> tuple[bool, str, int]:
    """akshare 基础接口：拉一次 ``stock_info_a_code_name``（A 股代码列表）。

    走的是相对稳定的代码字典接口，不是行情接口。**这一项 ok 不代表
    akshare-spot 也 ok**——push2 行情后端偶发 RST，是独立的故障域。
    """
    def _do() -> str:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_info_a_code_name()
        if df is None or len(df) == 0:
            raise RuntimeError("returned empty DataFrame")
        return f"{len(df)} A 股代码"

    return _timed(_do)


def probe_akshare_spot() -> tuple[bool, str, int]:
    """akshare 行情接口（**实盘信号 spot 实际走这个**）：``stock_zh_a_spot``（新浪源）。

    与 ``probe_akshare`` 的 ``stock_info_a_code_name`` 是两个独立后端，任一
    挂了另一个不受影响。和实盘 spot 走同一接口，结果直接代表实盘可用性。

    新浪 spot 是分页接口，5K+ 票要拉 ~70 页，本探测耗时通常 10-30s。比东财
    push2 的 1-3s 慢，但稳定性显著更高（旧 spot_em 易被限流 RST）。
    """
    def _do() -> str:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_zh_a_spot()
        if df is None or len(df) == 0:
            raise RuntimeError("returned empty DataFrame")
        return f"sina spot 拉到 {len(df)} 行"

    return _timed(_do)


def probe_baostock() -> tuple[bool, str, int]:
    """baostock：login + 1 天 ``query_trade_dates`` + logout。"""
    def _do() -> str:
        from datetime import date

        from backend.adapters.baostock.client import baostock_session

        today = date.today().isoformat()
        with baostock_session():
            import baostock as bs  # noqa: PLC0415

            rs = bs.query_trade_dates(start_date=today, end_date=today)
            if rs.error_code != "0":
                raise RuntimeError(
                    f"query_trade_dates code={rs.error_code} msg={rs.error_msg}"
                )
            # 把游标耗完一次确保 RPC 真的回来
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        return f"trade_dates 接口返回 {len(rows)} 行"

    return _timed(_do)


def probe_mysql() -> tuple[bool, str, int]:
    """MySQL：``SELECT 1``。"""
    def _do() -> str:
        from backend.storage.mysql_client import mysql_conn

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                row = cur.fetchone()
        if not row or row.get("ok") != 1:
            raise RuntimeError(f"unexpected SELECT 1 result: {row!r}")
        return "SELECT 1 ok"

    return _timed(_do)


def probe_clickhouse() -> tuple[bool, str, int]:
    """ClickHouse：``SELECT 1``。"""
    def _do() -> str:
        from backend.storage.clickhouse_client import ch_client

        with ch_client() as cli:
            rows = cli.execute("SELECT 1")
        if not rows or rows[0][0] != 1:
            raise RuntimeError(f"unexpected SELECT 1 result: {rows!r}")
        return "SELECT 1 ok"

    return _timed(_do)


# ---------------------------- 聚合 ----------------------------


# (name, callable) 顺序固定，前端展示按此顺序
_PROBES: list[tuple[str, Any]] = [
    ("akshare", probe_akshare),
    ("akshare-spot", probe_akshare_spot),
    ("baostock", probe_baostock),
    ("mysql", probe_mysql),
    ("clickhouse", probe_clickhouse),
]


def probe_all() -> list[dict]:
    """串行跑所有探测，返回前端友好的列表（每条含 name/status/latency_ms/message/tested_at）。

    任一项失败不会终止后续探测。
    """
    out: list[dict] = []
    for name, fn in _PROBES:
        log.info("probing datasource: %s", name)
        ok_, msg, latency_ms = fn()
        out.append({
            "name": name,
            "status": "ok" if ok_ else "error",
            "latency_ms": latency_ms,
            "message": msg,
            "tested_at": datetime.now().isoformat(timespec="seconds"),
        })
        log.info(
            "probe %s: %s (%dms) — %s",
            name, "ok" if ok_ else "ERROR", latency_ms, msg,
        )
    return out
