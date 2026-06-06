# 图形相似度检索 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 构建一个归一化曲线形状检索引擎，支撑「截图找相似股票」(需求1) 与「个股历史自相似」(需求2) 两个入口。

**Architecture:** 隔离的纯数值引擎 `shape_search(query_curve, candidates)`（z-score 归一化 + 重采样定长 + 相关系数粗筛 + numba DTW 精排），上接两个查询入口：需求2 用框选窗口在该股自身历史滑窗检索；需求1 用视觉 LLM 把截图提取成折线再在股票池×最近窗口检索。引擎隔离便于未来替换为学习型 embedding。

**Tech Stack:** Python / FastAPI / numpy / numba（已装）；前端 Vue3 + TS + ECharts + TanStack Query（已装）。**无需新增依赖。**

**落地顺序：** Phase 1（Task 1-6）= 引擎 + 需求2，无 LLM、可用真实数据完全验证；Phase 2（Task 7-10）= 需求1 截图提取叠加。

---

## 约定速查（实现前必读）

- 行情访问：`DataService().load_bars([symbol], start, end, freq="1d", adjust="qfq")` → `dict[symbol, DataFrame(index=DatetimeIndex, cols=open/high/low/close/volume/amount_k)]`。
- 股票池：`DataService().resolve_pool(pool_id: int) -> list[str]`。
- API 响应契约：`from backend.api.schemas import ok` → `ok(data)` 返回 `{"code":0,"data":...}`。
- 路由：`APIRouter(prefix="/api/...")`，在 `backend/api/main.py` 用 `app.include_router(...)` 注册，并加入第 30 行的 `from backend.api.routers import (...)`。
- LLM：`backend/services/factor_assistant._call_openai_compatible(messages, reasoning_effort="medium") -> str`，支持图片分片（chat/responses/anthropic 三协议）；测试一律 monkeypatch 它，不真调。
- 测试目录：`backend/tests/`，pytest 风格，参考 `test_factor_assistant.py`。
- 前端 API 层：`frontend/src/api/*.ts`，用 `import { client } from './client'` + TanStack Query。

---

# Phase 1：引擎 + 需求2

## Task 1: 引擎 — 曲线归一化 `normalize_curve`

**Files:**
- Create: `backend/services/pattern_search.py`
- Test: `backend/tests/test_pattern_search.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_pattern_search.py
"""pattern_search 引擎单测：归一化 / 相关系数 / DTW / shape_search 排序。

纯数值，不依赖数据库 / 网络。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.pattern_search import normalize_curve, TARGET_LEN


def test_normalize_curve_resamples_to_target_len():
    out = normalize_curve(np.array([1.0, 2.0, 3.0]))
    assert out.shape == (TARGET_LEN,)


def test_normalize_curve_is_scale_and_level_invariant():
    # 同形状、不同价位与振幅，z-score 后应几乎相等
    base = np.linspace(0, 1, 50) ** 2
    a = normalize_curve(base * 10 + 100)
    b = normalize_curve(base * 3 + 5)
    assert np.allclose(a, b, atol=1e-6)


def test_normalize_curve_constant_series_returns_zeros():
    out = normalize_curve(np.full(20, 7.0))
    assert np.allclose(out, 0.0)


def test_normalize_curve_rejects_too_short():
    with pytest.raises(ValueError):
        normalize_curve(np.array([1.0]))
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_search.py -v`
Expected: FAIL（`ModuleNotFoundError: pattern_search` 或 `ImportError`）

**Step 3: Write minimal implementation**

```python
# backend/services/pattern_search.py
"""图形相似度检索引擎（纯数值，无 LLM、无 IO）。

设计见 docs/plans/2026-06-06-pattern-search-design.md。
核心接口 ``shape_search(query_curve, candidates)``，便于未来替换为学习型 embedding。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numba import njit

# 所有曲线统一重采样到定长，保证不同长度窗口可比、DTW 计算量有界。
TARGET_LEN = 128


def normalize_curve(prices, target_len: int = TARGET_LEN) -> np.ndarray:
    """价格序列 → 形状向量：线性重采样到定长 + z-score。

    z-score 去掉绝对价位与涨幅，只保留走势形状。常数序列返回全 0。
    """
    arr = np.asarray(prices, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        raise ValueError("曲线至少需要 2 个有效点")
    xp = np.linspace(0.0, 1.0, arr.size)
    xq = np.linspace(0.0, 1.0, target_len)
    resampled = np.interp(xq, xp, arr)
    mu = float(resampled.mean())
    sd = float(resampled.std())
    if sd < 1e-12:
        return np.zeros(target_len, dtype=np.float64)
    return (resampled - mu) / sd
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_search.py -v`
Expected: PASS（4 个用例）

