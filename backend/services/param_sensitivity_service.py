"""参数敏感性扫描（异步持久化）。

一次 ``run_param_sensitivity`` 扫同一因子的一个超参数在 N 个取值下的评估指标，
每点走一次 ``evaluate_factor_panel``，结果汇总入 ``fr_param_sensitivity_runs.points_json``。

设计取舍：
- close panel 与参数无关，循环外加载一次复用；因子内部 ctx.data.load_panel
  命中 ClickHouse 内存缓存，实际每点只重算 factor.compute + metrics。
- 单点失败不中止整批：异常堆栈塞进该点的 error 字段，其余继续扫——
  研究场景经常会有"某个极端值让 factor.compute 除零"这种边缘案例，
  整批 fail 会浪费前面算好的点。
- status / progress / abort 三件套和 cost_sensitivity 完全同构：每点前 check_abort，
  进度按点线性 0→100；相同的前端列表/详情/中断 UX 可以直接复用。
"""
from __future__ import annotations

import json
import logging
import math
import traceback
from datetime import datetime
from typing import Any

import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
from backend.services.abort_check import AbortedError, check_abort
from backend.services.eval_service import evaluate_factor_panel
from backend.storage.data_service import DataService
from backend.storage.mysql_client import mysql_conn

log = logging.getLogger(__name__)


def _update_status(
    run_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    points_json: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """更新 fr_param_sensitivity_runs 的状态列（与 cost_sensitivity._update_status 同构）。"""
    sets: list[str] = []
    vals: list[Any] = []
    if status is not None:
        sets.append("status=%s")
        vals.append(status)
    if progress is not None:
        sets.append("progress=%s")
        vals.append(progress)
    if error is not None:
        sets.append("error_message=%s")
        vals.append(error)
    if points_json is not None:
        sets.append("points_json=%s")
        vals.append(points_json)
    if started:
        sets.append("started_at=%s")
        vals.append(datetime.now())
    if finished:
        sets.append("finished_at=%s")
        vals.append(datetime.now())
    if not sets:
        return
    vals.append(run_id)
    with mysql_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE fr_param_sensitivity_runs SET {','.join(sets)} WHERE run_id=%s",
                vals,
            )
        c.commit()


def _coerce_value(raw: float, schema_entry: dict[str, Any] | None) -> float | int:
    """按 params_schema 声明的类型把前端传来的 number 规范成 int / float。

    前端 JSON 数字统一是 float，直接喂给因子里 ``int(params[x])`` 写法没问题，
    但透出给前端展示时要还原"原本的类型"避免显示 "60.0"。
    schema 缺失 / type 为未知字符串时按 float 处理，保持宽松。
    """
    if not schema_entry:
        return float(raw)
    t = str(schema_entry.get("type", "")).lower()
    if t == "int":
        return int(round(raw))
    return float(raw)


def _fmt(v: Any) -> Any:
    """把 NaN / inf 规整成 None，JSON 不炸；非数值直接返回。"""
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            return None
    return v


def _compute_point(
    factor,
    data: DataService,
    symbols: list[str],
    close: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    base_params: dict[str, Any],
    param_name: str,
    coerced: float | int,
    fwd_periods: list[int],
    n_groups: int,
) -> dict[str, Any]:
    """对单个参数取值跑一次完整评估，返回结构化指标 dict。

    所有失败路径都回结构化 dict（error 字段非空），不抛异常——调用方要的是
    "每个点对应一条记录"，即使个别点失败也别让整批掉。
    """
    point: dict[str, Any] = {
        "value": coerced,
        "ic_mean": None,
        "rank_ic_mean": None,
        "ic_ir": None,
        "rank_ic_ir": None,
        "long_short_sharpe": None,
        "long_short_annret": None,
        "turnover_mean": None,
        "n_ic_days": None,
        "error": None,
    }
    try:
        params = {**base_params, param_name: coerced}
        warmup = factor.required_warmup(params)
        ctx = FactorContext(
            data=data,
            symbols=symbols,
            start_date=start,
            end_date=end,
            warmup_days=warmup,
        )
        F = factor.compute(ctx, params)
        if F is None or F.empty:
            point["error"] = "factor.compute 返回空宽表"
            return point
        F_aligned = F.loc[(F.index >= start) & (F.index <= end)]
        if F_aligned.empty:
            point["error"] = "factor 宽表在评估区间内无有效行"
            return point

        payload, structured = evaluate_factor_panel(
            F_aligned,
            close,
            forward_periods=fwd_periods,
            n_groups=n_groups,
            split_date=None,
        )
        point["ic_mean"] = _fmt(structured.get("ic_mean"))
        point["rank_ic_mean"] = _fmt(structured.get("rank_ic_mean"))
        point["ic_ir"] = _fmt(structured.get("ic_ir"))
        point["rank_ic_ir"] = _fmt(structured.get("rank_ic_ir"))
        point["long_short_sharpe"] = _fmt(structured.get("long_short_sharpe"))
        point["long_short_annret"] = _fmt(structured.get("long_short_annret"))
        point["turnover_mean"] = _fmt(structured.get("turnover_mean"))
        base_p = fwd_periods[0] if fwd_periods else 1
        ic_obj = payload.get("ic", {}).get(str(base_p), {}) if payload else {}
        vals = ic_obj.get("values", []) if isinstance(ic_obj, dict) else []
        point["n_ic_days"] = sum(1 for v in vals if v is not None)
    except Exception:
        log.exception(
            "param sensitivity point failed: factor=%s %s=%s",
            factor.factor_id, param_name, coerced,
        )
        point["error"] = traceback.format_exc(limit=4)[-800:]
    return point


