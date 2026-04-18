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
