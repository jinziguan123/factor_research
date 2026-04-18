"""把 backtest parquet 产物读成前端可直接用的 JSON 结构。

拆出来是为了：
1. 让 router 层只管路径校验 / HTTP 约定，不碰 pandas；
2. 纯函数（``downsample_step``）便于单测，避免每次改图表就得跑完整 HTTP 链路。

这里**不做**缓存、不做异步、不做 mmap——回测产物单股规模通常日频数千点 / 分钟频几万点，
一次读 parquet → JSON 在毫秒量级；加缓存反而带来过期 / 一致性的复杂度。等真跑出性能
问题再说。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd


def downsample_step(n: int, max_points: int) -> int:
    """计算等步长抽样的 step；返回 ``step`` 满足 ``ceil(n/step) <= max_points``。

    - ``n <= max_points`` → 1（不抽）
    - ``max_points <= 0`` → 1（退化为不抽；防御式，调用方应保证 >0）
    - 其余走 ``ceil(n / max_points)``
    """
    if n <= 0 or max_points <= 0 or n <= max_points:
        return 1
    return math.ceil(n / max_points)


def _downsample_indices(n: int, max_points: int) -> list[int]:
    """返回抽样后保留的 **原始行下标**；保证首尾都在，单调递增去重。

    首尾必须保留——折线图不含首尾会"画出去""停得早"，视觉上会误导。
    """
    if n == 0:
        return []
    step = downsample_step(n, max_points)
    if step == 1:
        return list(range(n))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def load_equity_series(
    path: Path | str, *, max_points: int = 2000
) -> dict[str, Any]:
    """读 ``equity.parquet`` → ``{dates: [...], values: [...], total, sampled}``。

    - equity.parquet 约定：index=trade_date（datetime 或 date），列含 ``equity``；
      若只有一列且不叫 equity，也兜底取第 0 列，避免 vectorbt 版本变列名时直接 500。
    - 时间统一 ISO date 字符串（YYYY-MM-DD），前端图表 axisLabel 直接喂。
    - ``sampled=True`` 表示做过降采样，前端可以提示 "已降采样显示 N 个点"。
    """
    df = pd.read_parquet(str(path))
    if df.empty:
        return {"dates": [], "values": [], "total": 0, "sampled": False}

    # 值列：优先 equity，否则退化到第 0 列（兼容 vectorbt 不同版本命名）
    value_col = "equity" if "equity" in df.columns else df.columns[0]

    # 索引统一转 ISO 日期字符串：DatetimeIndex / date / 已是 str 都兼容
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        dates_iter = (d.strftime("%Y-%m-%d") for d in idx)
    else:
        # 既可能是 Timestamp 也可能是 python date/str —— pd.to_datetime 统一先转 ts
        dates_iter = (
            pd.Timestamp(d).strftime("%Y-%m-%d") for d in idx
        )

    all_dates = list(dates_iter)
    all_values = [
        None if pd.isna(v) else float(v) for v in df[value_col].tolist()
    ]

    n = len(all_dates)
    keep = _downsample_indices(n, max_points)
    sampled = len(keep) < n
    return {
        "dates": [all_dates[i] for i in keep],
        "values": [all_values[i] for i in keep],
        "total": n,
        "sampled": sampled,
    }


def load_trades_page(
    path: Path | str, *, page: int = 1, size: int = 50
) -> dict[str, Any]:
    """读 ``trades.parquet`` → 分页 JSON ``{total, page, size, columns, rows}``。

    - ``page`` 1-based；越界（超出最后一页）回空 ``rows``，``total`` 仍返回真实值。
    - ``columns`` 是 parquet 的列名（顺序保留），前端直接拿来当表头；这样 VectorBT
      改列名时不用同步改前端。
    - ``rows`` 元素是 ``{col: value, ...}``；时间 / datetime 列统一 ISO 字符串。
    """
    page = max(1, int(page))
    size = max(1, min(int(size), 500))

    df = pd.read_parquet(str(path))
    total = len(df)
    columns = [str(c) for c in df.columns]

    if total == 0:
        return {
            "total": 0,
            "page": page,
            "size": size,
            "columns": columns,
            "rows": [],
        }

    start = (page - 1) * size
    end = start + size
    if start >= total:
        return {
            "total": total,
            "page": page,
            "size": size,
            "columns": columns,
            "rows": [],
        }

    page_df = df.iloc[start:end]
    rows: list[dict[str, Any]] = []
    for _, r in page_df.iterrows():
        row: dict[str, Any] = {}
        for c in columns:
            v = r[c]
            # NaN → None（JSON 合法）
            if isinstance(v, float) and pd.isna(v):
                row[c] = None
            elif isinstance(v, pd.Timestamp):
                row[c] = v.strftime("%Y-%m-%d %H:%M:%S")
            elif hasattr(v, "isoformat"):
                # date / datetime（非 pd.Timestamp）
                row[c] = v.isoformat()
            elif isinstance(v, (int, float, str, bool)) or v is None:
                row[c] = v
            else:
                # numpy 标量等：str() 兜底，保证 JSON 可序列化
                row[c] = str(v)
        rows.append(row)

    return {
        "total": total,
        "page": page,
        "size": size,
        "columns": columns,
        "rows": rows,
    }
