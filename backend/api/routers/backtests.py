"""回测 run 的 CRUD + 触发异步任务 + 产物下载。

结构与 evals.py 同构，差异：
- 使用 ``fr_backtest_runs`` / ``fr_backtest_metrics`` / ``fr_backtest_artifacts`` 表；
- 调 ``backtest_entry``；
- 3 个产物下载端点：``GET /{run_id}/{equity|orders|trades}``，返回 parquet 文件。

产物下载的安全要点：
- 从 ``fr_backtest_artifacts`` 读到 ``artifact_path`` 后要做 **resolve + 路径前缀校验**，
  避免外部把 artifact_path 写成 ``../../etc/passwd`` 实现路径穿越；
- ``commonpath`` 在 str 比较时对尾部 / 不敏感，但两边都要 resolve。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from backend.api.schemas import CreateBacktestIn, ok
from backend.config import settings
from backend.runtime.entries import backtest_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.services.params_hash import params_hash
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

# 产物根目录 resolve 一次，后续 commonpath 比较都用这份；避免每次 os.path.realpath。
# backtest_service 也用 settings.artifact_dir，这里必须保持一致。
_ARTIFACT_DIR_RESOLVED = str(Path(settings.artifact_dir).resolve())

# 白名单产物类型：避免前端把 ``../trades`` 这种特殊 artifact_type 传进来绕过校验。
_ALLOWED_ARTIFACT_TYPES = ("equity", "orders", "trades")


@router.post("")
def create_backtest(body: CreateBacktestIn, bt: BackgroundTasks) -> dict:
    """创建回测任务并派发到 ProcessPool。"""
    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        factor = reg.get(body.factor_id)
    except KeyError:
        raise HTTPException(status_code=400, detail="factor not found")

    version = reg.latest_version_from_db(body.factor_id)
    params = body.params or factor.default_params
    phash = params_hash(params)
    run_id = uuid.uuid4().hex

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_backtest_runs
                (run_id, factor_id, factor_version, params_hash, params_json,
                 pool_id, freq, start_date, end_date,
                 status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
                """,
                (
                    run_id,
                    body.factor_id,
                    version,
                    phash,
                    json.dumps(params, ensure_ascii=False),
                    body.pool_id,
                    body.freq,
                    body.start_date,
                    body.end_date,
                    # fr_backtest_runs 的 created_at 是 datetime(6)，.now() 精度足够。
                    datetime.now(),
                ),
            )
        c.commit()

    bt.add_task(submit, backtest_entry, run_id, body.model_dump(mode="json"))
    return ok({"run_id": run_id, "status": "pending"})


@router.get("")
def list_backtests(
    factor_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """列出回测任务（倒序 + 可选筛）。"""
    limit = max(1, min(int(limit), 500))
    sql = "SELECT * FROM fr_backtest_runs WHERE 1=1"
    params: list = []
    if factor_id:
        sql += " AND factor_id=%s"
        params.append(factor_id)
    if status:
        sql += " AND status=%s"
        params.append(status)
    sql += " ORDER BY created_at DESC, run_id DESC LIMIT %s"
    params.append(limit)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            return ok(cur.fetchall())


@router.get("/{run_id}")
def get_backtest(run_id: str) -> dict:
    """返回 run 完整记录 + metrics payload + artifacts 列表。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_backtest_runs WHERE run_id=%s", (run_id,)
            )
            run = cur.fetchone()
            if not run:
                raise HTTPException(
                    status_code=404, detail="backtest run not found"
                )
            cur.execute(
                "SELECT * FROM fr_backtest_metrics WHERE run_id=%s", (run_id,)
            )
            m = cur.fetchone()
            cur.execute(
                "SELECT artifact_type, artifact_path "
                "FROM fr_backtest_artifacts WHERE run_id=%s",
                (run_id,),
            )
            arts = cur.fetchall()
    if m and m.get("payload_json"):
        try:
            m["payload"] = json.loads(m.pop("payload_json"))
        except (ValueError, TypeError):
            m["payload"] = None
    run["metrics"] = m
    # artifact_path 是服务器本地路径，不直接回前端——前端应通过专门的下载端点取。
    run["artifacts"] = [
        {"artifact_type": a["artifact_type"]} for a in (arts or [])
    ]
    return ok(run)


@router.get("/{run_id}/status")
def get_backtest_status(run_id: str) -> dict:
    """回测状态端点（轻量轮询用）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, "
                "started_at, finished_at "
                "FROM fr_backtest_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return ok(r)


@router.delete("/{run_id}")
def delete_backtest(run_id: str) -> dict:
    """硬删 run + metrics + artifacts 三张表记录。不清磁盘 parquet 文件。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_backtest_metrics WHERE run_id=%s", (run_id,)
            )
            cur.execute(
                "DELETE FROM fr_backtest_artifacts WHERE run_id=%s", (run_id,)
            )
            cur.execute(
                "DELETE FROM fr_backtest_runs WHERE run_id=%s", (run_id,)
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return ok({"run_id": run_id, "deleted": True})


# ---------------------------- 产物下载 ----------------------------


def _resolve_artifact(run_id: str, artifact_type: str) -> Path:
    """查 fr_backtest_artifacts 拿 path，校验安全后返回 ``Path``。

    校验两层：
    1. ``artifact_type`` 白名单：防止 query 参数走奇葩类型绕过；
    2. 路径前缀校验：resolve 后必须在 ``_ARTIFACT_DIR_RESOLVED`` 下，
       防止历史脏数据里有 ``../..`` 或绝对路径穿越。
    """
    if artifact_type not in _ALLOWED_ARTIFACT_TYPES:
        # 理论上由路由路径固定传入，不会触发；属于深度防御。
        raise HTTPException(status_code=400, detail="invalid artifact type")

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT artifact_path FROM fr_backtest_artifacts "
                "WHERE run_id=%s AND artifact_type=%s",
                (run_id, artifact_type),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="artifact not found")

    raw_path = str(row["artifact_path"])
    abs_path = str(Path(raw_path).resolve())
    # commonpath 在 Windows / POSIX 下对大小写的处理不同，但单机 macOS/Linux 够用。
    try:
        common = os.path.commonpath([abs_path, _ARTIFACT_DIR_RESOLVED])
    except ValueError:
        # 不同盘符 / 完全无共同前缀：直接判非法。
        raise HTTPException(status_code=400, detail="artifact path rejected")
    if common != _ARTIFACT_DIR_RESOLVED:
        # 典型触发场景：artifact_path 被污染成项目外路径；拒绝访问。
        raise HTTPException(status_code=400, detail="artifact path rejected")

    path = Path(abs_path)
    if not path.is_file():
        # 数据库记录存在但文件被清：返回 410 比 404 更精确，但 FastAPI HTTPException
        # 统一走 404，避免前端多处判码。
        raise HTTPException(status_code=404, detail="artifact file missing")
    return path


@router.get("/{run_id}/equity")
def download_equity(run_id: str) -> FileResponse:
    """下载 equity.parquet。"""
    path = _resolve_artifact(run_id, "equity")
    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=f"{run_id}-equity.parquet",
    )


@router.get("/{run_id}/orders")
def download_orders(run_id: str) -> FileResponse:
    """下载 orders.parquet。"""
    path = _resolve_artifact(run_id, "orders")
    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=f"{run_id}-orders.parquet",
    )


@router.get("/{run_id}/trades")
def download_trades(run_id: str) -> FileResponse:
    """下载 trades.parquet。"""
    path = _resolve_artifact(run_id, "trades")
    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=f"{run_id}-trades.parquet",
    )
