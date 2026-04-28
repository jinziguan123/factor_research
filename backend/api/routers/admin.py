"""运维端点：触发 bar_1d 聚合 / qfq 导入 + 查看最近任务状态。

为什么用 ``BackgroundTasks``：
- ``aggregate_bar_1d.aggregate`` 和 ``run_import`` 都是同步阻塞任务，一次可能跑几十秒
  到几分钟；HTTP 请求不能等到它返回，否则前端超时 + 后端线程卡死。
- ``BackgroundTasks`` 在 response 发完后的 hook 里执行，不会阻塞本次响应。
  返回 ``202 Accepted`` 语义上更准确，但上层 envelope ``code=0`` 已能表达"已受理"。

``GET /api/admin/jobs``：读 ``stock_bar_import_job`` 最近 20 条。这张表由 timing_driven
维护，factor_research 只读；若未来 schema 变化或表为空，静默回空列表，避免把运维
页面打崩。
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.api.schemas import ok
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AggregateIn(BaseModel):
    """``POST /api/admin/bar_1d:aggregate`` 请求体。

    start / end 闭区间；不传则让 ``aggregate`` 自己找窗口——这里强制必填以避免
    "误跑十年"。
    """

    start: date
    end: date


class QfqImportIn(BaseModel):
    """``POST /api/admin/qfq:import`` 请求体。

    - ``file_path`` 缺省 → 用 ``settings.qfq_factor_path``；
    - ``chunk_size`` 默认 500 列一批。
    """

    file_path: str | None = None
    chunk_size: int = 500


class BaostockInstrumentsSyncIn(BaseModel):
    """``POST /api/admin/instruments:sync_baostock`` 请求体。

    无参数版本：直接从 Baostock 拉全市场（含退市）标的列表到 ``fr_instrument``。
    本接口幂等（upsert），重复调用是安全的。
    """


class BaostockCalendarSyncIn(BaseModel):
    """``POST /api/admin/calendar:sync_baostock`` 请求体。

    - start / end：日历区间，闭区间；不传默认 2015-01-01 到当日。
    - Phase 1 只支持 CN 市场，市场参数暂不暴露。
    """

    start: date | None = None
    end: date | None = None


class BaostockIndustrySyncIn(BaseModel):
    """``POST /api/admin/industry:sync_baostock`` 请求体。

    无参数。Baostock 行业接口只返回当前快照（updateDate 是接口刷新日，不是行业归
    属变更日），故本接口仅用于刷新 ``fr_industry_current``，**没有历史回溯能力**；
    历史归属下个 phase 接 Akshare 申万解决。
    """


class BaostockIndexConstituentSyncIn(BaseModel):
    """``POST /api/admin/index_constituent:sync_baostock`` 请求体。

    - ``start`` / ``end``：探测窗口；不传默认 2015-01-01..今天；
    - ``index_codes``：可选 subset，如 ``["000300.SH"]``；不传默认 HS300+ZZ500+ZZ1000。
    """

    start: date | None = None
    end: date | None = None
    index_codes: list[str] | None = None


class BaostockProfitSyncIn(BaseModel):
    """``POST /api/admin/profit:sync_baostock`` 请求体。

    - ``universe``：``"hs300_history"`` / ``"all_in_db"`` / 显式 symbol 列表；默认 hs300_history。
    - ``start_year`` / ``start_quarter``：起始季度，默认 2018Q1；
    - ``end_date``：探测窗口右端，缺省今天。

    任务为长跑（HS300 历史 ≈ 17000 次 baostock 调用，~1-3 小时），通过
    BackgroundTasks 提交，进度看 server log。
    """

    universe: str | list[str] = "hs300_history"
    start_year: int = 2018
    start_quarter: int = 1
    end_date: date | None = None


class Bar1mImportIn(BaseModel):
    """``POST /api/admin/bar_1m:import`` 请求体。

    - ``mode``：``"full"`` 全量 / ``"incremental"`` 增量；增量用 ClickHouse
      ``MAX(trade_date)`` 作为起点；
    - ``symbol``：单股模式（调试用，如 ``000001.SZ``）；留空则扫 ``base_dir``；
    - ``base_dir``：QMT 数据根目录；留空则用环境变量 ``IQUANT_LOCAL_DATA_DIR``；
    - ``rewind_days``：增量模式回退的自然日数，默认 3（留足除权 / 周末安全窗）。
    """

    mode: str = "incremental"
    symbol: str | None = None
    base_dir: str | None = None
    rewind_days: int = 3


def _run_aggregate_safely(start: date, end: date) -> None:
    """BackgroundTasks 回调：异常必须被吞掉，否则会污染 ASGI 事件循环日志。"""
    try:
        # 延迟 import：aggregate_bar_1d 在 import 时会触碰 settings，启动阶段不该触发。
        from backend.scripts.aggregate_bar_1d import aggregate

        aggregate(start, end)
    except Exception:  # noqa: BLE001
        log.exception("admin aggregate_bar_1d failed: [%s, %s]", start, end)


def _run_qfq_import_safely(file_path: str | None, chunk_size: int) -> None:
    try:
        from backend.scripts.importers.qfq import run_import

        run_import(file_path=file_path, chunk_size=chunk_size)
    except Exception:  # noqa: BLE001
        log.exception(
            "admin qfq import failed: file=%s chunk=%d", file_path, chunk_size
        )


def _run_sync_baostock_instruments_safely() -> None:
    """BackgroundTasks 回调：Baostock → fr_instrument 全量同步。

    所有异常都吞在日志里；失败时前端感知只能通过重试 + 看 server log，暂无 job 表
    承载进度（后续 Phase 2 可考虑复用 stock_bar_import_job 或新建 fr_admin_jobs）。
    """
    try:
        from backend.adapters.baostock.client import baostock_session
        from backend.adapters.baostock.instruments import sync_instruments

        with baostock_session():
            result = sync_instruments()
        log.info("sync_baostock_instruments ok: %s", result)
    except Exception:  # noqa: BLE001
        log.exception("sync_baostock_instruments failed")


def _run_sync_baostock_calendar_safely(
    start: date | None, end: date | None
) -> None:
    """BackgroundTasks 回调：Baostock → fr_trade_calendar（CN 市场）。"""
    try:
        from backend.adapters.baostock.calendar import sync_calendar
        from backend.adapters.baostock.client import baostock_session

        # 默认窗口：Phase 1 敲定的 2015-01-01 到当日。
        s = start or date(2015, 1, 1)
        e = end or date.today()
        if s > e:
            log.error("sync_baostock_calendar skipped: start %s > end %s", s, e)
            return
        with baostock_session():
            result = sync_calendar(s, e, market="CN")
        log.info("sync_baostock_calendar ok: %s..%s %s", s, e, result)
    except Exception:  # noqa: BLE001
        log.exception(
            "sync_baostock_calendar failed: start=%s end=%s", start, end
        )


def _run_sync_baostock_industry_safely() -> None:
    """BackgroundTasks 回调：Baostock → fr_industry_current。"""
    try:
        from backend.adapters.baostock.client import baostock_session
        from backend.adapters.baostock.industry import sync_industry

        with baostock_session():
            result = sync_industry()
        log.info("sync_baostock_industry ok: %s", result)
    except Exception:  # noqa: BLE001
        log.exception("sync_baostock_industry failed")


def _run_sync_baostock_index_constituent_safely(
    start: date | None,
    end: date | None,
    index_codes: list[str] | None,
) -> None:
    """BackgroundTasks 回调：Baostock → fr_index_constituent（按 updateDate 翻篇）。"""
    try:
        from backend.adapters.baostock.client import baostock_session
        from backend.adapters.baostock.index_constituent import (
            sync_index_constituent,
        )

        with baostock_session():
            result = sync_index_constituent(
                index_codes=index_codes, start=start, end=end
            )
        log.info("sync_baostock_index_constituent ok: %s", result)
    except Exception:  # noqa: BLE001
        log.exception(
            "sync_baostock_index_constituent failed: start=%s end=%s codes=%s",
            start,
            end,
            index_codes,
        )


def _run_sync_baostock_profit_safely(
    universe: str | list[str],
    start_year: int,
    start_quarter: int,
    end_date: date | None,
) -> None:
    """BackgroundTasks 回调：Baostock query_profit_data → fr_fundamental_profit。"""
    try:
        from backend.adapters.baostock.client import baostock_session
        from backend.adapters.baostock.profit import sync_profit

        with baostock_session():
            result = sync_profit(
                universe=universe,
                start_year=start_year,
                start_quarter=start_quarter,
                end=end_date,
            )
        log.info("sync_baostock_profit ok: %s", result)
    except Exception:  # noqa: BLE001
        log.exception(
            "sync_baostock_profit failed: universe=%s window=%dQ%d..%s",
            universe,
            start_year,
            start_quarter,
            end_date,
        )


def _run_bar_1m_import_safely(
    mode: str,
    symbol: str | None,
    base_dir: str | None,
    rewind_days: int,
) -> None:
    """BackgroundTasks 回调：QMT .DAT → ClickHouse stock_bar_1m。

    异常必须被吞掉——BackgroundTasks 的 hook 里抛异常会污染 ASGI 日志；失败信息
    已在 run_import 内写入 ``stock_bar_import_job``，前端通过 ``/api/admin/jobs``
    能看到 status / error。
    """
    try:
        from backend.scripts.importers.stock_1m import run_import

        run_import(
            mode=mode,
            symbol=symbol,
            base_dir=base_dir,
            rewind_days=rewind_days,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "admin bar_1m import failed: mode=%s symbol=%s base_dir=%s",
            mode,
            symbol,
            base_dir,
        )


@router.post("/bar_1d:aggregate")
def trigger_aggregate(body: AggregateIn, bt: BackgroundTasks) -> dict:
    """触发日线聚合任务（异步）。"""
    if body.start > body.end:
        raise HTTPException(status_code=400, detail="start must be <= end")
    bt.add_task(_run_aggregate_safely, body.start, body.end)
    return ok(
        {
            "message": "aggregate task submitted; see server logs for progress",
            "start": body.start.isoformat(),
            "end": body.end.isoformat(),
        }
    )


@router.post("/qfq:import")
def trigger_qfq_import(body: QfqImportIn, bt: BackgroundTasks) -> dict:
    """触发 qfq parquet 导入任务（异步）。"""
    bt.add_task(_run_qfq_import_safely, body.file_path, body.chunk_size)
    return ok(
        {
            "message": "qfq import task submitted; see server logs for progress",
            "file_path": body.file_path,
            "chunk_size": body.chunk_size,
        }
    )


@router.post("/bar_1m:import")
def trigger_bar_1m_import(body: Bar1mImportIn, bt: BackgroundTasks) -> dict:
    """触发 QMT .DAT → ClickHouse stock_bar_1m 导入任务（异步）。

    mode 只允许 ``full`` / ``incremental``；其它值直接 400。单用户研究场景不做并发
    锁——后端目前也没有运行中 job 去重机制；如果用户连点两下会起两次任务，
    两次都安全（ReplacingMergeTree 会以更大 version 覆盖），只是浪费时间。
    """
    if body.mode not in {"full", "incremental"}:
        raise HTTPException(
            status_code=400,
            detail=f"mode must be 'full' or 'incremental', got {body.mode!r}",
        )
    bt.add_task(
        _run_bar_1m_import_safely,
        body.mode,
        body.symbol,
        body.base_dir,
        body.rewind_days,
    )
    return ok(
        {
            "message": "bar_1m import task submitted; see /api/admin/jobs for status",
            "mode": body.mode,
            "symbol": body.symbol,
            "base_dir": body.base_dir,
            "rewind_days": body.rewind_days,
        }
    )


@router.post("/instruments:sync_baostock")
def trigger_sync_baostock_instruments(
    body: BaostockInstrumentsSyncIn, bt: BackgroundTasks
) -> dict:
    """从 Baostock 全量同步标的（含退市）到 ``fr_instrument``。

    幂等；重复调用会 upsert。无 job 表承载进度，观察日志即可；Phase 2 再补 job 表。
    """
    _ = body  # 当前无参数；保留 body 以便未来扩展而不破坏前端调用
    bt.add_task(_run_sync_baostock_instruments_safely)
    return ok({"message": "baostock instruments sync submitted; see server logs"})


@router.post("/calendar:sync_baostock")
def trigger_sync_baostock_calendar(
    body: BaostockCalendarSyncIn, bt: BackgroundTasks
) -> dict:
    """从 Baostock 同步 A 股交易日历到 ``fr_trade_calendar``。

    默认窗口 2015-01-01 到当日；可通过 body 覆盖。
    """
    bt.add_task(_run_sync_baostock_calendar_safely, body.start, body.end)
    return ok(
        {
            "message": "baostock calendar sync submitted; see server logs",
            "start": body.start.isoformat() if body.start else None,
            "end": body.end.isoformat() if body.end else None,
        }
    )


@router.post("/industry:sync_baostock")
def trigger_sync_baostock_industry(
    body: BaostockIndustrySyncIn, bt: BackgroundTasks
) -> dict:
    """从 Baostock 同步当前行业归属到 ``fr_industry_current``。"""
    _ = body
    bt.add_task(_run_sync_baostock_industry_safely)
    return ok({"message": "baostock industry sync submitted; see server logs"})


@router.post("/index_constituent:sync_baostock")
def trigger_sync_baostock_index_constituent(
    body: BaostockIndexConstituentSyncIn, bt: BackgroundTasks
) -> dict:
    """按 updateDate 翻篇同步指数成分历史到 ``fr_index_constituent``。"""
    if body.start and body.end and body.start > body.end:
        raise HTTPException(status_code=400, detail="start must be <= end")
    bt.add_task(
        _run_sync_baostock_index_constituent_safely,
        body.start,
        body.end,
        body.index_codes,
    )
    return ok(
        {
            "message": "baostock index_constituent sync submitted; see server logs",
            "start": body.start.isoformat() if body.start else None,
            "end": body.end.isoformat() if body.end else None,
            "index_codes": body.index_codes,
        }
    )


@router.post("/profit:sync_baostock")
def trigger_sync_baostock_profit(
    body: BaostockProfitSyncIn, bt: BackgroundTasks
) -> dict:
    """从 Baostock query_profit_data 同步财报数据到 ``fr_fundamental_profit``。

    长跑任务（HS300 历史 ~1-3 小时）；BackgroundTasks 提交后立即返回，进度看日志。
    """
    if body.start_quarter < 1 or body.start_quarter > 4:
        raise HTTPException(
            status_code=400, detail="start_quarter must be in [1,4]"
        )
    bt.add_task(
        _run_sync_baostock_profit_safely,
        body.universe,
        body.start_year,
        body.start_quarter,
        body.end_date,
    )
    return ok(
        {
            "message": "baostock profit sync submitted; long-running, see server logs",
            "universe": body.universe,
            "start_year": body.start_year,
            "start_quarter": body.start_quarter,
            "end_date": body.end_date.isoformat() if body.end_date else None,
        }
    )


@router.post("/datasources:probe")
def probe_datasources() -> dict:
    """同步探测 akshare / baostock / MySQL / ClickHouse 是否可用。

    每个源各跑一次最小请求（akshare A 股代码列表 / baostock query_trade_dates /
    SELECT 1 / SELECT 1），返回各自 ``status`` + ``latency_ms`` + ``message``。
    任一项失败不影响其它项；总耗时通常 <10s。详见
    ``services/datasource_probe_service.py``。
    """
    from backend.services.datasource_probe_service import probe_all

    return ok({"sources": probe_all()})


@router.get("/jobs")
def list_jobs() -> dict:
    """列最近 20 条 ``stock_bar_import_job``。表缺失 / 异常时回空列表并打 log。

    factor_research 只读这张表；schema 见 ``docker-compose-test/mysql/mysql-init.sql``。
    """
    try:
        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT job_id, job_type, status, symbol_count, "
                    "success_symbol_count, failed_symbol_count, "
                    "inserted_rows, updated_rows, started_at, finished_at, note "
                    "FROM stock_bar_import_job "
                    "ORDER BY job_id DESC LIMIT 20"
                )
                return ok(cur.fetchall())
    except Exception:  # noqa: BLE001
        # 表不存在 / 权限不足等：回空列表比把页面打挂更友好。
        log.exception("list_jobs failed; returning empty list")
        return ok([])
