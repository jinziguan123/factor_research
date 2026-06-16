"""学习型选股：从用户标注的正/反例里学一个"是否符合我意图"的打分器，再给股票池打分。

为什么不是形状检索：形状检索按整条归一化曲线的主导形态打分，局部/语义特征（跌穿、
趋势、回撤深度）会被淹没、且 z-score 把价位/趋势删了。这里改成**工程特征 + 监督学习**：
- 特征同时含「形状」(下采样归一化曲线) 与「语义」(涨幅/回撤/趋势斜率/末端动量…)，
  把被 z-score 删掉的信息补回来；
- 用正例 vs 反例的对比训练 LightGBM，判别力来自对比（所以**必须有反例**）；
- 模型输出 = "符合我意图"的概率，用它给全池最近窗口打分排序。

小样本：刻意用浅树 + 允许极小叶子，配合主动学习循环（标得越多越准），不上端到端深网。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import numpy as np

from backend.services.pattern_search import (
    Candidate,
    Match,
    _downsample,
    normalize_curve,
)
from backend.storage.curve_cache import load_qfq_closes

_log = logging.getLogger(__name__)

# 下采样归一化曲线的点数（形状特征）。
_CURVE_FEATS = 16
_CONTEXT_FEATS = 6
_PRE_CONTEXT_DAYS = 60


def _slope(x: np.ndarray) -> float:
    """归一化线性回归斜率——去绝对价位量纲。"""
    xs = np.linspace(0.0, 1.0, x.size)
    xm = xs - xs.mean()
    denom = float((xm * xm).sum())
    if denom <= 0:
        return 0.0
    y = (x - x.mean()) / (x.std() + 1e-9)
    return float((xm * (y - y.mean())).sum() / denom)


def _extract_context_features(pre_close: np.ndarray | None) -> np.ndarray:
    """窗口前价格 → 趋势上下文特征（上升/下降/震荡环境）。

    供学习型选股模型区分"同一形状在上升趋势 vs 下跌趋势中"。
    pre_close 不足时返回全 0（中性，不影响判别）。
    """
    zeros = np.zeros(_CONTEXT_FEATS, dtype=np.float64)
    if pre_close is None:
        return zeros
    p = np.asarray(pre_close, dtype=np.float64)
    p = p[np.isfinite(p)]
    if p.size < 5 or p[0] <= 0:
        return zeros
    k20 = min(20, p.size)
    rets = np.diff(p) / p[:-1]
    return np.asarray([
        float(p[-1] / p[-k20] - 1.0),                       # 近 20 日涨幅（短期趋势）
        float(p[-1] / p[0] - 1.0),                           # 全段涨幅（中期趋势）
        _slope(p),                                            # 趋势斜率
        float(p[-1] / p.mean() - 1.0),                       # 末端 vs 均价（偏强/偏弱）
        float((p / np.maximum.accumulate(p) - 1.0).min()),   # 最大回撤（趋势稳定度）
        float(rets.std()) if rets.size else 0.0,              # 波动率
    ], dtype=np.float64)


def extract_window_features(close: np.ndarray, pre_close: np.ndarray | None = None) -> np.ndarray | None:
    """一段收盘价窗口 → 固定长度特征向量；点数不足返回 None。

    特征三块：语义 14 维 + 形状 16 维 + 趋势上下文 6 维。
    上下文来自窗口前 ``pre_close``，让模型区分同一形状在不同趋势环境中的意义。
    """
    c = np.asarray(close, dtype=np.float64)
    c = c[np.isfinite(c)]
    if c.size < 10 or c[0] <= 0:
        return None
    L = c.size
    ret = c / c[0] - 1.0
    peak = np.maximum.accumulate(c)
    dd = c / peak - 1.0
    rmax = float(c.max())
    half = L // 2

    rets = np.diff(c) / c[:-1]
    feats = [
        float(ret[-1]),
        float(rmax / c[0] - 1.0),
        float(c[-1] / rmax - 1.0),
        float(dd.min()),
        float(c[-1] / c[max(0, L - 4)] - 1.0),
        float(c[-1] / c[max(0, L - 6)] - 1.0),
        float(c[-1] / c[max(0, L - 11)] - 1.0),
        _slope(c),
        _slope(c[:half]) if half >= 3 else 0.0,
        _slope(c[half:]) if L - half >= 3 else 0.0,
        float(np.argmax(c) / (L - 1)),
        float(np.argmin(c) / (L - 1)),
        float(rets.std()) if rets.size else 0.0,
        float(c[-1] / c.mean() - 1.0),
    ]
    curve = np.asarray(_downsample(normalize_curve(c), _CURVE_FEATS), dtype=np.float64)
    ctx = _extract_context_features(pre_close)
    return np.concatenate([np.asarray(feats, dtype=np.float64), curve, ctx])


def _train(features: np.ndarray, y: np.ndarray):
    """正/反例特征 → 概率打分器。

    小样本（几条~几十条标注）下用 **标准化 + 强正则逻辑回归**，而不是 GBM：
    - GBM 在 4 个样本上会完美过拟合，对池里几乎所有候选都吐 ~0.999（"全 99.9%" 就是这么来的）；
    - LR + L2(C 小) 概率平滑、分得开、可排序，且样本少时更稳。
    标注攒多了再换更强模型不迟。
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    clf = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.3,                    # 强正则，避免可分小样本把概率推到 0/1 极端
            class_weight="balanced",
            max_iter=2000,
        ),
    )
    clf.fit(features, y)
    return clf


