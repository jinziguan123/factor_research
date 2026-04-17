"""成本敏感性分析的 CRUD + 触发异步任务。

结构对齐 evals.py / backtests.py：
- ``POST /api/cost-sensitivity``：建 run 记录 + 派发 worker。
- ``GET  /api/cost-sensitivity``：列表页（倒序 + 可选 factor_id / status 过滤）。
- ``GET  /api/cost-sensitivity/{run_id}``：详情，含 points_json 解析后的 points 数组。
- ``GET  /api/cost-sensitivity/{run_id}/status``：状态轮询轻量端点。
- ``DELETE /api/cost-sensitivity/{run_id}``：硬删一条记录。

points_json 存储格式：{"points": [{cost_bps, annual_return, sharpe_ratio, ...}, ...]}。
前端读到后直接渲染成敏感曲线 + 表格。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.api.schemas import CreateCostSensitivityIn, ok
from backend.runtime.entries import cost_sensitivity_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.services.params_hash import params_hash
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/cost-sensitivity", tags=["cost-sensitivity"])


@router.post("")
def create_cost_sensitivity(
    body: CreateCostSensitivityIn, bt: BackgroundTasks
) -> dict:
    """创建成本敏感性分析任务并派发到 ProcessPool。"""
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
                INSERT INTO fr_cost_sensitivity_runs
                (run_id, factor_id, factor_version, params_hash, params_json,
                 pool_id, freq, start_date, end_date,
                 n_groups, rebalance_period, position, init_cash,
                 cost_bps_list, status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
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
                    body.n_groups,
                    body.rebalance_period,
                    body.position,
                    body.init_cash,
                    json.dumps(body.cost_bps_list),
                    datetime.now(),
                ),
            )
        c.commit()

    bt.add_task(
        submit, cost_sensitivity_entry, run_id, body.model_dump(mode="json")
    )
    return ok({"run_id": run_id, "status": "pending"})


@router.get("")
def list_cost_sensitivity(
    factor_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """列表页：不返回 ``params_json`` / ``points_json``（后者可能几十 KB）。"""
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, factor_id, factor_version, params_hash, pool_id, freq, "
        "start_date, end_date, n_groups, rebalance_period, position, init_cash, "
        "cost_bps_list, status, progress, error_message, "
        "created_at, started_at, finished_at "
        "FROM fr_cost_sensitivity_runs WHERE 1=1"
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
            rows = cur.fetchall() or []
    # cost_bps_list 是 JSON 字符串，列表页渲染为 chip 数组比字符串好看。
    for r in rows:
        raw = r.get("cost_bps_list")
        if raw:
            try:
                r["cost_bps_list"] = json.loads(raw)
            except (TypeError, ValueError):
                pass
    return ok(rows)


@router.get("/{run_id}")
def get_cost_sensitivity(run_id: str) -> dict:
    """返回 run 完整记录 + 解析后的 points 数组。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_cost_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            run = cur.fetchone()
    if not run:
        raise HTTPException(
            status_code=404, detail="cost-sensitivity run not found"
        )

    # cost_bps_list / points_json 都还原为结构化字段；points_json 的顶层是
    # {"points": [...]}，直接把 points 列出让前端零解析成本。
    raw_list = run.get("cost_bps_list")
    if raw_list:
        try:
            run["cost_bps_list"] = json.loads(raw_list)
        except (TypeError, ValueError):
            pass
    raw_points = run.pop("points_json", None)
    if raw_points:
        try:
            parsed = json.loads(raw_points)
            run["points"] = parsed.get("points") if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            run["points"] = None
    else:
        run["points"] = None
    return ok(run)


@router.get("/{run_id}/status")
def get_cost_sensitivity_status(run_id: str) -> dict:
    """状态端点（轻量轮询用）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, "
                "started_at, finished_at "
                "FROM fr_cost_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(
            status_code=404, detail="cost-sensitivity run not found"
        )
    return ok(r)


@router.delete("/{run_id}")
def delete_cost_sensitivity(run_id: str) -> dict:
    """硬删一条记录。只一张表，没有附加 artifact 要清。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_cost_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(
            status_code=404, detail="cost-sensitivity run not found"
        )
    return ok({"run_id": run_id, "deleted": True})
