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

import numpy as np

from backend.services.pattern_search import (
    Candidate,
    Match,
    _downsample,
    normalize_curve,
)

# 下采样归一化曲线的点数（形状特征）。
_CURVE_FEATS = 16


def extract_window_features(close: np.ndarray) -> np.ndarray | None:
    """一段收盘价窗口 → 固定长度特征向量；点数不足返回 None。

    特征分两块：
    - 语义（捕捉趋势/涨幅/回撤/末端动量——z-score 会删掉的信息）；
    - 形状（下采样归一化曲线——和形状检索同源，保留"长得像不像"）。
    """
    c = np.asarray(close, dtype=np.float64)
    c = c[np.isfinite(c)]
    if c.size < 10 or c[0] <= 0:
        return None
    L = c.size
    ret = c / c[0] - 1.0                      # 整段累计涨跌
    peak = np.maximum.accumulate(c)
    dd = c / peak - 1.0                        # 逐日相对前高回撤
    rmax = float(c.max())
    half = L // 2

    def _slope(x: np.ndarray) -> float:
        xs = np.linspace(0.0, 1.0, x.size)
        xm = xs - xs.mean()
        denom = float((xm * xm).sum())
        if denom <= 0:
            return 0.0
        # 对归一化后的序列求斜率，去掉绝对价位量纲
        y = (x - x.mean()) / (x.std() + 1e-9)
        return float((xm * (y - y.mean())).sum() / denom)

    rets = np.diff(c) / c[:-1]
    feats = [
        float(ret[-1]),                        # 整段涨幅
        float(rmax / c[0] - 1.0),              # 期间最大涨幅（涨过一波）
        float(c[-1] / rmax - 1.0),             # 末端相对窗口最高的回撤（跌穿/超跌）
        float(dd.min()),                       # 期间最大回撤
        float(c[-1] / c[max(0, L - 4)] - 1.0), # 近3日动量（末端那一下）
        float(c[-1] / c[max(0, L - 6)] - 1.0), # 近5日动量
        float(c[-1] / c[max(0, L - 11)] - 1.0),# 近10日动量
        _slope(c),                             # 整段趋势斜率
        _slope(c[:half]) if half >= 3 else 0.0,   # 前半段斜率（涨）
        _slope(c[half:]) if L - half >= 3 else 0.0,  # 后半段斜率（跌）
        float(np.argmax(c) / (L - 1)),         # 最高点位置（早/晚）
        float(np.argmin(c) / (L - 1)),         # 最低点位置
        float(rets.std()) if rets.size else 0.0,  # 波动率
        float(c[-1] / c.mean() - 1.0),         # 末端相对窗口均价（位置）
    ]
    curve = np.asarray(_downsample(normalize_curve(c), _CURVE_FEATS), dtype=np.float64)
    return np.concatenate([np.asarray(feats, dtype=np.float64), curve])


def _train(features: np.ndarray, y: np.ndarray):
    """正/反例特征 → LightGBM 概率分类器。小样本用浅树 + 允许极小叶子。"""
    import lightgbm as lgb

    clf = lgb.LGBMClassifier(
        n_estimators=120,
        num_leaves=7,
        max_depth=3,
        learning_rate=0.05,
        min_child_samples=1,
        min_split_gain=0.0,
        subsample=1.0,
        colsample_bytree=1.0,
        class_weight="balanced",     # 正反例数量不均时平衡
        verbosity=-1,
        n_jobs=1,
    )
    clf.fit(features, y)
    return clf


def search_by_learned(
    data, labels: list[dict], pool_id: int,
    scales: list[int] | None = None, top_k: int = 20,
) -> dict:
    """用标注的正/反例训练打分器，再给股票池最近窗口打分排序。

    ``labels`` = ``[{"symbol","start","end","label"(1/0)}, ...]``。
    返回 ``{query_curves(正例归一化曲线), matches}``。
    需要至少各 1 个正例和反例（否则没法学判别）。
    """
    from datetime import date as _date

    scales = scales or [30, 60, 90, 120]

    # 1) 从标注窗口抽特征
    feats: list[np.ndarray] = []
    ys: list[int] = []
    pos_curves: list[list[float]] = []
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
        if lb.get("start") and lb.get("end"):
            mask = np.asarray(
                (idx >= pd.Timestamp(lb["start"])) & (idx <= pd.Timestamp(lb["end"]))
            )
            seg = close.to_numpy(dtype=float)[mask]
        else:
            seg = close.to_numpy(dtype=float)[-60:]
        f = extract_window_features(seg)
        if f is None:
            continue
        feats.append(f)
        y = 1 if int(lb["label"]) == 1 else 0
        ys.append(y)
        if y == 1 and len(seg) >= 2:
            pos_curves.append([round(float(v), 4) for v in normalize_curve(seg)])

    y_arr = np.asarray(ys, dtype=int)
    if y_arr.sum() < 1 or (len(y_arr) - y_arr.sum()) < 1:
        raise ValueError("至少需要 1 个正例和 1 个反例才能训练（判别力来自正反对比）")

    model = _train(np.vstack(feats), y_arr)

    # 2) 给股票池最近窗口打分
    symbols = [s for s in data.resolve_pool(pool_id) if s not in exclude]
    out: dict[str, Match] = {}
    if symbols:
        pool_bars = data.load_bars(
            symbols, _date(2005, 1, 1), _date.today(), freq="1d", adjust="qfq"
        )
        for sym, df in pool_bars.items():
            close = df["close"].dropna()
            closes = close.to_numpy(dtype=float)
            dates = [d.strftime("%Y-%m-%d") for d in close.index]
            n = len(closes)
            best: Match | None = None
            for scale in scales:
                if scale > n:
                    continue
                seg = closes[-scale:]
                f = extract_window_features(seg)
                if f is None:
                    continue
                prob = float(model.predict_proba(f.reshape(1, -1))[0, 1])
                if best is None or prob > best.score:
                    best = Match(
                        label=sym, score=round(prob, 4), scale=scale,
                        start_date=dates[-scale], end_date=dates[-1],
                        curve=_downsample(normalize_curve(seg)),
                    )
            if best is not None:
                out[sym] = best

    final = sorted(out.values(), key=lambda m: m.score, reverse=True)[:top_k]
    return {
        "query_curves": pos_curves,
        "matches": [
            {
                "label": m.label, "score": m.score, "scale": m.scale,
                "start_date": m.start_date, "end_date": m.end_date,
                "curve": m.curve, "sub_scores": [],
            }
            for m in final
        ],
    }
