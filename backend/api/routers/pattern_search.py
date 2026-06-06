"""图形相似度检索端点。

- POST /api/pattern_search/by_stock：需求2，个股历史自相似。
- POST /api/pattern_search/by_image：需求1，截图找相似股票（Phase 2 加入）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.schemas import ok
from backend.services.pattern_query import search_by_image, search_by_stock
from backend.storage.data_service import DataService

router = APIRouter(prefix="/api/pattern_search", tags=["pattern_search"])


class ByStockReq(BaseModel):
    symbol: str
    window_start: str | None = None
    window_end: str | None = None
    scales: list[int] | None = None
    top_k: int = 20


@router.post("/by_stock")
def post_by_stock(req: ByStockReq) -> dict:
    res = search_by_stock(
        DataService(), symbol=req.symbol,
        window_start=req.window_start, window_end=req.window_end,
        scales=req.scales, top_k=req.top_k,
    )
    return ok(res)


class ByImageReq(BaseModel):
    image: str            # data URI
    pool_id: int
    hint: str | None = None
    scales: list[int] | None = None
    top_k: int = 20


@router.post("/by_image")
def post_by_image(req: ByImageReq) -> dict:
    res = search_by_image(
        DataService(), image=req.image, pool_id=req.pool_id,
        hint=req.hint, scales=req.scales, top_k=req.top_k,
    )
    return ok(res)
