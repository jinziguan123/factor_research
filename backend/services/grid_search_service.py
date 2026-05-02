"""多参数栅格搜索（Grid Search）：枚举所有参数组合评估因子表现，找到最优参数。

与 ``param_sensitivity_service`` 的区别：
- param_sensitivity 只扫**一个参数**，产出一维曲线
- grid_search 扫**多个参数**的所有组合，产出 N 维结果矩阵 + 最优组合

指标选择（optimize_by）：
- ``ic_mean``：截面 IC 均值（默认）
- ``rank_ic_mean``：截面 Rank IC 均值
- ``long_short_sharpe``：多空夏普比率
"""
from __future__ import annotations

import itertools
import json
import logging
import traceback
from datetime import datetime
from typing import Any

import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
from backend.services.abort_check import AbortedError, check_abort
from backend.services.metrics import cross_sectional_ic, cross_sectional_rank_ic
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)

# 最大组合数：防炸（3 参数 × 8 值 = 512，够用）
_MAX_COMBINATIONS = 200


def _update_gs_status(
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """更新 fr_param_sensitivity_runs 状态（复用该表）。"""
    from backend.services.run_status import update_run_status

    update_run_status(
        "fr_param_sensitivity_runs", run_id,
        status=status, progress=progress, error=error,
        started=started, finished=finished,
    )


def _coerce_value(raw: Any, schema_entry: dict | None) -> int | float:
    """按 schema 类型转型。"""
    ptype = (schema_entry or {}).get("type", "int")
    if ptype == "float":
        return float(raw)
    return int(raw)


def run_grid_search(run_id: str, body: dict) -> None:
    """执行多参数栅格搜索。

    body 字段：
        factor_id, pool_id, start_date, end_date, n_groups, forward_periods,
        base_params（可选，未扫的参数用此值）
        grid: {param_name: [value, ...]}（至少 2 个参数，每参数 2-8 个值）
        optimize_by: "ic_mean" | "rank_ic_mean" | "long_short_sharpe"
    """
    try:
        _update_gs_status(run_id, status="running", started=True, progress=2)
        check_abort("grid_search", run_id)

        reg = FactorRegistry()
        reg.scan_and_register()
        factor_id: str = body["factor_id"]
        factor = reg.get(factor_id)

        grid: dict[str, list] = body["grid"]
        if len(grid) < 1:
            raise ValueError("grid 至少需要 1 个参数")
        param_names = list(grid.keys())
        values_lists = [grid[p] for p in param_names]

        # 计算组合数
        n_combos = 1
        for vl in values_lists:
            n_combos *= len(vl)
        if n_combos > _MAX_COMBINATIONS:
            raise ValueError(
                f"组合数 {n_combos} 超过上限 {_MAX_COMBINATIONS}，请缩小扫描范围"
            )

        optimize_by = body.get("optimize_by", "ic_mean")
        pool_id = int(body["pool_id"])
        start = pd.to_datetime(body["start_date"])
        end = pd.to_datetime(body["end_date"])
        fwd_periods = [int(x) for x in body.get("forward_periods", [1])]
        n_groups = int(body.get("n_groups", 5))
        base_params = dict(factor.default_params or {})
        if body.get("base_params"):
            base_params.update(body["base_params"])

        data = DataService()
        symbols = data.resolve_pool(pool_id)
        if len(symbols) < n_groups:
            raise ValueError(f"股票池仅 {len(symbols)} 只，< n_groups={n_groups}")

        # 预加载 close（所有组合共用）
        warmup = factor.required_warmup(base_params)
        data_start = (start - pd.Timedelta(days=warmup)).date()
        close = data.load_panel(
            symbols, data_start, end.date(), field="close", adjust="qfq",
        )
        if close.empty:
            raise ValueError("close 数据为空")

        # 枚举所有组合
        all_combos = list(itertools.product(*values_lists))
        results: list[dict] = []
        base_period = fwd_periods[0] if fwd_periods else 1

        for idx, combo in enumerate(all_combos):
            check_abort("grid_search", run_id)
            params = dict(base_params)
            combo_dict: dict[str, Any] = {}
            for pname, pval in zip(param_names, combo):
                schema_entry = (factor.params_schema or {}).get(pname)
                params[pname] = _coerce_value(pval, schema_entry)
                combo_dict[pname] = params[pname]

            try:
                warmup_i = factor.required_warmup(params)
                data_start_i = (start - pd.Timedelta(days=warmup_i)).date()
                ctx = FactorContext(
                    data=data, symbols=symbols,
                    start_date=pd.Timestamp(data_start_i),
                    end_date=end, warmup_days=warmup_i,
                )
                F = factor.compute(ctx, params)
                F_aligned, C_aligned = F.align(close, join="inner")
                if F_aligned.empty or len(F_aligned.columns) < n_groups:
                    results.append({"params": combo_dict, "error": "有效样本不足"})
                    continue

                fwd_ret = C_aligned.shift(-base_period) / C_aligned - 1
                ic_series = cross_sectional_ic(F_aligned, fwd_ret)
                rank_ic_series = cross_sectional_rank_ic(F_aligned, fwd_ret)

                point: dict[str, Any] = {
                    "params": combo_dict,
                    "ic_mean": float(ic_series.mean()) if not ic_series.empty else 0.0,
                    "rank_ic_mean": float(rank_ic_series.mean()) if not rank_ic_series.empty else 0.0,
                    "ic_ir": float(ic_series.mean() / (ic_series.std(ddof=1) or 1e-12))
                    if not ic_series.empty and len(ic_series) > 1 else 0.0,
                    "ic_win_rate": float((ic_series > 0).mean()) if not ic_series.empty else 0.0,
                    "n_dates": len(ic_series),
                }
                results.append(point)
            except Exception as e:
                results.append({"params": combo_dict, "error": str(e)[:200]})

            progress = 10 + int(80 * (idx + 1) / len(all_combos))
            _update_gs_status(run_id, progress=min(progress, 90))

        # 找最优组合（按 optimize_by 降序）
        valid = [r for r in results if "error" not in r]
        if optimize_by == "long_short_sharpe":
            valid.sort(key=lambda x: x.get("ic_ir", 0), reverse=True)
        elif optimize_by == "rank_ic_mean":
            valid.sort(key=lambda x: x.get("rank_ic_mean", 0), reverse=True)
        else:
            valid.sort(key=lambda x: x.get("ic_mean", 0), reverse=True)

        best = valid[0] if valid else None

        # 构建 2D 热力图数据（如果刚好 2 个参数）
        heatmap: dict | None = None
        if len(param_names) == 2 and valid:
            p1_vals = sorted(set(r["params"][param_names[0]] for r in valid))
            p2_vals = sorted(set(r["params"][param_names[1]] for r in valid))
            matrix = []
            for r in valid:
                matrix.append({
                    "x": r["params"][param_names[0]],
                    "y": r["params"][param_names[1]],
                    "value": r.get(optimize_by, 0),
                })
            heatmap = {
                "x_param": param_names[0],
                "y_param": param_names[1],
                "x_values": [float(v) for v in p1_vals],
                "y_values": [float(v) for v in p2_vals],
                "matrix": matrix,
            }

        payload = {
            "factor_id": factor_id,
            "optimize_by": optimize_by,
            "param_names": param_names,
            "n_combinations": len(all_combos),
            "n_valid": len(valid),
            "best": best,
            "heatmap": heatmap,
            "results": results,
        }

        with mysql_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    REPLACE INTO fr_param_sensitivity_runs
                    (run_id, factor_id, param_name, status, progress,
                     started_at, finished_at, points_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id, factor_id,
                        "×".join(param_names),
                        "success", 100,
                        datetime.now(), datetime.now(),
                        json.dumps(payload, ensure_ascii=False, allow_nan=False),
                    ),
                )
            c.commit()
        _update_gs_status(run_id, status="success", progress=100, finished=True)

    except AbortedError:
        log.info("grid_search aborted: run_id=%s", run_id)
        try:
            _update_gs_status(run_id, status="aborted", finished=True)
        except Exception:
            log.exception("gs _update_status aborted failed: run_id=%s", run_id)
    except Exception:
        log.exception("grid_search failed: run_id=%s", run_id)
        try:
            _update_gs_status(
                run_id, status="failed",
                error=traceback.format_exc()[:4000], finished=True,
            )
        except Exception:
            log.exception("gs _update_status failed: run_id=%s", run_id)
