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
from backend.runtime.entries import (
    pattern_search_entry,
    pattern_search_learned_entry,
    pattern_search_window_entry,
)
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
    min_score: float = 0.0   # 综合相似度阈值，低于此分不返回（0=不过滤）


@router.post("/by_stock")
def post_by_stock(req: ByStockReq) -> dict:
    res = search_by_stock(
        DataService(), symbol=req.symbol,
        window_start=req.window_start, window_end=req.window_end,
        scales=req.scales, top_k=req.top_k, min_score=req.min_score,
    )
    return ok(res)


# ---------------------------- 相似K线选股：用真实走势在池里选股（异步任务） ----------------------------


class WindowSpec(BaseModel):
    symbol: str
    start: str | None = None
    end: str | None = None


class ByWindowReq(BaseModel):
    pool_id: int                            # 在哪个股票池里选股
    windows: list[WindowSpec] | None = None  # 一段或多段查询走势（多段=联合检索，抗过拟合）
    # 兼容单段旧调用：
    symbol: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    scales: list[int] | None = None
    top_k: int = 20
    agg: str = "min"
    min_score: float = 0.0


@router.post("/by_window")
def post_by_window(req: ByWindowReq, bt: BackgroundTasks) -> dict:
    """创建「相似K线选股」任务并派发到 ProcessPool，立刻返回 run_id。

    全池 DTW 较慢易超时，故和 by_image 一样走异步任务 + 轮询。支持一段或多段走势联合检索。
    """
    # 归一化查询窗口：windows 优先；否则用单段 symbol/window_*。
    windows: list[dict] = []
    if req.windows:
        windows = [w.model_dump() for w in req.windows]
    elif req.symbol:
        windows = [{"symbol": req.symbol, "start": req.window_start, "end": req.window_end}]
    if not windows:
        raise HTTPException(status_code=400, detail="至少需要一段查询走势")

    run_id = uuid.uuid4().hex
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_pattern_search_runs
                (run_id, kind, pool_id, query_json, num_images, scales_json,
                 top_k, agg, status, progress, created_at)
                VALUES (%s,'by_window',%s,%s,%s,%s,%s,%s,'pending',0,%s)
                """,
                (
                    run_id,
                    req.pool_id,
                    json.dumps(windows, ensure_ascii=False),
                    len(windows),
                    json.dumps(req.scales) if req.scales else None,
                    req.top_k,
                    req.agg,
                    datetime.now(),
                ),
            )
        c.commit()

    body = {
        "windows": windows, "pool_id": req.pool_id, "scales": req.scales,
        "top_k": req.top_k, "agg": req.agg, "min_score": req.min_score,
    }
    bt.add_task(submit, pattern_search_window_entry, run_id, body)
    return ok({"run_id": run_id, "status": "pending"})


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
    min_score: float = 0.0            # 综合相似度阈值，低于此分不返回（0=不过滤）


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


# ---------------------------- 学习型选股：标注 + 训练打分（异步） ----------------------------


class LabelReq(BaseModel):
    pattern_name: str
    symbol: str
    start: str | None = None
    end: str | None = None
    label: int            # 1=正例 / 0=反例


@router.post("/labels")
def add_label(req: LabelReq) -> dict:
    """给某命名形态加一条标注（正例/反例）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO fr_pattern_labels "
                "(pattern_name, symbol, start_date, end_date, label, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (req.pattern_name, req.symbol.upper(), req.start, req.end,
                 1 if req.label == 1 else 0, datetime.now()),
            )
            new_id = cur.lastrowid
        c.commit()
    return ok({"id": new_id})


@router.get("/labels")
def list_labels(pattern_name: str) -> dict:
    """列出某形态的全部标注。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, pattern_name, symbol, start_date, end_date, label, created_at "
                "FROM fr_pattern_labels WHERE pattern_name=%s ORDER BY id DESC",
                (pattern_name,),
            )
            return ok(cur.fetchall())


@router.delete("/labels/{label_id}")
def delete_label(label_id: int) -> dict:
    """删一条标注。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM fr_pattern_labels WHERE id=%s", (label_id,))
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="label not found")
    return ok({"id": label_id, "deleted": True})


class ByLearnedReq(BaseModel):
    pattern_name: str
    pool_id: int
    scales: list[int] | None = None
    top_k: int = 20


@router.post("/by_learned")
def post_by_learned(req: ByLearnedReq, bt: BackgroundTasks) -> dict:
    """创建「学习型选股」任务：读该形态的标注 → 训练打分器 → 给池打分（异步）。"""
    run_id = uuid.uuid4().hex
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_pattern_search_runs
                (run_id, kind, pool_id, query_json, num_images, scales_json,
                 top_k, agg, status, progress, created_at)
                VALUES (%s,'learned',%s,%s,0,%s,%s,'-','pending',0,%s)
                """,
                (
                    run_id,
                    req.pool_id,
                    json.dumps({"pattern_name": req.pattern_name}, ensure_ascii=False),
                    json.dumps(req.scales) if req.scales else None,
                    req.top_k,
                    datetime.now(),
                ),
            )
        c.commit()

    body = {
        "pattern_name": req.pattern_name, "pool_id": req.pool_id,
        "scales": req.scales, "top_k": req.top_k,
    }
    bt.add_task(submit, pattern_search_learned_entry, run_id, body)
    return ok({"run_id": run_id, "status": "pending"})


@router.get("/runs")
def list_pattern_runs(status: str | None = None, limit: int = 50) -> dict:
    """列出检索任务（倒序 + 可选按 status 筛）。不返回曲线/结果大字段。"""
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, kind, pool_id, image_names, query_json, num_images, hint, top_k, agg, "
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
        for col in ("image_names", "query_json"):
            if r.get(col):
                try:
                    r[col] = json.loads(r[col])
                except (ValueError, TypeError):
                    r[col] = None
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
    for col in ("image_names", "query_json"):
        if run.get(col):
            try:
                run[col] = json.loads(run[col])
            except (ValueError, TypeError):
                run[col] = None
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