def run_param_sensitivity(run_id: str, body: dict) -> None:
    """执行一次参数敏感性扫描。

    Args:
        run_id: ``fr_param_sensitivity_runs.run_id``，API 层 INSERT 时生成。
        body: 请求体 dict（字段见 ``CreateParamSensitivityIn``），包括 factor_id /
            param_name / values / pool_id / start_date / end_date / n_groups /
            forward_periods / base_params。

    副作用：
        - 更新 fr_param_sensitivity_runs 的 status / progress / started_at /
          finished_at / error_message / points_json。
    """
    try:
        _update_status(run_id, status="running", started=True, progress=2)
        check_abort("param_sensitivity", run_id)

        factor_id: str = body["factor_id"]
        param_name: str = body["param_name"]
        raw_values = list(body.get("values") or [])
        pool_id: int = int(body["pool_id"])
        start = pd.to_datetime(body["start_date"])
        end = pd.to_datetime(body["end_date"])
        fwd_periods: list[int] = [
            int(x) for x in (body.get("forward_periods") or [1, 5, 10])
        ]
        n_groups: int = int(body.get("n_groups") or 5)
        base_params_override: dict[str, Any] | None = body.get("base_params")

        # 去重 + 保持升序（API schema 层已保证 >=2 个且有序，这里兜一次）。
        seen: set[float] = set()
        unique_raw: list[float] = []
        for v in raw_values:
            fv = float(v)
            if fv in seen:
                continue
            seen.add(fv)
            unique_raw.append(fv)
        if len(unique_raw) < 2:
            raise ValueError("values 至少需要 2 个不同的扫描点")

        reg = FactorRegistry()
        reg.scan_and_register()
        factor = reg.get(factor_id)
        schema = factor.params_schema or {}
        if param_name not in schema and param_name not in (factor.default_params or {}):
            raise ValueError(
                f"参数 {param_name} 不在 factor {factor_id} 的 params_schema / default_params 中，"
                f"可用：{list(schema.keys()) or list((factor.default_params or {}).keys())}"
            )
        schema_entry = schema.get(param_name)

        base_params: dict[str, Any] = dict(factor.default_params or {})
        if base_params_override:
            base_params.update(base_params_override)
        default_value = base_params.get(param_name)

        data = DataService()
        symbols = data.resolve_pool(pool_id)
        if len(symbols) < n_groups:
            raise ValueError(
                f"股票池 pool_id={pool_id} 仅含 {len(symbols)} 只股票，小于 n_groups={n_groups}，"
                f"无法计算横截面 IC / 分组指标。"
            )

        # close panel 只取一次；循环里所有点共用。
        close = data.load_panel(
            symbols, start.date(), end.date(), field="close", adjust="qfq"
        )
        _update_status(run_id, progress=10)

        # 进度线性分配：10 → 95（留 5% 给落库）。
        total = len(unique_raw)
        points: list[dict[str, Any]] = []
        for idx, raw in enumerate(unique_raw):
            check_abort("param_sensitivity", run_id)  # 每点前一次
            coerced = _coerce_value(raw, schema_entry)
            pt = _compute_point(
                factor=factor,
                data=data,
                symbols=symbols,
                close=close,
                start=start,
                end=end,
                base_params=base_params,
                param_name=param_name,
                coerced=coerced,
                fwd_periods=fwd_periods,
                n_groups=n_groups,
            )
            points.append(pt)
            prog = 10 + int(85 * (idx + 1) / total)
            _update_status(run_id, progress=min(prog, 95))

        result = {
            "points": points,
            "default_value": default_value,
            "schema_entry": schema_entry,
        }
        points_json = json.dumps(result, ensure_ascii=False, allow_nan=False)
        _update_status(
            run_id,
            status="success",
            progress=100,
            points_json=points_json,
            finished=True,
        )
    except AbortedError as exc:
        # 主动中断：落 aborted；已经算完的点不回写——要历史就重跑并缩小 values。
        log.info("param_sensitivity aborted: run_id=%s reason=%s", run_id, exc)
        try:
            _update_status(run_id, status="aborted", finished=True)
        except Exception:
            log.exception("_update_status 落 aborted 失败: run_id=%s", run_id)
    except Exception:
        log.exception("param_sensitivity failed: run_id=%s", run_id)
        try:
            _update_status(
                run_id,
                status="failed",
                error=traceback.format_exc()[:4000],
                finished=True,
            )
        except Exception:
            log.exception(
                "_update_status 记录失败时自身也抛异常: run_id=%s", run_id
            )