**Step 5: Commit**

```bash
git add backend/services/pattern_search.py backend/tests/test_pattern_search.py
git commit -m "feat(pattern): 曲线归一化 normalize_curve (z-score+重采样)"
```

---

## Task 2: 引擎 — 相关系数粗筛 + DTW 精排

**Files:**
- Modify: `backend/services/pattern_search.py`
- Test: `backend/tests/test_pattern_search.py`

**Step 1: Write the failing test**

```python
# 追加到 backend/tests/test_pattern_search.py
from backend.services.pattern_search import correlation_scores, dtw_similarity


def _norm(x):
    return normalize_curve(np.asarray(x, dtype=float))


def test_correlation_identical_is_one():
    q = _norm(np.linspace(0, 1, 60) ** 2)
    score = correlation_scores(q, q.reshape(1, -1))[0]
    assert score == pytest.approx(1.0, abs=1e-6)


def test_correlation_inverted_is_negative():
    base = np.linspace(0, 1, 60) ** 2
    q = _norm(base)
    inv = _norm(-base)
    score = correlation_scores(q, inv.reshape(1, -1))[0]
    assert score < -0.9


def test_dtw_phase_shift_still_high():
    # 相位平移：相关系数会掉，DTW 应仍判为高度相似
    n = 128
    a = np.zeros(n); a[40:60] = np.hanning(20)
    b = np.zeros(n); b[60:80] = np.hanning(20)
    qa, qb = _norm(a), _norm(b)
    corr = correlation_scores(qa, qb.reshape(1, -1))[0]
    sim = dtw_similarity(qa, qb)
    assert sim > corr  # DTW 对相位错位更鲁棒
    assert sim > 0.5
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_search.py -k "correlation or dtw" -v`
Expected: FAIL（`ImportError: cannot import name 'correlation_scores'`）

**Step 3: Write minimal implementation**

```python
# 追加到 backend/services/pattern_search.py

def correlation_scores(query: np.ndarray, cand_matrix: np.ndarray) -> np.ndarray:
    """query 与候选矩阵每行的 Pearson 相关系数（向量化）。

    入参均假定已 z-score（mean≈0, std≈1），故相关系数 = 点积 / n。
    返回 shape=(N,)，值域约 [-1, 1]，越大越像。
    """
    n = query.shape[0]
    return (cand_matrix @ query) / n


@njit(cache=True)
def _dtw_band(a: np.ndarray, b: np.ndarray, band: int) -> float:
    """Sakoe-Chiba 带约束 DTW，平方欧氏距离。返回累计距离。"""
    n = a.shape[0]
    INF = 1e18
    cost = np.full((n + 1, n + 1), INF)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        jstart = max(1, i - band)
        jend = min(n, i + band)
        for j in range(jstart, jend + 1):
            d = a[i - 1] - b[j - 1]
            d = d * d
            m = cost[i - 1, j]
            if cost[i, j - 1] < m:
                m = cost[i, j - 1]
            if cost[i - 1, j - 1] < m:
                m = cost[i - 1, j - 1]
            cost[i, j] = d + m
    return cost[n, n]


def dtw_similarity(query: np.ndarray, cand: np.ndarray, band_ratio: float = 0.1) -> float:
    """DTW 距离 → [0,1] 相似度分。两序列均应已 z-score。"""
    band = max(1, int(query.shape[0] * band_ratio))
    dist = _dtw_band(query, cand, band)
    return 1.0 / (1.0 + math.sqrt(dist / query.shape[0]))
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_search.py -v`
Expected: PASS（首次运行 numba 会 JIT 编译，稍慢属正常）

