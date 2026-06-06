"""图形相似度检索端点。

- POST /api/pattern_search/by_stock：需求2，个股历史自相似（**同步**，快）。
- POST /api/pattern_search/by_image：需求1，截图找相似股票（**异步任务**）。
  by_image 涉及视觉 LLM + 全池 DTW，耗时长易超时，故改为「创建任务 → 轮询」：
  - POST /by_image            创建任务，立刻返回 run_id（pending）
  - GET  /runs               任务列表（倒序，可按 status 筛）
  - GET  /runs/{run_id}      任务详情（含识别曲线 + 检索结果）
  - GET  /runs/{run_id}/status  轻量轮询
  - POST /runs/{run_id}/abort   协作式中断
  - DELETE /runs/{run_id}    删除记录
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.api.schemas import ok
from backend.runtime.entries import pattern_search_entry
from backend.runtime.task_pool import submit
from backend.services.pattern_query import search_by_stock
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/pattern_search", tags=["pattern_search"])


# ---------------------------- 需求2：个股历史自相似（同步） ----------------------------


class ByStockReq(BaseModel):
    symbol: str
    window_start: str | None = None
    window_end: str | None = None
    scales: list[int] | None = None
    top_k: int = 20


@router.post("/by_stock")
def post_by_stock(req: ByStockReq) -> dict:
    res = search_by_stock(
        DataService(), symbol=req.symbol,
        window_start=req.window_start, window_end=req.window_end,
        scales=req.scales, top_k=req.top_k,
    )
    return ok(res)


# ---------------------------- 需求1：截图找相似股票（异步任务） ----------------------------


class ByImageReq(BaseModel):
    pool_id: int
    images: list[str] | None = None   # 多张 data URI（综合检索）
    image: str | None = None          # 单张 data URI（兼容）
    image_names: list[str] | None = None  # 上传文件名（仅展示用）
    hint: str | None = None
    scales: list[int] | None = None
    top_k: int = 20
    agg: str = "min"                  # 多图聚合：min=对每张都像 / mean=平均


@router.post("/by_image")
def post_by_image(req: ByImageReq, bt: BackgroundTasks) -> dict:
    """创建截图检索任务并派发到 ProcessPool，立刻返回 run_id。"""
    n_images = len(req.images) if req.images else (1 if req.image else 0)
    if n_images == 0:
        raise HTTPException(status_code=400, detail="至少需要一张截图")

    run_id = uuid.uuid4().hex
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_pattern_search_runs
                (run_id, pool_id, image_names, num_images, hint, scales_json,
                 top_k, agg, status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
                """,
                (
                    run_id,
                    req.pool_id,
                    json.dumps(req.image_names or [], ensure_ascii=False),
                    n_images,
                    req.hint,
                    json.dumps(req.scales) if req.scales else None,
                    req.top_k,
                    req.agg,
                    datetime.now(),
                ),
            )
        c.commit()

    bt.add_task(submit, pattern_search_entry, run_id, req.model_dump(mode="json"))
    return ok({"run_id": run_id, "status": "pending"})


@router.get("/runs")
def list_pattern_runs(status: str | None = None, limit: int = 50) -> dict:
    """列出检索任务（倒序 + 可选按 status 筛）。不返回曲线/结果大字段。"""
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, pool_id, image_names, num_images, hint, top_k, agg, "
        "status, progress, error_message, created_at, started_at, finished_at "
        "FROM fr_pattern_search_runs WHERE 1=1"
    )
    params: list = []
    if status:
        sql += " AND status=%s"
        params.append(status)
    sql += " ORDER BY created_at DESC, run_id DESC LIMIT %s"
    params.append(limit)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    for r in rows or []:
        if r.get("image_names"):
            try:
                r["image_names"] = json.loads(r["image_names"])
            except (ValueError, TypeError):
                r["image_names"] = []
    return ok(rows)


@router.get("/runs/{run_id}")
def get_pattern_run(run_id: str) -> dict:
    """返回 run 完整记录 + 识别曲线 + 检索结果。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_pattern_search_runs WHERE run_id=%s", (run_id,)
            )
            run = cur.fetchone()
            if not run:
                raise HTTPException(status_code=404, detail="pattern search run not found")
            cur.execute(
                "SELECT query_curves_json, matches_json "
                "FROM fr_pattern_search_results WHERE run_id=%s",
                (run_id,),
            )
            res = cur.fetchone()
    if run.get("image_names"):
        try:
            run["image_names"] = json.loads(run["image_names"])
        except (ValueError, TypeError):
            run["image_names"] = []
    run["query_curves"] = []
    run["matches"] = []
    if res:
        try:
            run["query_curves"] = json.loads(res.get("query_curves_json") or "[]")
            run["matches"] = json.loads(res.get("matches_json") or "[]")
        except (ValueError, TypeError):
            pass
    return ok(run)


@router.get("/runs/{run_id}/status")
def get_pattern_run_status(run_id: str) -> dict:
    """轻量轮询端点。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, started_at, finished_at "
                "FROM fr_pattern_search_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="pattern search run not found")
    return ok(r)


@router.post("/runs/{run_id}/abort")
def abort_pattern_run(run_id: str) -> dict:
    """请求中断排队/运行中的任务（协作式）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_pattern_search_runs SET status='aborting' "
                "WHERE run_id=%s AND status IN ('pending','running')",
                (run_id,),
            )
            changed = cur.rowcount
            cur.execute(
                "SELECT status FROM fr_pattern_search_runs WHERE run_id=%s", (run_id,)
            )
            row = cur.fetchone()
        c.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="pattern search run not found")
    current_status = row["status"]
    if changed == 0 and current_status != "aborting":
        raise HTTPException(
            status_code=409,
            detail=f"cannot abort: current status is '{current_status}'",
        )
    return ok({"run_id": run_id, "status": current_status})


@router.delete("/runs/{run_id}")
def delete_pattern_run(run_id: str) -> dict:
    """硬删 run + results 两张表记录。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_pattern_search_results WHERE run_id=%s", (run_id,)
            )
            cur.execute(
                "DELETE FROM fr_pattern_search_runs WHERE run_id=%s", (run_id,)
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="pattern search run not found")
    return ok({"run_id": run_id, "deleted": True})
