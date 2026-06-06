"""图形检索的两个查询入口（需求2 by_stock / 需求1 by_image）。

引擎在 pattern_search.py；本模块负责取数、生成候选窗口、组织返回。
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from backend.services.factor_assistant import _call_openai_compatible
from backend.services.pattern_search import (
    Candidate,
    Match,
    normalize_curve,
    shape_search,
)

DEFAULT_SCALES = [30, 60, 90, 120]
_HISTORY_START = date(2005, 1, 1)


def _match_to_dict(m: Match) -> dict:
    return {
        "label": m.label, "score": m.score, "scale": m.scale,
        "start_date": m.start_date, "end_date": m.end_date, "curve": m.curve,
    }


def _suppress_overlaps(matches: list[Match], min_gap: int = 10) -> list[Match]:
    """简单 NMS：按分数降序，丢弃与已保留窗口结束日太近的低分项。"""
    kept: list[Match] = []
    for m in sorted(matches, key=lambda x: x.score, reverse=True):
        if all(abs((pd.Timestamp(m.end_date) - pd.Timestamp(k.end_date)).days) > min_gap for k in kept):
            kept.append(m)
    return kept


def search_by_stock(
    data, symbol: str,
    window_start: str | None = None, window_end: str | None = None,
    scales: list[int] | None = None, top_k: int = 20, step: int = 5,
) -> dict:
    """需求2：在 ``symbol`` 自身历史里找与查询窗口相似的图形。"""
    scales = scales or DEFAULT_SCALES
    bars = data.load_bars([symbol], _HISTORY_START, date.today(), freq="1d", adjust="qfq")
    if not bars:
        return {"query_curve": [], "matches": []}
    close = next(iter(bars.values()))["close"].dropna()
    closes = close.to_numpy(dtype=float)
    dates = [d.strftime("%Y-%m-%d") for d in close.index]
    n = len(closes)
    if n < min(scales):
        return {"query_curve": [], "matches": []}

    # 查询窗口：未指定则取最近 60 日
    if window_start and window_end:
        mask = np.asarray(
            (close.index >= pd.Timestamp(window_start)) & (close.index <= pd.Timestamp(window_end))
        )
        q_prices = closes[mask]
        q_lo = int(np.argmax(mask))
        q_hi = q_lo + len(q_prices)
    else:
        q_prices = closes[-60:]
        q_lo, q_hi = n - 60, n
    if len(q_prices) < 2:
        return {"query_curve": [], "matches": []}
    query_curve = normalize_curve(q_prices)

    # 候选：各尺度滑窗，排除与查询窗重叠的
    candidates: list[Candidate] = []
    for scale in scales:
        if scale > n:
            continue
        for lo in range(0, n - scale + 1, step):
            hi = lo + scale
            if not (hi <= q_lo or lo >= q_hi):  # 与查询窗重叠 → 跳过
                continue
            candidates.append(Candidate(
                label=f"{symbol}@{dates[lo]}", prices=closes[lo:hi], scale=scale,
                start_date=dates[lo], end_date=dates[hi - 1],
            ))
    matches = shape_search(query_curve, candidates, top_k=top_k * 3)
    matches = _suppress_overlaps(matches)[:top_k]
    return {
        "query_curve": [round(float(v), 4) for v in query_curve],
        "matches": [_match_to_dict(m) for m in matches],
    }


_EXTRACT_SYSTEM = (
    "你是金融图表解析助手。用户给一张股票走势截图，请提取其【价格主曲线】的形状，"
    "输出 JSON：{\"points\": [[x,y], ...], \"trend\": \"一句话趋势描述\"}。"
    "x 为时间归一化到 [0,1]（从左到右递增），y 为价格归一化到 [0,1]（越高价越大）。"
    "采样 30~60 个点，覆盖整体轮廓即可。忽略均线、成交量、坐标轴与水印。只输出 JSON。"
)


def extract_curve_from_image(image_data_uri: str, hint: str | None = None) -> np.ndarray:
    """调视觉 LLM 把截图提取成归一化折线 → normalize_curve。"""
    user_text = "请提取这张走势图的价格主曲线。"
    if hint:
        user_text += f"\n用户提示（用于纠偏）：{hint}"
    # chat_completions 协议的图文混合分片
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]},
    ]
    raw = _call_openai_compatible(messages)
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:])
        text = text.rsplit("```", 1)[0]
    obj = json.loads(text)
    pts = obj.get("points", [])
    if len(pts) < 2:
        raise ValueError("LLM 返回的折线点不足 2 个")
    ys = np.array([float(p[1]) for p in pts], dtype=float)
    return normalize_curve(ys)


def search_by_image(
    data, image: str, pool_id: int, hint: str | None = None,
    scales: list[int] | None = None, top_k: int = 20,
) -> dict:
    """需求1：截图 → 折线 → 在股票池每只股最近窗口里找相似。"""
    scales = scales or DEFAULT_SCALES
    query_curve = extract_curve_from_image(image, hint=hint)
    symbols = data.resolve_pool(pool_id)
    if not symbols:
        return {"query_curve": [round(float(v), 4) for v in query_curve], "matches": []}
    bars = data.load_bars(symbols, _HISTORY_START, date.today(), freq="1d", adjust="qfq")
    candidates: list[Candidate] = []
    for sym, df in bars.items():
        close = df["close"].dropna()
        closes = close.to_numpy(dtype=float)
        dates = [d.strftime("%Y-%m-%d") for d in close.index]
        n = len(closes)
        for scale in scales:
            if scale > n:
                continue
            seg = closes[-scale:]  # 最近窗口
            candidates.append(Candidate(
                label=sym, prices=seg, scale=scale,
                start_date=dates[-scale], end_date=dates[-1],
            ))
    matches = shape_search(query_curve, candidates, top_k=top_k * 2)
    # 同股多尺度只保留最佳
    best: dict[str, Match] = {}
    for m in matches:
        if m.label not in best or m.score > best[m.label].score:
            best[m.label] = m
    final = sorted(best.values(), key=lambda x: x.score, reverse=True)[:top_k]
    return {
        "query_curve": [round(float(v), 4) for v in query_curve],
        "matches": [_match_to_dict(m) for m in final],
    }
