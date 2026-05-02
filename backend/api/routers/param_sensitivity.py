"""参数敏感性扫描 API：CRUD + 触发异步任务。

结构对齐 cost_sensitivity.py：
- ``POST /api/param-sensitivity``：建 run 记录 + 派发 worker。
- ``GET  /api/param-sensitivity``：列表页（倒序 + 可选 factor_id / status 过滤）。
- ``GET  /api/param-sensitivity/{run_id}``：详情，含 points_json 解析后的字段。
- ``GET  /api/param-sensitivity/{run_id}/status``：状态轮询轻量端点。
- ``POST /api/param-sensitivity/{run_id}/abort``：协作式中断。
- ``DELETE /api/param-sensitivity/{run_id}``：硬删一条记录。

points_json 存储格式：
    {"points": [{value, ic_mean, ...}, ...],
     "default_value": ..., "schema_entry": {...}}
前端详情页直接把 points 画曲线、拿 default_value 标注默认位。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.api.schemas import CreateParamSensitivityIn, ok
from backend.runtime.entries import param_sensitivity_entry
from backend.runtime.factor_registry import FactorRegistry
from backend.runtime.task_pool import submit
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/param-sensitivity", tags=["param-sensitivity"])


@router.post("")
def create_param_sensitivity(
    body: CreateParamSensitivityIn, bt: BackgroundTasks
) -> dict:
    """创建参数敏感性扫描任务并派发到 ProcessPool。"""
    reg = FactorRegistry()
    reg.scan_and_register()
    try:
        factor = reg.get(body.factor_id)
    except KeyError:
        raise HTTPException(status_code=400, detail="factor not found")

    # 路由层就近校验 param_name 合法性：前端 schema 下拉能兜一层，但
    # curl / 脚本直连时还是要挡，避免任务跑起来 worker 里 raise，落 failed。
    schema = factor.params_schema or {}
    default_params = factor.default_params or {}
    if body.param_name not in schema and body.param_name not in default_params:
        raise HTTPException(
            status_code=400,
            detail=(
                f"参数 {body.param_name} 不在 factor {body.factor_id} 的 "
                f"params_schema / default_params 中"
            ),
        )

    version = reg.latest_version_from_db(body.factor_id)
    # 去重 + 升序，入库更直观，service 那里也只跑唯一值。
    unique_values = sorted({float(v) for v in body.values})
    run_id = uuid.uuid4().hex

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fr_param_sensitivity_runs
                (run_id, factor_id, factor_version, param_name, values_json,
                 base_params_json, pool_id, freq, start_date, end_date,
                 n_groups, forward_periods,
                 status, progress, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',0,%s)
                """,
                (
                    run_id,
                    body.factor_id,
                    version,
                    body.param_name,
                    json.dumps(unique_values),
                    json.dumps(body.base_params, ensure_ascii=False)
                    if body.base_params is not None
                    else None,
                    body.pool_id,
                    body.freq,
                    body.start_date,
                    body.end_date,
                    body.n_groups,
                    json.dumps(body.forward_periods),
                    datetime.now(),
                ),
            )
        c.commit()

    # 用 unique_values 覆盖 body.values，避免 worker 再去重一遍（冗余但无害）。
    task_body = body.model_dump(mode="json")
    task_body["values"] = unique_values
    bt.add_task(submit, param_sensitivity_entry, run_id, task_body)
    return ok({"run_id": run_id, "status": "pending"})


@router.get("")
def list_param_sensitivity(
    factor_id: str | None = None,
    status: str | None = None,
    param_name: str | None = None,
    limit: int = 50,
) -> dict:
    """列表页：不返回 points_json / base_params_json（后者可能几 KB，列表无需）。"""
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT run_id, factor_id, factor_version, param_name, values_json, "
        "pool_id, freq, start_date, end_date, n_groups, forward_periods, "
        "status, progress, error_message, "
        "created_at, started_at, finished_at "
        "FROM fr_param_sensitivity_runs WHERE 1=1"
    )
    params: list = []
    if factor_id:
        sql += " AND factor_id=%s"
        params.append(factor_id)
    if status:
        sql += " AND status=%s"
        params.append(status)
    if param_name:
        sql += " AND param_name=%s"
        params.append(param_name)
    sql += " ORDER BY created_at DESC, run_id DESC LIMIT %s"
    params.append(limit)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []
    # 字符串的 JSON 列统一还原结构化，前端零解析。
    for r in rows:
        _decode_json_inplace(r, "values_json", "values")
        _decode_json_inplace(r, "forward_periods", "forward_periods")
    return ok(rows)