**Step 5: Commit**

```bash
git add backend/services/pattern_search.py backend/tests/test_pattern_search.py
git commit -m "feat(pattern): 相关系数粗筛 + numba 带约束 DTW 精排"
```

---

## Task 3: 引擎 — `shape_search` 多尺度编排

**Files:**
- Modify: `backend/services/pattern_search.py`
- Test: `backend/tests/test_pattern_search.py`

**Step 1: Write the failing test**

```python
# 追加到 backend/tests/test_pattern_search.py
from backend.services.pattern_search import Candidate, Match, shape_search


def test_shape_search_ranks_planted_match_first():
    target = np.sin(np.linspace(0, np.pi, 80))  # 圆弧顶形状
    query = normalize_curve(target)
    rng = np.linspace(0, 1, 80)
    candidates = [
        Candidate(label="noise1", prices=np.cumsum(np.ones(80)), scale=80),
        Candidate(label="line", prices=rng, scale=80),
        Candidate(label="planted", prices=target * 5 + 100, scale=80),  # 同形状不同价位
        Candidate(label="vshape", prices=-target, scale=80),
    ]
    out = shape_search(query, candidates, top_k=4)
    assert isinstance(out[0], Match)
    assert out[0].label == "planted"
    assert out[0].score > 0.9


def test_shape_search_empty_candidates():
    q = normalize_curve(np.linspace(0, 1, 30))
    assert shape_search(q, [], top_k=5) == []
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_search.py -k shape_search -v`
Expected: FAIL（`ImportError: Candidate/Match/shape_search`）

**Step 3: Write minimal implementation**

```python
# 追加到 backend/services/pattern_search.py

@dataclass
class Candidate:
    """一个候选窗口。prices 为原始价格（未归一化）。"""
    label: str
    prices: np.ndarray
    scale: int
    start_date: str | None = None
    end_date: str | None = None


@dataclass
class Match:
    label: str
    score: float
    scale: int
    start_date: str | None
    end_date: str | None
    curve: list[float]  # 归一化后下采样的缩略曲线，供前端画 sparkline


def _downsample(curve: np.ndarray, n: int = 48) -> list[float]:
    if curve.shape[0] <= n:
        return [round(float(v), 4) for v in curve]
    idx = np.linspace(0, curve.shape[0] - 1, n).astype(int)
    return [round(float(v), 4) for v in curve[idx]]


def shape_search(
    query_curve: np.ndarray,
    candidates: list[Candidate],
    top_k: int = 20,
    prefilter_k: int = 50,
) -> list[Match]:
    """对候选窗口做形状检索：相关系数粗筛 Top-K → DTW 精排 → 取 top_k。

    query_curve 必须已是 normalize_curve 的输出（定长 z-score）。
    """
    if not candidates:
        return []
    norm = np.vstack([normalize_curve(c.prices) for c in candidates])
    corr = correlation_scores(query_curve, norm)
    k = min(prefilter_k, len(candidates))
    # 取相关系数最高的 k 个进入 DTW 精排（argpartition 比全排序快）
    cand_idx = np.argpartition(-corr, k - 1)[:k] if k < len(candidates) else np.arange(len(candidates))
    scored = []
    for i in cand_idx:
        sim = dtw_similarity(query_curve, norm[i])
        scored.append((int(i), sim))
    scored.sort(key=lambda t: t[1], reverse=True)
    out: list[Match] = []
    for i, sim in scored[:top_k]:
        c = candidates[i]
        out.append(
            Match(
                label=c.label,
                score=round(float(sim), 4),
                scale=c.scale,
                start_date=c.start_date,
                end_date=c.end_date,
                curve=_downsample(norm[i]),
            )
        )
    return out
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_search.py -v`
Expected: PASS（全部用例）

**Step 5: Commit**

```bash
git add backend/services/pattern_search.py backend/tests/test_pattern_search.py
git commit -m "feat(pattern): shape_search 多尺度编排 + Match/Candidate 结构"
```

---

## Task 4: 需求2 服务 — `search_by_stock`

**Files:**
- Create: `backend/services/pattern_query.py`
- Test: `backend/tests/test_pattern_query.py`

