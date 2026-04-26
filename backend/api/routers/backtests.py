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

from backend.api.schemas import BatchDeleteIn, CreateBacktestIn, ok
from backend.config import settings
from backend.runtime.entries import backtest_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.services.backtest_artifact_view import (
    load_equity_series,
    load_trades_page,
)
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
    """列出回测任务（倒序 + 可选筛）。

    同 ``list_evals``：只返回列表页需要的字段，不带 ``params_json``（可能几 KB 一行）。
    需要完整参数时走 ``GET /api/backtests/{run_id}``。
    """
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, factor_id, factor_version, params_hash, pool_id, freq, "
        "start_date, end_date, status, progress, error_message, "
        "created_at, started_at, finished_at "
        "FROM fr_backtest_runs WHERE 1=1"
    )
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


@router.post("/{run_id}/abort")
def abort_backtest(run_id: str) -> dict:
    """请求中断一个排队 / 运行中的回测任务。语义与 abort_eval 一致（协作式）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_backtest_runs SET status='aborting' "
                "WHERE run_id=%s AND status IN ('pending','running')",
                (run_id,),
            )
            changed = cur.rowcount
            cur.execute(
                "SELECT status FROM fr_backtest_runs WHERE run_id=%s",
                (run_id,),
            )
            row = cur.fetchone()
        c.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    current_status = row["status"]
    if changed == 0 and current_status != "aborting":
        raise HTTPException(
            status_code=409,
            detail=f"cannot abort: current status is '{current_status}'",
        )
    return ok({"run_id": run_id, "status": current_status})


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


@router.post("/batch-delete")
def batch_delete_backtests(body: BatchDeleteIn) -> dict:
    """批量硬删多条回测记录（run + metrics + artifacts）。不清磁盘 parquet 文件。"""
    deleted = 0
    with mysql_conn() as c:
        with c.cursor() as cur:
            for rid in body.run_ids:
                cur.execute(
                    "DELETE FROM fr_backtest_metrics WHERE run_id=%s", (rid,)
                )
                cur.execute(
                    "DELETE FROM fr_backtest_artifacts WHERE run_id=%s", (rid,)
                )
                cur.execute(
                    "DELETE FROM fr_backtest_runs WHERE run_id=%s", (rid,)
                )
                deleted += cur.rowcount
        c.commit()
    return ok({"deleted_count": deleted})


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


# ---------------------------- 产物在线查看 ----------------------------
#
# 产物下载（上方）把 parquet 直接丢给用户自己拿 pandas 分析；在线查看端点则把
# parquet 读成前端图表 / 表格可直接吃的 JSON，避免"为了看一眼净值也得下载"。
# 实际的 parquet 解析在 backend/services/backtest_artifact_view.py 里做单测覆盖；
# 这里只做 HTTP 层：路径校验复用 _resolve_artifact，参数校验交给 FastAPI。


@router.get("/{run_id}/equity_series")
def get_equity_series(run_id: str, max_points: int = 2000) -> dict:
    """读 equity.parquet → ``{dates, values, total, sampled}`` JSON。

    ``max_points`` 默认 2000：折线图超过这个点数后眼睛已经分辨不出单点差异，
    多余点纯浪费带宽 / 渲染。超过时做等步长抽样并强制保留首尾。
    """
    # 上限做个硬防御：20k 点浏览器也还画得动，再高就不合理了
    max_points = max(1, min(int(max_points), 20_000))
    path = _resolve_artifact(run_id, "equity")
    return ok(load_equity_series(path, max_points=max_points))


@router.get("/{run_id}/trades_page")
def get_trades_page(
    run_id: str,
    page: int = 1,
    size: int = 50,
    symbol: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """读 trades.parquet → 分页 JSON ``{total, page, size, columns, rows}``。

    支持三个可选筛选：
    - ``symbol``：股票代码子串匹配（大小写不敏感）。VectorBT 的 ``records_readable``
      默认把 symbol 放在 ``Column`` 列；子串匹配覆盖"我只记得代码前几位"的常见用法。
    - ``start_date`` / ``end_date``（YYYY-MM-DD）：按 **开仓时间**（``Entry Timestamp``）
      作为筛选锚点——"这段时间里开的仓"比"这段时间里平的仓"对问答更直观。

    筛选在 **分页之前** 做，所以 ``total`` 反映的是筛选后的总条数；前端拿到直接驱动
    NPagination 的 itemCount 即可，用户翻页不会穿越被筛掉的行。
    """
    path = _resolve_artifact(run_id, "trades")
    try:
        data = load_trades_page(
            path,
            page=page,
            size=size,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as e:
        # schema mismatch / 非法日期格式——用户层可读的 400 错，胜过全局 500
        raise HTTPException(status_code=400, detail=str(e))
    return ok(data)