@router.get("/{run_id}")
def get_param_sensitivity(run_id: str) -> dict:
    """返回 run 完整记录 + 解析后的 points / default_value / schema_entry。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM fr_param_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            run = cur.fetchone()
    if not run:
        raise HTTPException(
            status_code=404, detail="param-sensitivity run not found"
        )

    _decode_json_inplace(run, "values_json", "values")
    _decode_json_inplace(run, "forward_periods", "forward_periods")
    raw_base = run.pop("base_params_json", None)
    if raw_base:
        try:
            run["base_params"] = json.loads(raw_base)
        except (TypeError, ValueError):
            run["base_params"] = None
    else:
        run["base_params"] = None

    raw_points = run.pop("points_json", None)
    if raw_points:
        try:
            parsed = json.loads(raw_points)
            if isinstance(parsed, dict):
                # 栅格搜索：payload 含 results / best 字段
                if "results" in parsed:
                    run["is_grid_search"] = True
                    run["results"] = parsed.get("results")
                    run["best"] = parsed.get("best")
                    run["heatmap"] = parsed.get("heatmap")
                    run["optimize_by"] = parsed.get("optimize_by")
                    run["points"] = None
                    run["default_value"] = None
                    run["schema_entry"] = None
                else:
                    run["is_grid_search"] = False
                    run["points"] = parsed.get("points")
                    run["default_value"] = parsed.get("default_value")
                    run["schema_entry"] = parsed.get("schema_entry")
            else:
                run["is_grid_search"] = False
                run["points"] = None
                run["default_value"] = None
                run["schema_entry"] = None
        except (TypeError, ValueError):
            run["is_grid_search"] = False
            run["points"] = None
            run["default_value"] = None
            run["schema_entry"] = None
    else:
        run["is_grid_search"] = False
        run["points"] = None
        run["default_value"] = None
        run["schema_entry"] = None
    return ok(run)


@router.get("/{run_id}/status")
def get_param_sensitivity_status(run_id: str) -> dict:
    """状态端点（轻量轮询用）。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT run_id, status, progress, error_message, "
                "started_at, finished_at "
                "FROM fr_param_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            r = cur.fetchone()
    if not r:
        raise HTTPException(
            status_code=404, detail="param-sensitivity run not found"
        )
    return ok(r)


@router.post("/{run_id}/abort")
def abort_param_sensitivity(run_id: str) -> dict:
    """请求中断一个排队 / 运行中的参数敏感性扫描任务。

    协作式：UPDATE status='aborting'，worker 每个点前 check_abort 会看到并抛异常。
    最坏等一个点（~30-60s）。
    """
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE fr_param_sensitivity_runs SET status='aborting' "
                "WHERE run_id=%s AND status IN ('pending','running')",
                (run_id,),
            )
            changed = cur.rowcount
            cur.execute(
                "SELECT status FROM fr_param_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            row = cur.fetchone()
        c.commit()
    if row is None:
        raise HTTPException(
            status_code=404, detail="param-sensitivity run not found"
        )
    current_status = row["status"]
    if changed == 0 and current_status != "aborting":
        raise HTTPException(
            status_code=409,
            detail=f"cannot abort: current status is '{current_status}'",
        )
    return ok({"run_id": run_id, "status": current_status})


@router.delete("/{run_id}")
def delete_param_sensitivity(run_id: str) -> dict:
    """硬删一条记录。没有附加 artifact。"""
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM fr_param_sensitivity_runs WHERE run_id=%s",
                (run_id,),
            )
            deleted = cur.rowcount
        c.commit()
    if deleted == 0:
        raise HTTPException(
            status_code=404, detail="param-sensitivity run not found"
        )
    return ok({"run_id": run_id, "deleted": True})


@router.post("/{run_id}/apply-best")
def apply_best_params(run_id: str) -> dict:
    """将栅格搜索的最优参数应用为因子默认参数。

    从 points_json 里读取 best.params，覆写因子源码中的 default_params 行，
    然后 reload 模块 + 重置进程池。
    """
    import ast
    import inspect
    import re

    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT factor_id, points_json FROM fr_param_sensitivity_runs "
                "WHERE run_id=%s",
                (run_id,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    if not row.get("points_json"):
        raise HTTPException(status_code=400, detail="该 run 尚未产生结果")

    try:
        parsed = json.loads(row["points_json"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="points_json 解析失败")

    best = parsed.get("best") if isinstance(parsed, dict) else None
    if not best or not best.get("params"):
        raise HTTPException(status_code=400, detail="该 run 没有 best 参数组合")

    new_params = best["params"]
    factor_id = row["factor_id"]

    # 定位因子源码文件
    from backend.api.routers.factors import _require_factor_file
    from backend.runtime.factor_registry import FactorRegistry

    reg = FactorRegistry()
    reg.scan_and_register()
    p = _require_factor_file(factor_id, reg)
    code = p.read_text(encoding="utf-8")

    # 用正则替换 default_params = {...} 行
    params_str = json.dumps(new_params, ensure_ascii=False)
    new_line = f"    default_params: dict = {params_str}"
    code, n = re.subn(
        r"^\s*default_params\s*[:=]\s*\{[^}]*\}",
        new_line,
        code,
        flags=re.MULTILINE,
    )
    if n == 0:
        raise HTTPException(
            status_code=500,
            detail="未在源码中找到 default_params = {...} 赋值，无法自动更新",
        )

    # 写回
    p.write_text(code, encoding="utf-8")
    mod = inspect.getmodule(reg.get(factor_id).__class__)
    if mod is not None:
        reg.reload_module(mod.__name__)
    from backend.runtime.task_pool import reset_pool

    reset_pool()

    logger.info(
        "apply_best_params: factor_id=%s new_params=%s",
        factor_id, params_str,
    )
    return ok({
        "factor_id": factor_id,
        "new_default_params": new_params,
        "version": reg.current_version(factor_id),
    })


def _decode_json_inplace(row: dict, src_key: str, dst_key: str) -> None:
    """把 row[src_key] 里的 JSON 字符串解码塞到 row[dst_key]，失败就保留原值。

    抽出来是因为 values_json / forward_periods 两处都要做一样的事，
    还要在 list 和 detail 两个端点调用，inline 会很吵。
    """
    raw = row.pop(src_key, None)
    if raw is None:
        row[dst_key] = None
        return
    try:
        row[dst_key] = json.loads(raw)
    except (TypeError, ValueError):
        row[dst_key] = raw