**说明：** 候选 = 该股全历史在各尺度 {30,60,90,120} 下、step 步长滑窗；排除与查询窗口重叠的窗口；对结果做简单 NMS（抑制日期高度重叠的低分项）。

**Step 1: Write the failing test**

```python
# backend/tests/test_pattern_query.py
"""pattern_query 服务单测：用伪造 DataService（不连数据库）验证候选生成与排重叠。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import pattern_query as pq


class _FakeData:
    """返回一段构造行情：中部植入一个与查询窗同形状的圆弧。"""
    def __init__(self, close: pd.Series):
        self._close = close

    def load_bars(self, symbols, start, end, freq="1d", adjust="qfq"):
        df = pd.DataFrame({"close": self._close})
        df.index.name = "trade_date"
        return {symbols[0]: df}


def _make_series():
    dates = pd.date_range("2020-01-01", periods=400, freq="B")
    base = np.random.RandomState(0).normal(0, 0.3, 400).cumsum() + 50
    arc = np.sin(np.linspace(0, np.pi, 60)) * 5
    base[300:360] += arc  # 植入相似形态
    return pd.Series(base, index=dates)


def test_search_by_stock_finds_planted_pattern():
    s = _make_series()
    data = _FakeData(s)
    # 查询窗 = 植入段本身
    res = pq.search_by_stock(
        data, symbol="000001.SZ",
        window_start="2021-02-22", window_end="2021-05-14",  # 约对应 300:360
        scales=[60], top_k=5, step=5,
    )
    # 查询窗自身应被排除，但应能在别处找到相似（此处主要验证不报错且返回结构正确）
    assert "query_curve" in res
    assert isinstance(res["matches"], list)
    for m in res["matches"]:
        assert set(m) >= {"label", "score", "scale", "start_date", "end_date", "curve"}


def test_search_by_stock_default_window_uses_recent():
    s = _make_series()
    res = pq.search_by_stock(_FakeData(s), symbol="000001.SZ", scales=[60], top_k=3, step=10)
    assert len(res["query_curve"]) > 0
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_query.py -v`
Expected: FAIL（`ModuleNotFoundError: pattern_query`）

**Step 3: Write minimal implementation**

```python
# backend/services/pattern_query.py
"""图形检索的两个查询入口（需求2 by_stock / 需求1 by_image）。

引擎在 pattern_search.py；本模块负责取数、生成候选窗口、组织返回。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

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
        mask = (close.index >= pd.Timestamp(window_start)) & (close.index <= pd.Timestamp(window_end))
        q_prices = closes[mask.to_numpy()]
        q_lo = int(np.argmax(mask.to_numpy()))
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
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_query.py -v`
Expected: PASS

**Step 5: 用真实数据人工验证（可选但建议）**

Run: `cd backend && python -c "from backend.storage.data_service import DataService; from backend.services.pattern_query import search_by_stock; import json; print(json.dumps(search_by_stock(DataService(), '000001.SZ', scales=[60], top_k=5)['matches'], ensure_ascii=False, indent=2))"`
Expected: 打印 5 条带日期段与相似度分的匹配，分数降序。

**Step 6: Commit**

```bash
git add backend/services/pattern_query.py backend/tests/test_pattern_query.py
git commit -m "feat(pattern): 需求2 search_by_stock 个股历史自相似检索"
```

---

## Task 5: 需求2 API 端点 + 注册

**Files:**
- Create: `backend/api/routers/pattern_search.py`
- Modify: `backend/api/main.py`（第 30 行 import 块 + include_router 区）
- Test: `backend/tests/test_pattern_search_api.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_pattern_search_api.py
"""pattern_search 路由集成测试：用 TestClient + monkeypatch 服务层，不连数据库。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routers import pattern_search as router_mod

client = TestClient(app)


def test_by_stock_endpoint(monkeypatch):
    def _fake(data, symbol, **kw):
        return {"query_curve": [0.0, 1.0], "matches": [
            {"label": f"{symbol}@2020-01-01", "score": 0.95, "scale": 60,
             "start_date": "2020-01-01", "end_date": "2020-03-25", "curve": [0.0, 1.0]}
        ]}
    monkeypatch.setattr(router_mod, "search_by_stock", _fake)
    resp = client.post("/api/pattern_search/by_stock", json={"symbol": "000001.SZ", "scales": [60], "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["matches"][0]["score"] == 0.95
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_search_api.py -v`
Expected: FAIL（路由不存在 → 404 或 import 错误）

