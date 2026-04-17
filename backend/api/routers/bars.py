"""日线行情查询端点。

``GET /api/bars/daily`` 给前端"股票池预览 / K 线预览"用。单股粒度，
返回 ``DataService.load_bars`` 的字典里那一支的 records 形式（``date`` 转 ISO）。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.storage.data_service import DataService

router = APIRouter(prefix="/api/bars", tags=["bars"])


@router.get("/daily")
def get_daily_bars(
    symbol: str,
    start: date,
    end: date,
    adjust: str = "qfq",
) -> dict:
    """单支日线行情。``adjust`` 默认 ``qfq`` —— 前端展示用前复权更直观。"""
    if adjust not in ("qfq", "none"):
        raise HTTPException(status_code=400, detail="invalid adjust")
    if start > end:
        raise HTTPException(status_code=400, detail="start must be <= end")

    svc = DataService()
    bars = svc.load_bars(
        [symbol], start, end, freq="1d", adjust=adjust  # type: ignore[arg-type]
    )
    # load_bars 的 key 经 resolver 规范化可能和入参大小写 / 空格不同；宽容匹配。
    key_map = {k.strip().upper(): k for k in bars}
    normalized = symbol.strip().upper()
    if normalized not in key_map:
        raise HTTPException(status_code=404, detail="no data for symbol")
    frame = bars[key_map[normalized]]

    # 把 DatetimeIndex 转 ISO 字符串后再转 records，前端直接渲染无需二次处理。
    records = (
        frame.reset_index()
        .assign(trade_date=lambda d: d["trade_date"].dt.strftime("%Y-%m-%d"))
        .to_dict(orient="records")
    )
    return ok({"symbol": symbol, "adjust": adjust, "rows": records})
