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


def _run_aggregate_safely(start: date, end: date) -> None:
    """BackgroundTasks 回调：异常必须被吞掉，否则会污染 ASGI 事件循环日志。"""
    try:
        # 延迟 import：aggregate_bar_1d 会做 _safety_check，启动阶段不该触发。
        from backend.scripts.aggregate_bar_1d import aggregate

        aggregate(start, end)
    except Exception:  # noqa: BLE001
        log.exception("admin aggregate_bar_1d failed: [%s, %s]", start, end)


def _run_qfq_import_safely(file_path: str | None, chunk_size: int) -> None:
    try:
        from backend.scripts.import_qfq import run_import

        run_import(file_path=file_path, chunk_size=chunk_size)
    except Exception:  # noqa: BLE001
        log.exception(
            "admin qfq import failed: file=%s chunk=%d", file_path, chunk_size
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
