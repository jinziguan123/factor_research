"""缠论分析端点。

``GET /api/chanlun/analyze``：对单只股票做缠论结构识别（分型 / 笔 / 中枢 / 买卖点），
返回的时间字符串与 K 线 x 轴类目直接对齐，前端拿到即可叠加渲染。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.services import chanlun_service

router = APIRouter(prefix="/api/chanlun", tags=["chanlun"])


@router.get("/analyze")
def analyze(
    symbol: str,
    start: date,
    end: date,
    freq: str = "1d",
    adjust: str = "qfq",
) -> dict:
    if freq not in ("1d", "1m"):
        raise HTTPException(status_code=400, detail="freq must be 1d or 1m")
    if adjust not in ("qfq", "none"):
        raise HTTPException(status_code=400, detail="invalid adjust")
    if start > end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    result = chanlun_service.analyze(symbol, start, end, freq=freq, adjust=adjust)
    return ok(chanlun_service.to_dict(result))