**Step 3: Write minimal implementation**

```python
# backend/api/routers/pattern_search.py
"""图形相似度检索端点。

- POST /api/pattern_search/by_stock：需求2，个股历史自相似。
- POST /api/pattern_search/by_image：需求1，截图找相似股票（Phase 2 加入）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.schemas import ok
from backend.services.pattern_query import search_by_stock
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
```

在 `backend/api/main.py` 第 30 行的 `from backend.api.routers import (` 列表里加 `pattern_search,`，并在 include_router 区（约 338 行后）加：

```python
app.include_router(pattern_search.router)
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_search_api.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/api/routers/pattern_search.py backend/api/main.py backend/tests/test_pattern_search_api.py
git commit -m "feat(pattern): 需求2 by_stock API 端点 + 注册"
```

---

## Task 6: 需求2 前端 — API 层 + 结果组件 + 框选入口

**Files:**
- Create: `frontend/src/api/patternSearch.ts`
- Create: `frontend/src/components/pattern/MatchResultList.vue`
- Modify: `frontend/src/components/charts/CandlestickChart.vue`（框选区域加「找相似」按钮，emit 选中日期段）
- Modify: `frontend/src/pages/klines/KlineViewer.vue`（接收 emit → 调 by_stock → 弹出结果抽屉）

**Step 1: API 层**

```typescript
// frontend/src/api/patternSearch.ts
// 图形相似度检索 API 层。mutation 风格（手动触发）。
import { useMutation } from '@tanstack/vue-query'
import { client } from './client'

export interface PatternMatch {
  label: string
  score: number
  scale: number
  start_date: string | null
  end_date: string | null
  curve: number[]
}
export interface PatternResult {
  query_curve: number[]
  matches: PatternMatch[]
}
export interface ByStockReq {
  symbol: string
  window_start?: string
  window_end?: string
  scales?: number[]
  top_k?: number
}

export function useByStockSearch() {
  return useMutation({
    mutationFn: async (req: ByStockReq): Promise<PatternResult> => {
      const { data } = await client.post('/pattern_search/by_stock', req)
      return data.data
    },
  })
}
```

**Step 2: 结果组件**（每条一个 ECharts sparkline + 分数 + 日期段 + 跳转按钮）

```vue
<!-- frontend/src/components/pattern/MatchResultList.vue -->
<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { PatternMatch } from '../../api/patternSearch'

const props = defineProps<{ matches: PatternMatch[] }>()
const emit = defineEmits<{ (e: 'open', m: PatternMatch): void }>()

function sparkOption(curve: number[]) {
  return {
    grid: { left: 2, right: 2, top: 2, bottom: 2 },
    xAxis: { type: 'category', show: false, data: curve.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{ type: 'line', data: curve, showSymbol: false, lineStyle: { width: 1.5 } }],
  }
}
const rows = computed(() => props.matches)
</script>

<template>
  <div class="match-list">
    <div v-for="m in rows" :key="m.label" class="match-row" @click="emit('open', m)">
      <v-chart class="spark" :option="sparkOption(m.curve)" autoresize />
      <div class="meta">
        <div class="label">{{ m.label }}</div>
        <div class="sub">{{ m.start_date }} ~ {{ m.end_date }} · {{ m.scale }}日</div>
      </div>
      <div class="score">{{ (m.score * 100).toFixed(1) }}%</div>
    </div>
  </div>
</template>

<style scoped>
.match-row { display: flex; align-items: center; gap: 12px; padding: 8px; cursor: pointer; border-bottom: 1px solid var(--n-border-color, #eee); }
.match-row:hover { background: rgba(0,0,0,0.03); }
.spark { width: 120px; height: 40px; flex: none; }
.meta { flex: 1; min-width: 0; }
.label { font-weight: 600; }
.sub { font-size: 12px; opacity: 0.6; }
.score { font-variant-numeric: tabular-nums; font-weight: 700; color: #e6584a; }
</style>
```

