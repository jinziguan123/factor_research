"""参数敏感性扫描（MVP）：同步计算，不落库。

用途：写完一个因子、跑过一次评估后，想知道"我挑的这个参数值是不是甜点？
邻域稳定吗？会不会换个值 IC 掉一半？"

与 run_eval 的关系：
- 每个采样点走同一套评估管线（``eval_service.evaluate_factor_panel``），保证
  这里算出的 ic_mean / long_short_sharpe 与正式评估一致，只是不写 MySQL / ClickHouse。
- 数据加载（resolve_pool + close panel）在循环外做一次，每点只重算 factor.compute + metrics。
- 单点失败不中止整批：异常堆栈塞进该点的 error 字段，其余继续扫。
"""

from __future__ import annotations

import logging
import math
import traceback
from typing import Any

import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.runtime.factor_registry import FactorRegistry
from backend.services.eval_service import evaluate_factor_panel
from backend.storage.data_service import DataService

log = logging.getLogger(__name__)


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


def preview(body: dict) -> dict:
    """同步扫 factor 的一个超参数并返回每点评估指标。

    Args:
        body: 已经 Pydantic 校验过的 dict，字段见 ``PreviewParamSensitivityIn``。

    Returns:
        {
            "factor_id", "param_name", "base_params", "default_value",
            "points": [
                {"value", "ic_mean", "rank_ic_mean", "ic_ir", "rank_ic_ir",
                 "long_short_sharpe", "long_short_annret", "turnover_mean",
                 "n_ic_days", "error"}
            ]
        }
        每点都带同样字段，失败点除了 "value" / "error" 外其余为 None。
    """
    factor_id: str = body["factor_id"]
    param_name: str = body["param_name"]
    values: list[float] = list(body["values"])
    pool_id: int = int(body["pool_id"])
    start = pd.to_datetime(body["start_date"])
    end = pd.to_datetime(body["end_date"])
    fwd_periods: list[int] = [int(x) for x in body.get("forward_periods", [1, 5, 10])]
    n_groups: int = int(body.get("n_groups", 5))
    base_params_override: dict[str, Any] | None = body.get("base_params")

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

    # close panel 与参数无关，循环外加载一次复用。因子内部的 ctx.data.load_panel
    # 命中 ClickHouse 内存缓存 → 实际性能损耗可忽略，但显式外提更直白。
    close = data.load_panel(symbols, start.date(), end.date(), field="close", adjust="qfq")

    points: list[dict[str, Any]] = []
    for raw in values:
        coerced = _coerce_value(raw, schema_entry)
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
                points.append(point)
                continue
            # evaluate_factor_panel 期望 F 和 close 在 index/columns 上大致对齐；
            # close 已按池 + 区间加载，F 的 index 就 loc 到 start..end 之间。
            F_aligned = F.loc[(F.index >= start) & (F.index <= end)]
            if F_aligned.empty:
                point["error"] = "factor 宽表在评估区间内无有效行"
                points.append(point)
                continue

            _payload, structured = evaluate_factor_panel(
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
            # n_ic_days：base_period 的 IC 序列有效天数，给前端作为"样本量"提示。
            base_p = fwd_periods[0] if fwd_periods else 1
            ic_obj = _payload.get("ic", {}).get(str(base_p), {}) if _payload else {}
            vals = ic_obj.get("values", []) if isinstance(ic_obj, dict) else []
            point["n_ic_days"] = sum(1 for v in vals if v is not None)
        except Exception:  # pragma: no cover - 记 traceback 交给前端展示
            log.exception(
                "param sensitivity point failed: factor=%s %s=%s",
                factor_id, param_name, coerced,
            )
            point["error"] = traceback.format_exc(limit=4)[-800:]
        points.append(point)

    return {
        "factor_id": factor_id,
        "param_name": param_name,
        "default_value": default_value,
        "base_params": base_params,
        "schema_entry": schema_entry,
        "pool_id": pool_id,
        "start_date": str(start.date()),
        "end_date": str(end.date()),
        "forward_periods": fwd_periods,
        "n_groups": n_groups,
        "points": points,
    }
