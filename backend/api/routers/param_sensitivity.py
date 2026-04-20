"""参数敏感性（MVP）：单一同步接口，无持久化、无列表。

``POST /api/param-sensitivity/preview``：扫一个因子的某个超参数在 N 个取值下
的评估指标（IC / Rank IC / 多空 Sharpe 等），同步返回。

为何不落库：研究场景通常"扫一次得结论"，历史回溯价值远低于 eval / backtest；
等 MVP 验证确实需要回翻再升级成 runs 表。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.api.schemas import PreviewParamSensitivityIn, ok
from backend.services import param_sensitivity_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/param-sensitivity", tags=["param-sensitivity"])


@router.post("/preview")
def preview_param_sensitivity(body: PreviewParamSensitivityIn) -> dict:
    """同步扫描 factor_id 的单个 param_name 在 values 中各点的评估指标。

    性能：每点 = 一次 factor.compute + 一次 evaluate_factor_panel。典型日频 5 年、
    池 ~500 只股票、扫 5 个点 ≈ 1-3 分钟。前端应设 5 分钟以上的 timeout。
    """
    try:
        data = param_sensitivity_service.preview(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"factor not found: {exc}")
    return ok(data)
