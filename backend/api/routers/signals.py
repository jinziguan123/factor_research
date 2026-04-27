"""实盘信号 run 的 CRUD + 异步触发。

结构与 evals.py / compositions.py 同构：
- ``POST /api/signals``：创建（异步派发到 ProcessPool）
- ``GET /api/signals``：列表（按 pool_id / status / 因子 / 日期过滤）
- ``GET /api/signals/{run_id}``：详情（含 payload.top / bottom）
- ``GET /api/signals/{run_id}/status``：轻量状态轮询
- ``POST /api/signals/{run_id}/abort``：协作式中断
- ``DELETE /api/signals/{run_id}``：硬删
- ``POST /api/signals/batch-delete``：批删（沿用 evals 的 BatchDeleteIn）
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.api.schemas import BatchDeleteIn, CreateSignalIn, ok
from backend.runtime.entries import signal_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.post("")
def create_signal(body: CreateSignalIn, bt: BackgroundTasks) -> dict:
    """创建实盘信号任务并派发到 ProcessPool。

    预检：所有 factor_id 必须已注册。as_of_time 默认 NOW()（service 兜底）。
    """
    reg = FactorRegistry()
    reg.scan_and_register()
    missing = []
    for it in body.factor_items:
        try:
            reg.get(it.factor_id)
        except KeyError:
            missing.append(it.factor_id)
    if missing:
        raise HTTPException(
            status_code=400, detail=f"factor not found: {missing}"
        )

    run_id = uuid.uuid4().hex
    items_payload = [it.model_dump(mode="json") for it in body.factor_items]
    as_of_time = body.as_of_time or datetime.now()

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_signal_runs
                (run_id, factor_items_json, method, pool_id, n_groups,
                 ic_lookback_days, as_of_time, as_of_date,
                 use_realtime, filter_price_limit,
                 status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
                """,
                (
                    run_id,
                    json.dumps(items_payload, ensure_ascii=False),
                    body.method,
                    body.pool_id,
                    body.n_groups,
                    body.ic_lookback_days,
                    as_of_time,
                    as_of_time.date(),
                    1 if body.use_realtime else 0,
                    1 if body.filter_price_limit else 0,
                    datetime.now(),
                ),
            )
        c.commit()

    # 用 model_dump(mode="json") 把 datetime / date 转成字符串，worker pickle 友好
    payload = body.model_dump(mode="json")
    payload["as_of_time"] = as_of_time.isoformat()
    bt.add_task(submit, signal_entry, run_id, payload)
    return ok({"run_id": run_id, "status": "pending"})


@router.get("")
def list_signals(
    pool_id: int | None = None,
    method: str | None = None,
    status: str | None = None,
    as_of_date: str | None = None,
    limit: int = 50,
) -> dict:
    """列表：只返结构化 + 轻量元数据，payload 留给详情页。"""
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, factor_items_json, method, pool_id, n_groups, "
        "ic_lookback_days, as_of_time, as_of_date, use_realtime, "
        "filter_price_limit, status, progress, error_message, "
        "n_holdings_top, n_holdings_bot, "
        "created_at, started_at, finished_at "
        "FROM fr_signal_runs WHERE 1=1"
    )
    params: list = []
    if pool_id is not None:
        sql += " AND pool_id=%s"
        params.append(pool_id)
    if method:
        sql += " AND method=%s"
        params.append(method)
    if status:
        sql += " AND status=%s"
        params.append(status)
    if as_of_date:
        sql += " AND as_of_date=%s"
        params.append(as_of_date)
    sql += " ORDER BY created_at DESC, run_id DESC LIMIT %s"
    params.append(limit)

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []

    # factor_items_json 解析为列表，便于前端 chip 渲染
    for r in rows:
        raw = r.pop("factor_items_json", None)
        if raw:
            try:
                r["factor_items"] = json.loads(raw)
            except (TypeError, ValueError):
                r["factor_items"] = []
        else:
            r["factor_items"] = []
    return ok(rows)


@router.get("/{run_id}")
def get_signal(run_id: str) -> dict:
    """返回完整记录，factor_items_json + payload_json 解析后展开。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_signal_runs WHERE run_id=%s", (run_id,),
            )
            run = cur.fetchone()
    if not run:
        raise HTTPException(status_code=404, detail="signal run not found")

    for src, dst in [
        ("factor_items_json", "factor_items"),
        ("payload_json", "payload"),
    ]:
        raw = run.pop(src, None)
        if raw:
            try:
                run[dst] = json.loads(raw)
            except (TypeError, ValueError):
                run[dst] = None
        else:
            run[dst] = None
    return ok(run)


@router.get("/{run_id}/status")
def get_signal_status(run_id: str) -> dict:
    """轻量状态端点，前端轮询用。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, "
                "started_at, finished_at "
                "FROM fr_signal_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="signal run not found")
    return ok(r)


@router.post("/{run_id}/abort")
def abort_signal(run_id: str) -> dict:
    """协作式中断（与 abort_eval 同构）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_signal_runs SET status='aborting' "
                "WHERE run_id=%s AND status IN ('pending','running')",
                (run_id,),
            )
            changed = cur.rowcount
            cur.execute(
                "SELECT status FROM fr_signal_runs WHERE run_id=%s", (run_id,),
            )
            row = cur.fetchone()
        c.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="signal run not found")
    current_status = row["status"]
    if changed == 0 and current_status != "aborting":
        raise HTTPException(
            status_code=409,
            detail=f"cannot abort: current status is '{current_status}'",
        )
    return ok({"run_id": run_id, "status": current_status})


@router.delete("/{run_id}")
def delete_signal(run_id: str) -> dict:
    """硬删一条记录。只一张表，无附加 artifact。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_signal_runs WHERE run_id=%s", (run_id,),
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="signal run not found")
    return ok({"run_id": run_id, "deleted": True})


@router.post("/batch-delete")
def batch_delete_signals(body: BatchDeleteIn) -> dict:
    """批量硬删（沿用 evals 的 BatchDeleteIn schema）。"""
    deleted = 0
    with mysql_conn() as c:
        with c.cursor() as cur:
            for rid in body.run_ids:
                cur.execute(
                    "DELETE FROM fr_signal_runs WHERE run_id=%s", (rid,),
                )
                deleted += cur.rowcount
        c.commit()
    return ok({"deleted_count": deleted})