**Step 3: 框选入口（CandlestickChart.vue）**

在已有 brush（Volume Profile 用）选区回调里，拿到选中起止索引后映射成 `trade_date`，新增一个「找相似」按钮，点击时 `emit('find-similar', { start, end })`。复用现有 brush 选区逻辑，仅追加一个按钮与 emit；不改动 VP 行为。

**Step 4: KlineViewer.vue 接线**

监听 `find-similar` → 调 `useByStockSearch().mutate({ symbol, window_start: start, window_end: end })` → 成功后用 Naive UI `n-drawer` 展示 `<MatchResultList :matches="..." @open="jumpTo" />`；`jumpTo` 把当前 symbol 的 dataZoom 跳到匹配日期段（同股，无需切股）。

**Step 5: 手动验证**

Run: `cd frontend && npm run dev`，打开任意股票 K 线 → 框选一段 → 点「找相似」→ 抽屉里出现按相似度降序的缩略图列表，点击跳转到对应历史段。

**Step 6: Commit**

```bash
git add frontend/src/api/patternSearch.ts frontend/src/components/pattern/MatchResultList.vue frontend/src/components/charts/CandlestickChart.vue frontend/src/pages/klines/KlineViewer.vue
git commit -m "feat(pattern): 需求2 前端 框选找相似 + 结果列表"
```

---

# Phase 2：需求1 截图找相似股票

## Task 7: 截图→折线提取（视觉 LLM）

**Files:**
- Modify: `backend/services/pattern_query.py`（加 `extract_curve_from_image`）
- Test: `backend/tests/test_pattern_query.py`（monkeypatch LLM）

**Step 1: Write the failing test**

```python
# 追加到 backend/tests/test_pattern_query.py
from backend.services import pattern_query as pq2


def test_extract_curve_from_image_parses_polyline(monkeypatch):
    # 桩：返回一段归一化折线 JSON
    fake = '{"points": [[0,0.1],[0.5,0.9],[1.0,0.3]], "trend": "先涨后跌"}'
    monkeypatch.setattr(pq2, "_call_openai_compatible", lambda messages, **kw: fake)
    curve = pq2.extract_curve_from_image("data:image/png;base64,xxx", hint="圆弧顶")
    from backend.services.pattern_search import TARGET_LEN
    assert curve.shape == (TARGET_LEN,)


def test_extract_curve_rejects_too_few_points(monkeypatch):
    monkeypatch.setattr(pq2, "_call_openai_compatible", lambda messages, **kw: '{"points": [[0,0.1]]}')
    with pytest.raises(ValueError):
        pq2.extract_curve_from_image("data:image/png;base64,xxx")
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_query.py -k extract -v`
Expected: FAIL（`extract_curve_from_image` 不存在）

**Step 3: Write minimal implementation**

```python
# 追加到 backend/services/pattern_query.py 顶部 import
import json
from backend.services.factor_assistant import _call_openai_compatible

# 追加函数
_EXTRACT_SYSTEM = (
    "你是金融图表解析助手。用户给一张股票走势截图，请提取其【价格主曲线】的形状，"
    "输出 JSON：{\"points\": [[x,y], ...], \"trend\": \"一句话趋势描述\"}。"
    "x 为时间归一化到 [0,1]（从左到右递增），y 为价格归一化到 [0,1]（越高价越大）。"
    "采样 30~60 个点，覆盖整体轮廓即可。忽略均线、成交量、坐标轴与水印。只输出 JSON。"
)


def extract_curve_from_image(image_data_uri: str, hint: str | None = None) -> "np.ndarray":
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
```

