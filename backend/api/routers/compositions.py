"""多因子合成的 CRUD + 触发异步任务。

结构对齐 evals.py / cost_sensitivity.py：
- ``POST   /api/compositions``：建 run 记录 + 派发 worker。
- ``GET    /api/compositions``：列表页（倒序 + 可选 pool_id / method / status 过滤，
  不返回大字段 payload_json / corr_matrix_json / weights_json / per_factor_ic_json）。
- ``GET    /api/compositions/{run_id}``：详情（解析所有 JSON 字段后一次返回）。
- ``GET    /api/compositions/{run_id}/status``：状态轮询轻量端点。
- ``DELETE /api/compositions/{run_id}``：硬删。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.api.schemas import CreateCompositionIn, ok
from backend.runtime.entries import composition_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/compositions", tags=["compositions"])


@router.post("")
def create_composition(
    body: CreateCompositionIn, bt: BackgroundTasks
) -> dict:
    """创建多因子合成任务并派发到 ProcessPool。

    router 只做轻量校验（因子存在性），真正的 compute + 合成在 worker 内执行。
    factor_items_json 入库时存请求原样；完成后 run_composition 会再 UPDATE 一次
    为"已解析的 items"（含 version / params_hash），方便复现。
    """
    reg = FactorRegistry()
    reg.scan_and_register()
    # 预检：所有 factor_id 都得存在；否则 worker 内才会发现就已经写了半条 run。
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

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_composition_runs
                (run_id, pool_id, freq, start_date, end_date, method,
                 factor_items_json, n_groups, forward_periods, ic_weight_period,
                 status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
                """,
                (
                    run_id,
                    body.pool_id,
                    body.freq,
                    body.start_date,
                    body.end_date,
                    body.method,
                    json.dumps(items_payload, ensure_ascii=False),
                    body.n_groups,
                    json.dumps(body.forward_periods),
                    body.ic_weight_period,
                    datetime.now(),
                ),
            )
        c.commit()

    bt.add_task(submit, composition_entry, run_id, body.model_dump(mode="json"))
    return ok({"run_id": run_id, "status": "pending"})


@router.get("")
def list_compositions(
    pool_id: int | None = None,
    method: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """列表页：只返回结构化 + 轻量元数据字段，payload / corr 等大字段留给详情页。"""
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, pool_id, freq, start_date, end_date, method, "
        "factor_items_json, n_groups, forward_periods, ic_weight_period, "
        "status, progress, error_message, "
        "ic_mean, ic_ir, long_short_sharpe, long_short_annret, turnover_mean, "
        "created_at, started_at, finished_at "
        "FROM fr_composition_runs WHERE 1=1"
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
    sql += " ORDER BY created_at DESC, run_id DESC LIMIT %s"
    params.append(limit)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []
    # factor_items_json 解析，让前端在列表页就能 chip 渲染因子列表。
    for r in rows:
        raw = r.get("factor_items_json")
        if raw:
            try:
                r["factor_items"] = json.loads(raw)
            except (TypeError, ValueError):
                r["factor_items"] = []
        # forward_periods 是 varchar 存的 JSON array，同样解析回 list。
        fp = r.get("forward_periods")
        if fp:
            try:
                r["forward_periods"] = json.loads(fp)
            except (TypeError, ValueError):
                pass
        r.pop("factor_items_json", None)
    return ok(rows)


@router.get("/{run_id}")
def get_composition(run_id: str) -> dict:
    """返回 run 完整记录 + 解析后的所有 JSON 字段。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_composition_runs WHERE run_id=%s",
                (run_id,),
            )
            run = cur.fetchone()
    if not run:
        raise HTTPException(status_code=404, detail="composition run not found")

    # 五份 JSON 全部 parse；错误容忍：即使 parse 失败也不让整次请求崩。
    for src, dst in [
        ("factor_items_json", "factor_items"),
        ("corr_matrix_json", "corr_matrix"),
        ("per_factor_ic_json", "per_factor_ic"),
        ("weights_json", "weights"),
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
    fp = run.get("forward_periods")
    if fp:
        try:
            run["forward_periods"] = json.loads(fp)
        except (TypeError, ValueError):
            pass
    return ok(run)


@router.get("/{run_id}/status")
def get_composition_status(run_id: str) -> dict:
    """状态端点（轻量轮询用）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, "
                "started_at, finished_at "
                "FROM fr_composition_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="composition run not found")
    return ok(r)


@router.delete("/{run_id}")
def delete_composition(run_id: str) -> dict:
    """硬删一条记录。只一张表，无附加 artifact。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_composition_runs WHERE run_id=%s", (run_id,)
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="composition run not found")
    return ok({"run_id": run_id, "deleted": True})