def _scale_buckets(pos_lens: list[int], max_buckets: int = 4) -> list[int]:
    """正例窗口长度 → 检索用的尺度集合（数据驱动）。

    直接取标注里**真实出现过的形态长度**（去重，clamp 到 [20,250]）；档数过多时
    用等距分位数压到 ``max_buckets`` 档。关键：尺度全部来自真实正例，绝不引入
    30 天这类「碰巧高分」的塌缩短窗——这正是放开多尺度而不偏向短窗的前提。
    """
    if not pos_lens:
        return [60]
    uniq = sorted({max(20, min(int(v), 250)) for v in pos_lens})
    if len(uniq) <= max_buckets:
        return uniq
    qs = np.linspace(0.0, 1.0, max_buckets)
    picks = sorted({int(round(float(np.quantile(uniq, q)))) for q in qs})
    return [max(20, min(p, 250)) for p in picks]


def search_by_learned(
    data, labels: list[dict], pool_id: int,
    top_k: int = 20, mode: str = "realtime",
    step: int = 5, history_days: int = 1000,
    on_progress: Callable[[int], None] | None = None,
) -> dict:
    """用标注的正/反例训练打分器，再给股票池打分排序。

    ``labels`` = ``[{"symbol","start","end","label"(1/0)}, ...]``。
    打分窗口长度统一取「正例窗口长度的中位数」，与你框选的长度一致。

    ``mode``：
    - ``"realtime"``（默认）：只看每只股的**最近**一段——找"现在正在出现该形态"的票，实时选股；
    - ``"history"``：在每只股**历史**里滑窗找该形态出现过的最佳一段——学习阶段用，
      便于发现历史样例去标注。``step``/``history_days`` 控制滑窗步长与回看深度。

    返回 ``{query_curves(正例归一化曲线), matches}``。需要至少各 1 个正例和反例。
    """
    from datetime import date as _date

    # 1) 从标注窗口抽特征
    feats: list[np.ndarray] = []
    ys: list[int] = []
    pos_curves: list[list[float]] = []
    pos_lens: list[int] = []          # 正例窗口长度，决定检索尺度集合
    pos_meta: list[dict] = []         # 正例的 symbol + 时段，供前端展示/跳转
    exclude: set[str] = set()
    label_syms = sorted({str(lb["symbol"]).upper() for lb in labels})
    if label_syms:
        bars = data.load_bars(
            label_syms, _date(2005, 1, 1), _date.today(), freq="1d", adjust="qfq"
        )
    else:
        bars = {}
    for lb in labels:
        sym = str(lb["symbol"]).upper()
        exclude.add(sym)
        df = bars.get(sym)
        if df is None:
            continue
        close = df["close"].dropna()
        idx = close.index
        import pandas as pd
        # 兼容两种 key：DB 读出来是 start_date/end_date；API 入参是 start/end。
        ws = lb.get("start") or lb.get("start_date")
        we = lb.get("end") or lb.get("end_date")
        close_arr = close.to_numpy(dtype=float)
        if ws and we:
            mask = np.asarray(
                (idx >= pd.Timestamp(ws)) & (idx <= pd.Timestamp(we))
            )
            seg = close_arr[mask]
            seg_idx = idx[mask]
            pre_mask = np.asarray(idx < pd.Timestamp(ws))
            pre = close_arr[pre_mask][-_PRE_CONTEXT_DAYS:] if pre_mask.any() else None
        else:
            seg = close_arr[-60:]
            seg_idx = idx[-60:]
            pre = close_arr[:-60][-_PRE_CONTEXT_DAYS:] if len(close_arr) > 60 else None
        f = extract_window_features(seg, pre_close=pre)
        if f is None:
            continue
        feats.append(f)
        y = 1 if int(lb["label"]) == 1 else 0
        ys.append(y)
        if y == 1 and len(seg) >= 2:
            pos_curves.append([round(float(v), 4) for v in normalize_curve(seg)])
            pos_lens.append(len(seg))
            pos_meta.append({
                "id": lb.get("id"),    # 标注主键，供前端删除误选正例
                "symbol": sym,
                "start_date": seg_idx[0].strftime("%Y-%m-%d") if len(seg_idx) else None,
                "end_date": seg_idx[-1].strftime("%Y-%m-%d") if len(seg_idx) else None,
            })

    y_arr = np.asarray(ys, dtype=int)
    if y_arr.sum() < 1 or (len(y_arr) - y_arr.sum()) < 1:
        raise ValueError("至少需要 1 个正例和 1 个反例才能训练（判别力来自正反对比）")

    model = _train(np.vstack(feats), y_arr)

    # 检索尺度集合 = 正例真实长度（数据驱动，去重/分桶）。同一形态可能 60 天形成、
    # 也可能 120 天形成，所以在每个真实尺度上各自找，避免「统一尺度」导致的错找/漏找。
    scale_set = _scale_buckets(pos_lens)

    # 2) 给股票池打分
    symbols = [s for s in data.resolve_pool(pool_id) if s not in exclude]
    if on_progress:
        on_progress(15)

    out: dict[str, Match] = {}
    if symbols:
        t0 = time.perf_counter()
        closes_map = load_qfq_closes(data, symbols)
        t_load = time.perf_counter()
        if on_progress:
            on_progress(50)

        # Phase 1: 提取所有候选窗口特征（纯 numpy，无 sklearn 开销）
        feat_list: list[np.ndarray] = []
        meta_list: list[tuple[str, int, int]] = []
        for sym, item in closes_map.items():
            closes = item["closes"]
            n = len(closes)
            for tl in scale_set:
                if n < tl:
                    continue
                if mode == "history":
                    lo_min = max(0, n - tl - max(0, history_days))
                    starts = range(lo_min, n - tl + 1, max(1, step))
                else:
                    starts = (n - tl,)
                for lo in starts:
                    seg = closes[lo:lo + tl]
                    pre = closes[max(0, lo - _PRE_CONTEXT_DAYS):lo] if lo > 0 else None
                    f = extract_window_features(seg, pre_close=pre)
                    if f is None:
                        continue
                    feat_list.append(f)
                    meta_list.append((sym, tl, lo))
        t_feat = time.perf_counter()
        if on_progress:
            on_progress(75)

        # Phase 2: 批量预测——一次 predict_proba 替代逐样本调用
        if feat_list:
            X = np.vstack(feat_list)
            probs = model.predict_proba(X)[:, 1]
            for i, (sym, tl, lo) in enumerate(meta_list):
                prob = float(probs[i])
                prev = out.get(sym)
                if prev is None or prob > prev.score:
                    item = closes_map[sym]
                    out[sym] = Match(
                        label=sym, score=round(prob, 4), scale=tl,
                        start_date=item["dates"][lo],
                        end_date=item["dates"][lo + tl - 1],
                        curve=_downsample(normalize_curve(
                            item["closes"][lo:lo + tl])),
                    )
        t_pred = time.perf_counter()
        _log.info(
            "learned 检索耗时: 加载 %.0fms | 特征 %.0fms (%d窗口) | 预测 %.0fms",
            (t_load - t0) * 1e3, (t_feat - t_load) * 1e3,
            len(feat_list), (t_pred - t_feat) * 1e3,
        )

    final = sorted(out.values(), key=lambda m: m.score, reverse=True)[:top_k]
    return {
        "query_curves": pos_curves,
        "query_labels": pos_meta,
        "matches": [
            {
                "label": m.label, "score": m.score, "scale": m.scale,
                "start_date": m.start_date, "end_date": m.end_date,
                "curve": m.curve, "sub_scores": [],
            }
            for m in final
        ],
    }