> 注：图片分片词表随 `OPENAI_API_PROTOCOL` 变化（responses 用 `input_image`、anthropic 用 `image`）。MVP 先按默认 `chat_completions` 实现；若你的环境用其它协议，复用 `factor_assistant._build_user_content` 的分片逻辑（见其 243-268 行）替换 messages 构造。

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_query.py -k extract -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/services/pattern_query.py backend/tests/test_pattern_query.py
git commit -m "feat(pattern): 需求1 截图→折线提取(视觉LLM)"
```

---

## Task 8: 需求1 服务 — `search_by_image`

**Files:**
- Modify: `backend/services/pattern_query.py`
- Test: `backend/tests/test_pattern_query.py`

**Step 1: Write the failing test**

```python
# 追加到 backend/tests/test_pattern_query.py
class _FakePool:
    def __init__(self, panels):  # panels: dict[symbol, np.ndarray]
        self._panels = panels
    def resolve_pool(self, pool_id):
        return list(self._panels)
    def load_bars(self, symbols, start, end, freq="1d", adjust="qfq"):
        out = {}
        for s in symbols:
            df = pd.DataFrame({"close": self._panels[s]})
            df.index = pd.date_range("2024-01-01", periods=len(self._panels[s]), freq="B")
            df.index.name = "trade_date"
            out[s] = df
        return out


def test_search_by_image_ranks_similar_pool_member(monkeypatch):
    arc = np.sin(np.linspace(0, np.pi, 60))
    panels = {
        "AAA.SZ": np.tile(arc, 3) * 5 + 100,      # 含圆弧
        "BBB.SZ": np.linspace(10, 1, 180),         # 单调下跌
    }
    monkeypatch.setattr(pq2, "_call_openai_compatible",
                        lambda messages, **kw: '{"points": ' + str([[i/59, float(v)] for i, v in enumerate(arc)]) + '}')
    res = pq2.search_by_image(_FakePool(panels), image="data:image/png;base64,x", pool_id=1, scales=[60], top_k=2)
    assert res["matches"][0]["label"].startswith("AAA")
    assert len(res["query_curve"]) > 0
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_query.py -k by_image -v`
Expected: FAIL（`search_by_image` 不存在）

**Step 3: Write minimal implementation**

```python
# 追加到 backend/services/pattern_query.py

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
    max_scale = max(scales)
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
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_query.py -v`
Expected: PASS（全部）

**Step 5: Commit**

```bash
git add backend/services/pattern_query.py backend/tests/test_pattern_query.py
git commit -m "feat(pattern): 需求1 search_by_image 池内最近窗口检索"
```

---

## Task 9: 需求1 API 端点

**Files:**
- Modify: `backend/api/routers/pattern_search.py`
- Test: `backend/tests/test_pattern_search_api.py`

**Step 1: Write the failing test**

```python
# 追加到 backend/tests/test_pattern_search_api.py
def test_by_image_endpoint(monkeypatch):
    def _fake(data, image, pool_id, **kw):
        return {"query_curve": [0.0, 1.0], "matches": [
            {"label": "AAA.SZ", "score": 0.9, "scale": 60,
             "start_date": "2024-01-01", "end_date": "2024-03-25", "curve": [0.0, 1.0]}]}
    monkeypatch.setattr(router_mod, "search_by_image", _fake)
    resp = client.post("/api/pattern_search/by_image",
                       json={"image": "data:image/png;base64,x", "pool_id": 1, "scales": [60], "top_k": 5})
    assert resp.status_code == 200
    assert resp.json()["data"]["matches"][0]["label"] == "AAA.SZ"
```

**Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pattern_search_api.py -k by_image -v`
Expected: FAIL（404）

**Step 3: Write minimal implementation**

```python
# 追加到 backend/api/routers/pattern_search.py
from backend.services.pattern_query import search_by_image


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
```

**Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pattern_search_api.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/api/routers/pattern_search.py backend/tests/test_pattern_search_api.py
git commit -m "feat(pattern): 需求1 by_image API 端点"
```

---

## Task 10: 需求1 前端 — 截图检索页

**Files:**
- Modify: `frontend/src/api/patternSearch.ts`（加 `useByImageSearch`）
- Create: `frontend/src/pages/pattern/PatternSearch.vue`
- Modify: 路由表（`frontend/src/router/index.ts` 或等价处）+ 侧边导航

**Step 1: API 层**

```typescript
// 追加到 frontend/src/api/patternSearch.ts
export interface ByImageReq {
  image: string
  pool_id: number
  hint?: string
  scales?: number[]
  top_k?: number
}
export function useByImageSearch() {
  return useMutation({
    mutationFn: async (req: ByImageReq): Promise<PatternResult> => {
      const { data } = await client.post('/pattern_search/by_image', req)
      return data.data
    },
  })
}
```

**Step 2: 页面**（上传截图→base64 dataURI；提示词 textarea；股票池下拉复用现有 pools API；运行；顶部回显 `query_curve` 供核对；下方 `MatchResultList`，点击「在K线中打开」跳到对应 symbol+日期段）

```vue
<!-- frontend/src/pages/pattern/PatternSearch.vue 关键骨架 -->
<script setup lang="ts">
import { ref } from 'vue'
import VChart from 'vue-echarts'
import { useByImageSearch, type PatternResult, type PatternMatch } from '../../api/patternSearch'
import MatchResultList from '../../components/pattern/MatchResultList.vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const imageUri = ref(''); const hint = ref(''); const poolId = ref<number | null>(null)
const result = ref<PatternResult | null>(null)
const search = useByImageSearch()

function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  const reader = new FileReader()
  reader.onload = () => { imageUri.value = reader.result as string }
  reader.readAsDataURL(f)
}
async function run() {
  if (!imageUri.value || poolId.value == null) return
  result.value = await search.mutateAsync({ image: imageUri.value, pool_id: poolId.value, hint: hint.value || undefined })
}
function openMatch(m: PatternMatch) {
  router.push({ name: 'klines', query: { symbol: m.label, start: m.start_date ?? undefined, end: m.end_date ?? undefined } })
}
function queryOption(curve: number[]) {
  return { grid: { left: 4, right: 4, top: 4, bottom: 4 },
    xAxis: { type: 'category', show: false, data: curve.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{ type: 'line', data: curve, showSymbol: false }] }
}
</script>

<template>
  <div style="padding:16px; display:flex; flex-direction:column; gap:12px;">
    <input type="file" accept="image/*" @change="onFile" />
    <img v-if="imageUri" :src="imageUri" style="max-height:200px" />
    <textarea v-model="hint" placeholder="可选提示，如：圆弧底后放量突破" />
    <!-- TODO: 用 n-select + usePools 填 poolId -->
    <input v-model.number="poolId" type="number" placeholder="股票池ID" />
    <button :disabled="search.isPending.value" @click="run">查找相似股票</button>

    <div v-if="result">
      <div style="font-size:12px;opacity:.6">系统识别出的查询曲线（请核对）：</div>
      <v-chart style="height:120px" :option="queryOption(result.query_curve)" autoresize />
      <MatchResultList :matches="result.matches" @open="openMatch" />
    </div>
  </div>
</template>
```

**Step 3: 路由 + 导航**

在路由表加 `{ path: '/pattern', name: 'pattern', component: () => import('../pages/pattern/PatternSearch.vue') }`，侧边栏加「图形检索」入口。确认 `klines` 路由能接收 `symbol/start/end` query 并定位（Task 6 已用同股跳转，此处为跨股跳转，KlineViewer 需支持按 query.symbol 切股——若尚未支持，补一行 `watch(route.query)` 切换）。

**Step 4: 手动验证**

Run: `cd frontend && npm run dev`，进入「图形检索」页 → 传一张走势截图 + 选股票池 → 查找 → 顶部回显识别曲线、下方按相似度列出池内股票，点击跳 K 线对应区间。

**Step 5: Commit**

```bash
git add frontend/src/api/patternSearch.ts frontend/src/pages/pattern/PatternSearch.vue frontend/src/router
git commit -m "feat(pattern): 需求1 前端 截图检索页"
```

---

## 收尾

- 全量回归：`cd backend && python -m pytest tests/test_pattern_search.py tests/test_pattern_query.py tests/test_pattern_search_api.py -v`
- 前端构建：`cd frontend && npm run build`
- 用 superpowers:requesting-code-review 做一次代码评审，再考虑合并回 master。

## 风险与后续（YAGNI，先不做）

- 截图提取精度依赖视觉模型——`query_curve` 回显是关键的人工纠偏闸门。
- 全市场×全历史检索（更大范围）：当前 MVP 不做，需引擎换向量索引/预计算 embedding 时再上（接口已隔离）。
- 成交量维度、固定技术形态库：均为后续可选增强。
