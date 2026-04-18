"""行情查询端点：日线 + 分钟线。

- ``GET /api/bars/daily``：单股日线 OHLCV，走 ``DataService.load_bars``，支持 raw / qfq。
- ``GET /api/bars/minute``：单股分钟线 OHLCV，直查 ``stock_bar_1m``；qfq 时复用
  日级因子表按当日缩放（分钟线没有独立的 qfq 因子）。

前端 K 线页通过这两条端点渲染 candlestick + 成交量，用于肉眼验证前复权数据。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException

from backend.api.schemas import ok
from backend.storage.clickhouse_client import ch_client
from backend.storage.data_service import DataService

router = APIRouter(prefix="/api/bars", tags=["bars"])

# 分钟线单次查询上限：10 个交易日 × ~240 分钟 ≈ 2400 行/股，足够肉眼查验；
# 超过这个区间一次拉回前端会卡，前端应该分页或缩短窗口。
_MINUTE_MAX_DAYS = 10


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


@router.get("/minute")
def get_minute_bars(
    symbol: str,
    start: date,
    end: date,
    adjust: str = "qfq",
) -> dict:
    """单支分钟线行情。

    实现要点：
    - ``stock_bar_1m`` 里 ``minute_slot: UInt16`` 是"距 00:00 的分钟数"
      （A 股日内 9:30=570 / 15:00=900），返回时拼成 ``YYYY-MM-DD HH:MM`` 字符串，
      前端直接作为 ECharts x-axis 类目。
    - qfq：分钟线没有独立的前复权因子，直接复用日级因子，按当日 trade_date 缩放
      OHLC（``_load_qfq_factors`` 会自动把窗口左侧 seed 带进来）。volume / amount_k
      不参与缩放（因子只影响价格）。
    - 性能护栏：一次最多 ``_MINUTE_MAX_DAYS`` 个交易日，超了直接 400 —— 一次拉
      20 年 × 240 根 会把浏览器和后端都冲爆；前端应按窗口滚动。
    """
    if adjust not in ("qfq", "none"):
        raise HTTPException(status_code=400, detail="invalid adjust")
    if start > end:
        raise HTTPException(status_code=400, detail="start must be <= end")
    # 用自然日估算，已经比实际交易日数多；能通过这个门槛，实际行数也安全。
    if (end - start).days > _MINUTE_MAX_DAYS * 2:
        raise HTTPException(
            status_code=400,
            detail=f"minute 查询窗口过大（自然日 > {_MINUTE_MAX_DAYS * 2}），"
            f"请缩到 {_MINUTE_MAX_DAYS} 个交易日内",
        )

    svc = DataService()
    sid_map = svc.resolver.resolve_many([symbol])
    if not sid_map:
        raise HTTPException(status_code=404, detail="unknown symbol")
    # 单 symbol，取首个（理论上就一个）。
    sid = next(iter(sid_map.values()))

    with ch_client() as ch:
        rows = ch.execute(
            """
            SELECT trade_date, minute_slot, open, high, low, close, volume, amount_k
            FROM quant_data.stock_bar_1m FINAL
            WHERE symbol_id = %(sid)s
              AND trade_date BETWEEN %(s)s AND %(e)s
            ORDER BY trade_date, minute_slot
            """,
            {"sid": int(sid), "s": start, "e": end},
        )
    if not rows:
        return ok({"symbol": symbol, "adjust": adjust, "rows": []})

    df = pd.DataFrame(
        rows,
        columns=[
            "trade_date", "minute_slot",
            "open", "high", "low", "close", "volume", "amount_k",
        ],
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype("float64")

    if adjust == "qfq":
        # 复用日级因子：用窗口内出现过的 trade_date 做 reindex 轴。
        factor_map = svc._load_qfq_factors([int(sid)], start, end)  # noqa: SLF001
        series = factor_map.get(int(sid))
        if series is not None and not series.empty:
            unique_dates = pd.DatetimeIndex(sorted(df["trade_date"].unique()))
            factors = series.reindex(unique_dates, method="ffill").fillna(1.0)
            factor_of_date = df["trade_date"].map(factors)
            for col in ("open", "high", "low", "close"):
                df[col] = df[col] * factor_of_date.to_numpy()

    # 把 (trade_date, minute_slot) 拼成 "YYYY-MM-DD HH:MM"；minute_slot 0..1439。
    hh = (df["minute_slot"] // 60).astype(int).map(lambda v: f"{v:02d}")
    mm = (df["minute_slot"] % 60).astype(int).map(lambda v: f"{v:02d}")
    df["ts"] = df["trade_date"].dt.strftime("%Y-%m-%d") + " " + hh + ":" + mm

    records = df[["ts", "open", "high", "low", "close", "volume", "amount_k"]].to_dict(
        orient="records"
    )
    return ok({"symbol": symbol, "adjust": adjust, "rows": records})
