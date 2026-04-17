"""因子评估 run 的 CRUD + 触发异步任务。

- ``POST /api/evals``：
  1. 校验 factor 已注册（``reg.get`` 抛 KeyError → 400）；
  2. 固化 ``factor_version`` 来自 DB（不是进程内 cache），避免热加载导致错版本；
  3. ``INSERT fr_factor_eval_runs status='pending'``；
  4. ``BackgroundTasks.add_task(submit, eval_entry, ...)``：请求不等 worker 完成。
- ``GET /api/evals``：列表（支持 factor_id / status 过滤 + limit）。
- ``GET /api/evals/{run_id}``：详情（含 metrics payload）。
- ``GET /api/evals/{run_id}/status``：轻量状态端点，前端轮询用。
- ``DELETE /api/evals/{run_id}``：硬删 run + metrics 两行。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.api.schemas import CreateEvalIn, ok
from backend.runtime.entries import eval_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.services.params_hash import params_hash
from backend.storage.mysql_client import mysql_conn

router = APIRouter(prefix="/api/evals", tags=["evals"])


@router.post("")
def create_eval(body: CreateEvalIn, bt: BackgroundTasks) -> dict:
    """创建一个评估任务并派发到 ProcessPool。"""
    reg = FactorRegistry()
    # 集成测试 / 正常启动都会扫；这里再扫一次保证"刚热部署上来的因子"能立刻用。
    reg.scan_and_register()
    try:
        factor = reg.get(body.factor_id)
    except KeyError:
        raise HTTPException(status_code=400, detail="factor not found")

    # 优先用 DB 最新 version（跨进程口径一致）；若 fr_factor_meta 出问题，KeyError
    # 说明元数据写入异常，属基础设施问题 → 交给 500 handler 暴露。
    version = reg.latest_version_from_db(body.factor_id)
    params = body.params or factor.default_params
    phash = params_hash(params)
    run_id = uuid.uuid4().hex

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_factor_eval_runs
                (run_id, factor_id, factor_version, params_hash, params_json,
                 pool_id, freq, start_date, end_date, forward_periods, n_groups,
                 split_date, status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
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
                    ",".join(str(x) for x in body.forward_periods),
                    body.n_groups,
                    body.split_date,
                    # 本地时区 now()，与 eval_service 内更新 started_at / finished_at 的
                    # 语义一致；避免 UTC 导致前端展示 -8h。
                    datetime.now(),
                ),
            )
        c.commit()

    # model_dump(mode="json") 把 date 转 ISO 字符串，worker 侧用 pd.to_datetime 能吃。
    bt.add_task(submit, eval_entry, run_id, body.model_dump(mode="json"))
    return ok({"run_id": run_id, "status": "pending"})


@router.get("")
def list_evals(
    factor_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """列出评估任务（倒序 + 可选筛）。

    limit 上限由前端约束；后端再用 ``min(limit, 500)`` 硬性兜底，避免扫表过大。

    返回字段只包含列表页需要的列（不返回 ``params_json`` / ``payload_json``）：
    - ``params_json`` 每行几百 B ~ 几 KB，500 条就是几 MB 白跑；
    - 需要完整参数 / 指标时走 ``GET /api/evals/{run_id}`` 详情端点。
    """
    # 硬兜底：前端 bug 或 curl 误传都不会拖垮库。
    limit = max(1, min(int(limit), 500))
    # 列表页只展示 run_id / 因子 / 状态 / 池 / 区间 / 时间；error_message 保留
    # 供"失败任务"的 tooltip 展示（通常几十字节，可接受）。
    sql = (
        "SELECT run_id, factor_id, factor_version, params_hash, pool_id, freq, "
        "start_date, end_date, status, progress, error_message, "
        "created_at, started_at, finished_at "
        "FROM fr_factor_eval_runs WHERE 1=1"
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
def get_eval(run_id: str) -> dict:
    """返回 run 完整记录 + metrics payload（JSON 解析后放 ``metrics.payload`` 字段）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_factor_eval_runs WHERE run_id=%s", (run_id,)
            )
            run = cur.fetchone()
            if not run:
                raise HTTPException(status_code=404, detail="eval run not found")
            cur.execute(
                "SELECT * FROM fr_factor_eval_metrics WHERE run_id=%s", (run_id,)
            )
            m = cur.fetchone()
    if m and m.get("payload_json"):
        # 把序列化的 payload 展开给前端，原始 JSON 字符串不再透传。
        try:
            m["payload"] = json.loads(m.pop("payload_json"))
        except (ValueError, TypeError):
            # 兜底：payload_json 不合法仍保留原字段，前端自己处理。
            m["payload"] = None
    run["metrics"] = m
    return ok(run)


@router.get("/{run_id}/status")
def get_eval_status(run_id: str) -> dict:
    """轻量状态端点，前端轮询（每 1-2s）只需要少量字段，避免把 payload_json 也搬运。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, "
                "started_at, finished_at "
                "FROM fr_factor_eval_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="eval run not found")
    return ok(r)


@router.delete("/{run_id}")
def delete_eval(run_id: str) -> dict:
    """硬删 run + metrics。不取消已派发的 worker（``BackgroundTasks`` 无取消句柄）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_factor_eval_metrics WHERE run_id=%s", (run_id,)
            )
            cur.execute(
                "DELETE FROM fr_factor_eval_runs WHERE run_id=%s", (run_id,)
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="eval run not found")
    return ok({"run_id": run_id, "deleted": True})
