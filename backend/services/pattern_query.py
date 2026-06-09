"""图形检索的两个查询入口（需求2 by_stock / 需求1 by_image）。

引擎在 pattern_search.py；本模块负责取数、生成候选窗口、组织返回。
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from backend.config import settings
from backend.services.factor_assistant import _call_openai_compatible
from backend.services.pattern_search import (
    Candidate,
    Match,
    normalize_curve,
    shape_search,
    shape_search_multi,
)

DEFAULT_SCALES = [30, 60, 90, 120]
_HISTORY_START = date(2005, 1, 1)


def _match_to_dict(m: Match) -> dict:
    return {
        "label": m.label, "score": m.score, "scale": m.scale,
        "start_date": m.start_date, "end_date": m.end_date, "curve": m.curve,
        "sub_scores": m.sub_scores,
    }


def _suppress_overlaps(matches: list[Match], min_gap: int = 10) -> list[Match]:
    """简单 NMS：按分数降序，丢弃与已保留窗口结束日太近的低分项。"""
    kept: list[Match] = []
    for m in sorted(matches, key=lambda x: x.score, reverse=True):
        if m.end_date is None:
            kept.append(m)  # 无日期无法判重叠，直接保留，避免 NaT 比较把它静默丢弃
            continue
        if all(abs((pd.Timestamp(m.end_date) - pd.Timestamp(k.end_date)).days) > min_gap
               for k in kept if k.end_date is not None):
            kept.append(m)
    return kept


def search_by_stock(
    data, symbol: str,
    window_start: str | None = None, window_end: str | None = None,
    scales: list[int] | None = None, top_k: int = 20, step: int = 5,
    min_score: float = 0.0,
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
    matches = shape_search(query_curve, candidates, top_k=top_k * 3, min_score=min_score)
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


def _image_user_content(text: str, image_data_uri: str, protocol: str) -> list[dict]:
    """按 LLM 协议构造「文本 + 图片」的 user content 分片。

    三套协议的图片词表完全不同，用错会导致图片被服务端静默丢弃（模型表现为
    「看不到图」只能瞎猜）。与 factor_assistant._build_user_content 保持一致：
    - responses：input_text / input_image（image_url 直接给 data URI）
    - anthropic_messages：text / image（source 用 base64，需剥掉 data: 前缀）
    - chat_completions（默认）：text / image_url
    """
    if protocol == "responses":
        return [
            {"type": "input_text", "text": text},
            {"type": "input_image", "image_url": image_data_uri},
        ]
    if protocol == "anthropic_messages":
        media_type = "image/png"
        data = image_data_uri
        if image_data_uri.startswith("data:"):
            header, b64 = image_data_uri.split(",", 1)
            if ";" in header:
                media_type = header.split(":")[1].split(";")[0]
            data = b64
        return [
            {"type": "text", "text": text},
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
        ]
    # chat_completions
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_data_uri}},
    ]


def _extract_curve_once(image_data_uri: str, hint: str | None, proto: str) -> np.ndarray:
    """调一次视觉 LLM，把截图提取成归一化折线 → normalize_curve。"""
    user_text = "请提取这张走势图的价格主曲线。"
    if hint:
        user_text += f"\n用户提示（用于纠偏）：{hint}"
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": _image_user_content(user_text, image_data_uri, proto)},
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
    xs = np.array([float(p[0]) for p in pts], dtype=float)
    ys = np.array([float(p[1]) for p in pts], dtype=float)
    # 模型给的 x 可能非等距：按 x 排序后重采样到均匀时间网格，避免把时间轴拉伸/压缩。
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]
    if xs[-1] > xs[0]:
        grid = np.linspace(xs[0], xs[-1], len(ys))
        ys = np.interp(grid, xs, ys)
    return normalize_curve(ys)


def extract_curve_from_image(image_data_uri: str, hint: str | None = None) -> np.ndarray:
    """截图 → 归一化折线。

    视觉 LLM 每次识别有随机噪声。``OPENAI_VISION_SAMPLES`` > 1 时对同一张图提取多次、
    **逐点取中位数**（自一致性），平掉抖动与偶发坏识别；个别采样失败会被跳过，只要有
    一次成功即可。默认采样 1 次（单次，行为不变）。
    """
    # 图片分片格式随 OPENAI_API_PROTOCOL 变化，按协议正确编码，否则图片会被丢弃。
    proto = (settings.openai_api_protocol or "chat_completions").lower()
    n = max(1, int(settings.openai_vision_samples or 1))
    if n == 1:
        return _extract_curve_once(image_data_uri, hint, proto)

    curves: list[np.ndarray] = []
    last_err: Exception | None = None
    for _ in range(n):
        try:
            curves.append(_extract_curve_once(image_data_uri, hint, proto))
        except Exception as e:  # noqa: BLE001 - 单次失败容忍，多数票兜底
            last_err = e
    if not curves:
        # 全部采样都失败：把最后一次错误抛出去，交给上层落 failed 终态。
        raise last_err if last_err is not None else ValueError("截图提取全部失败")
    # 逐点中位数后再 z-score 归一，保证 mean≈0/std≈1（下游 corr/DTW 依赖此前提）。
    median = np.median(np.vstack(curves), axis=0)
    return normalize_curve(median)


def _search_pool_by_curves(
    data, pool_id: int, query_curves: list[np.ndarray],
    scales: list[int], top_k: int, agg: str, min_score: float,
    exclude_symbol: str | None = None,
) -> list[dict]:
    """池内检索内核：股票池每只股的「最近窗口」做候选 → 综合检索 → 同股取最佳。

    供 search_by_image（截图）与 search_by_window（真实走势）共用。
    ``exclude_symbol`` 用于把查询自身那只股票从候选里排除。
    """
    symbols = data.resolve_pool(pool_id)
    if exclude_symbol:
        symbols = [s for s in symbols if s != exclude_symbol]
    if not symbols:
        return []
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
    matches = shape_search_multi(query_curves, candidates, top_k=top_k * 2, agg=agg, min_score=min_score)
    # 同股多尺度只保留最佳
    best: dict[str, Match] = {}
    for m in matches:
        if m.label not in best or m.score > best[m.label].score:
            best[m.label] = m
    final = sorted(best.values(), key=lambda x: x.score, reverse=True)[:top_k]
    return [_match_to_dict(m) for m in final]


def search_by_image(
    data, pool_id: int,
    images: list[str] | None = None, image: str | None = None,
    hint: str | None = None,
    scales: list[int] | None = None, top_k: int = 20, agg: str = "min",
    min_score: float = 0.0,
) -> dict:
    """需求1：截图 → 折线 → 在股票池每只股最近窗口里找相似。

    支持一张或多张截图：多张时综合成一个查询，要求候选「对每张都像」
    （``agg="min"`` 交集语义，默认）。``image``/``images`` 二选一，便于兼容旧调用。
    """
    scales = scales or DEFAULT_SCALES
    # 归一化入参：image 与 images 合并成一个非空列表。
    imgs = list(images) if images else []
    if image:
        imgs.append(image)
    if not imgs:
        raise ValueError("至少需要一张截图")
    query_curves = [extract_curve_from_image(img, hint=hint) for img in imgs]
    curves_out = [[round(float(v), 4) for v in qc] for qc in query_curves]
    matches = _search_pool_by_curves(
        data, pool_id, query_curves, scales, top_k, agg, min_score
    )
    return {
        "query_curve": curves_out[0],   # 兼容单图前端
        "query_curves": curves_out,
        "matches": matches,
    }


def search_by_window(
    data, symbol: str, pool_id: int,
    window_start: str | None = None, window_end: str | None = None,
    scales: list[int] | None = None, top_k: int = 20, min_score: float = 0.0,
) -> dict:
    """相似K线选股：用 ``symbol`` 的某段**真实走势**，在股票池里找走势最像的其他股票。

    无截图、无 LLM——查询曲线直接来自前复权收盘价，零提取噪声。窗口未指定则取最近 60 日。
    查询自身那只股票会从候选里排除。
    """
    scales = scales or DEFAULT_SCALES
    bars = data.load_bars([symbol], _HISTORY_START, date.today(), freq="1d", adjust="qfq")
    if not bars:
        return {"query_curve": [], "matches": []}
    close = next(iter(bars.values()))["close"].dropna()
    closes = close.to_numpy(dtype=float)
    if window_start and window_end:
        mask = np.asarray(
            (close.index >= pd.Timestamp(window_start)) & (close.index <= pd.Timestamp(window_end))
        )
        q_prices = closes[mask]
    else:
        q_prices = closes[-60:]
    if len(q_prices) < 2:
        return {"query_curve": [], "matches": []}
    query_curve = normalize_curve(q_prices)
    matches = _search_pool_by_curves(
        data, pool_id, [query_curve], scales, top_k, "min", min_score,
        exclude_symbol=symbol,
    )
    return {
        "query_curve": [round(float(v), 4) for v in query_curve],
        "matches": matches,
    }
