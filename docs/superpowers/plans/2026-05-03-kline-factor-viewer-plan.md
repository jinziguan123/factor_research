# K线查看页因子选择与查看 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 K 线查看页面增加因子选择与查看功能——用户可在成交量下方查看所选因子的时序折线图，与 K 线共享 X 轴缩放。

**Architecture:** 后端新增 `GET /api/factors/{id}/bars` 端点计算单股因子时序；前端 `CandlestickChart` 扩展 `factorRows` prop 动态构建 ECharts grid/series；`KlineViewer` 新增因子选择器、chip 管理、参数编辑弹窗。

**Tech Stack:** Python/FastAPI + Vue 3/Naive UI/ECharts + TypeScript

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/api/routers/factors.py` | 修改 | 新增 `GET /api/factors/{id}/bars` 端点 |
| `backend/tests/test_api_factor_bars.py` | 创建 | 端点集成测试 |
| `frontend/src/api/klines.ts` | 修改 | 新增 `FactorBarQuery`、`FactorBarResponse` 类型 + `useFactorBars` hook |
| `frontend/src/components/charts/CandlestickChart.vue` | 修改 | 新增 `factorRows` prop，动态 grid/series/yAxis/xAxis |
| `frontend/src/pages/klines/KlineViewer.vue` | 修改 | 因子选择器、chip 管理、参数编辑、频率切换逻辑 |

---

### Task 1: 后端端点测试

**Files:**
- Create: `backend/tests/test_api_factor_bars.py`

- [ ] **Step 1: 编写失败测试**

```python
"""GET /api/factors/{factor_id}/bars 端点集成测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_factor_bars_returns_time_series():
    """正常请求返回 {dates, values} 时间序列，与 K 线窗口对齐。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-15",
                "freq": "1d",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["factor_id"] == "reversal_n"
    assert data["symbol"] == "000001.SZ"
    assert isinstance(data["dates"], list)
    assert isinstance(data["values"], list)
    assert len(data["dates"]) == len(data["values"])
    assert "params" in data


def test_factor_bars_unsupported_freq_returns_400():
    """因子不支持该频率时返回 400。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-05",
                "freq": "1m",
            },
        )
    # reversal_n 默认 supported_freqs=("1d",)，不支持 1m
    assert r.status_code == 400, r.text


def test_factor_bars_invalid_factor_returns_404():
    """不存在的因子返回 404。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/__nonexistent__/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-05",
                "freq": "1d",
            },
        )
    assert r.status_code == 404


def test_factor_bars_with_custom_params():
    """传入自定义参数时使用该参数计算。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-10",
                "freq": "1d",
                "params": '{"n": 10}',
            },
        )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["params"] == {"n": 10}


def test_factor_bars_invalid_params_returns_400():
    """非法参数返回 400。"""
    from backend.api.main import app

    with TestClient(app) as c:
        r = c.get(
            "/api/factors/reversal_n/bars",
            params={
                "symbol": "000001.SZ",
                "start": "2025-11-01",
                "end": "2025-11-05",
                "freq": "1d",
                "params": '{"n": -1}',
            },
        )
    assert r.status_code == 400
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_api_factor_bars.py -v
```

Expected: 全部 5 个测试 FAIL（端点尚不存在，`reversal_n/bars` 路由未注册）。

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_api_factor_bars.py
git commit -m "test: add factor bars endpoint integration tests"
```

---

### Task 2: 实现后端端点 `GET /api/factors/{id}/bars`

**Files:**
- Modify: `backend/api/routers/factors.py` — 在现有路由后面追加新端点

- [ ] **Step 1: 添加必要的 import**

在 `backend/api/routers/factors.py` 顶部已有 import 区域追加：

```python
import json
from datetime import date

import pandas as pd

from backend.engine.base_factor import FactorContext
from backend.storage.data_service import DataService
```

（`FactorRegistry`、`HTTPException`、`ok`、`logger` 已在文件中导入）

- [ ] **Step 2: 在文件末尾（`set_sota` 之后、`get_lineage` 之前或之后）追加端点函数**

```python
@router.get("/{factor_id}/bars")
def get_factor_bars(
    factor_id: str,
    symbol: str,
    start: date,
    end: date,
    freq: str = "1d",
    params: str | None = None,
) -> dict:
    """计算单个因子对单支股票的时序值。

    校验：factor_id 存在 → freq ∈ supported_freqs → params 合法 → symbol 解析。
    计算：FactorContext(symbols=[symbol]) → factor.compute() → 提取列 → 返回时间序列。
    """
    reg = FactorRegistry()
    reg.scan_and_register()

    try:
        factor = reg.get(factor_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="factor not found")

    if freq not in factor.supported_freqs:
        raise HTTPException(
            status_code=400,
            detail=f"因子 {factor_id} 不支持频率 {freq!r}，仅支持 {list(factor.supported_freqs)}",
        )

    # 解析 params
    if params:
        try:
            parsed = json.loads(params)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"params JSON 解析失败：{e}") from e
    else:
        parsed = dict(factor.default_params)

    try:
        validated = factor.validate_params(parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 解析 symbol
    svc = DataService()
    sid_map = svc.resolver.resolve_many([symbol.strip().upper()])
    normalized = symbol.strip().upper()
    if normalized not in {k.strip().upper() for k in sid_map}:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol!r}")

    # 构造 context + compute
    warmup = factor.required_warmup(validated)
    ctx = FactorContext(
        data=svc,
        symbols=[normalized],
        start_date=pd.Timestamp(start),
        end_date=pd.Timestamp(end),
        warmup_days=warmup,
    )
    F = factor.compute(ctx, validated)

    # 从宽表中提取目标 symbol 列
    if F.empty or normalized not in F.columns:
        return ok({
            "factor_id": factor_id,
            "symbol": normalized,
            "freq": freq,
            "params": validated,
            "params_hash": "",
            "dates": [],
            "values": [],
            "version": reg.current_version(factor_id),
        })

    series = F[normalized]
    # 将 inf / -inf 替换为 None（NaN 已经是 None）
    values: list[float | None] = [
        None if (v is None or (isinstance(v, float) and not _isfinite(v)))
        else float(v)
        for v in series.values
    ]
    dates = [
        d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        for d in series.index
    ]

    return ok({
        "factor_id": factor_id,
        "symbol": normalized,
        "freq": freq,
        "params": validated,
        "params_hash": "",
        "dates": dates,
        "values": values,
        "version": reg.current_version(factor_id),
    })
```

- [ ] **Step 3: 添加辅助函数（文件顶部辅助函数区域）**

```python
import math

def _isfinite(v: float) -> bool:
    """检查浮点数是否有限（非 NaN、非 Inf）。"""
    return not (math.isnan(v) or math.isinf(v))
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_api_factor_bars.py -v
```

Expected: 全部 5 个测试 PASS（`test_factor_bars_unsupported_freq_returns_400` 取决于 `reversal_n` 的 `supported_freqs`，如果其包含 `1m` 则需换一个因子或用 `turnover_ratio`）。

- [ ] **Step 5: 提交**

```bash
git add backend/api/routers/factors.py
git commit -m "feat(api): add GET /api/factors/{id}/bars endpoint for single-stock factor time series"
```

---

### Task 3: 前端 API 层 —— `useFactorBars` hook

**Files:**
- Modify: `frontend/src/api/klines.ts`

- [ ] **Step 1: 在 `klines.ts` 末尾追加类型定义和 hook**

```typescript
// 因子 bars 查询参数
export interface FactorBarQuery {
  symbol: string
  start: string
  end: string
  freq: string
}

// 因子 bars 响应
export interface FactorBarResponse {
  factor_id: string
  symbol: string
  freq: string
  params: Record<string, any>
  dates: string[]
  values: (number | null)[]
  version: number
}

/** 计算单个因子对单支股票的时序值。 */
export function useFactorBars(
  factorId: MaybeRefOrGetter<string>,
  params: MaybeRefOrGetter<Record<string, any>>,
  query: MaybeRefOrGetter<FactorBarQuery | null>,
) {
  return useQuery<FactorBarResponse>({
    queryKey: ['factor-bars', () => toValue(factorId), () => toValue(params), () => toValue(query)] as any,
    queryFn: async () => {
      const q = toValue(query)
      if (!q) throw new Error('no query')
      const fid = toValue(factorId)
      const p = toValue(params)
      const { data } = await client.get(`/factors/${fid}/bars`, {
        params: {
          symbol: q.symbol,
          start: q.start,
          end: q.end,
          freq: q.freq,
          params: JSON.stringify(p),
        },
      })
      return data as FactorBarResponse
    },
    enabled: () => {
      const q = toValue(query)
      const fid = toValue(factorId)
      return !!(q && q.symbol && fid)
    },
    staleTime: 0,
  })
}
```

Note: `useQuery`、`toValue`、`MaybeRefOrGetter`、`client` 已在文件顶部导入。

- [ ] **Step 2: 验证 TypeScript 编译**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | head -20
```

Expected: 无新增类型错误。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/api/klines.ts
git commit -m "feat(ui): add useFactorBars hook for factor time series queries"
```

---

### Task 4: 扩展 CandlestickChart 支持因子行

**Files:**
- Modify: `frontend/src/components/charts/CandlestickChart.vue`

- [ ] **Step 1: 新增 `factorRows` prop 和网格布局工具函数**

在 `<script setup>` 中，`LineChart` 注册之后追加：

```typescript
import { LineChart } from 'echarts/charts'
// 在 use([...]) 中追加 LineChart
```

在 `use([...])` 调用中添加 `LineChart`（约第 33 行）：

```typescript
use([
  CanvasRenderer,
  CandlestickChart,
  BarChart,
  LineChart,  // 新增
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
])
```

在 `props` 定义中（`volumes` 之后）追加：

```typescript
factorRows?: {
  name: string
  color: string
  dates: string[]
  values: (number | null)[]
}[]
```

在 `withDefaults` 的默认值对象中追加：

```typescript
factorRows: () => [],
```

- [ ] **Step 2: 编写网格布局计算函数**

在 `const colors = computed(...)` 之后追加：

```typescript
// 因子网格布局常量（N → 各区域百分比）
const FACTOR_LAYOUT: Record<number, { klineH: number; volTop: number; volH: number; factorTop: number; factorH: number }> = {
  0: { klineH: 60, volTop: 64, volH: 12, factorTop: 0, factorH: 0 },
  1: { klineH: 52, volTop: 56, volH: 12, factorTop: 70, factorH: 20 },
  2: { klineH: 48, volTop: 52, volH: 12, factorTop: 66, factorH: 13 },
  3: { klineH: 46, volTop: 50, volH: 12, factorTop: 64, factorH: 9 },
  4: { klineH: 44, volTop: 48, volH: 11, factorTop: 61, factorH: 7 },
  5: { klineH: 42, volTop: 46, volH: 10, factorTop: 58, factorH: 6 },
}
```

- [ ] **Step 3: 更新 `tooltipFormatter` 处理 line 系列**

在 `tooltipFormatter` 函数中，`else if (p.seriesType === 'bar')` 分支之后追加：

```typescript
} else if (p.seriesType === 'line') {
  const v = typeof p.data === 'object' ? p.data?.value : p.data
  const name = p.seriesName ?? ''
  const valStr = v != null && Number.isFinite(v) ? (v as number).toFixed(4) : '-'
  lines.push(
    `<div style="display:flex;gap:12px"><span>${name}</span><span>${valStr}</span></div>`,
  )
}
```

- [ ] **Step 4: 更新 `option` computed 动态构建 grids/series/xAxis/yAxis**

用新的完整 `option` computed 替换现有实现（核心变更：dynamic grid/series 构建）：

在现有 `option` computed 中，替换计算逻辑为：

```typescript
const option = computed(() => {
  const N = Math.min(props.factorRows?.length ?? 0, 5)
  const layout = FACTOR_LAYOUT[N]
  const chartHeight = 400 + N * 60

  // 构建 grids
  const grids: any[] = [
    { left: 60, right: 60, top: 40, height: layout.klineH + '%' },
    { left: 60, right: 60, top: layout.volTop + '%', height: layout.volH + '%' },
  ]
  for (let i = 0; i < N; i++) {
    grids.push({
      left: 60,
      right: 60,
      top: (layout.factorTop + i * layout.factorH) + '%',
      height: (layout.factorH - 1) + '%',
    })
  }

  // xAxis: K线 + 成交量 + 每个因子一个
  const xAxes: any[] = [
    {
      type: 'category', data: props.categories, boundaryGap: true,
      axisLine: { onZero: false }, axisLabel: { show: false }, splitLine: { show: false },
    },
    {
      type: 'category', gridIndex: 1, data: props.categories, boundaryGap: true,
      axisLabel: { rotate: 30, fontSize: 10 },
    },
  ]
  for (let i = 0; i < N; i++) {
    const isLast = i === N - 1
    xAxes.push({
      type: 'category', gridIndex: 2 + i, data: props.categories, boundaryGap: true,
      axisLabel: isLast ? { rotate: 30, fontSize: 10 } : { show: false },
      axisLine: { show: isLast },
    })
  }

  // yAxis: K线 + 成交量 + 每个因子独立
  const yAxes: any[] = [
    {
      scale: true, splitArea: { show: true },
      axisLabel: { formatter: (v: number) => (Number.isFinite(v) ? v.toFixed(2) : String(v)) },
    },
    {
      gridIndex: 1, scale: true, splitNumber: 2,
      axisLabel: { fontSize: 10 }, axisLine: { show: false }, splitLine: { show: false },
    },
  ]
  for (let i = 0; i < N; i++) {
    yAxes.push({
      gridIndex: 2 + i, scale: true,
      axisLabel: { fontSize: 10 },
      splitLine: { show: true, lineStyle: { color: '#333', type: 'dashed' as const } },
    })
  }

  // series: K线 + 成交量 + 因子 lines
  const allXAxisIndices = Array.from({ length: 2 + N }, (_, i) => i)
  const series: any[] = [
    {
      name: 'K 线', type: 'candlestick', data: candleData.value,
      itemStyle: { color: colors.value.up, color0: colors.value.down, borderColor: colors.value.up, borderColor0: colors.value.down },
    },
    {
      name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumeData.value,
    },
  ]
  for (let i = 0; i < N; i++) {
    const row = props.factorRows![i]
    series.push({
      name: row.name,
      type: 'line',
      xAxisIndex: 2 + i,
      yAxisIndex: 2 + i,
      data: row.values,
      symbol: 'none',
      lineStyle: { color: row.color, width: 1.5 },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#555', type: 'dashed' as const, width: 1 },
        data: [{ yAxis: 0 }],
      },
    })
  }

  return {
    animation: false,
    legend: { data: ['K 线', '成交量', ...(props.factorRows ?? []).map(r => r.name)], top: 5 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(30,32,38,0.95)',
      borderWidth: 0,
      textStyle: { color: '#fff', fontSize: 12 },
      formatter: tooltipFormatter,
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: allXAxisIndices, start: 0, end: 100 },
      { type: 'slider', xAxisIndex: allXAxisIndices, top: '94%', start: 0, end: 100 },
    ],
    series,
  }
})
```

- [ ] **Step 5: 动态图表高度**

将 template 中的 `style="width: 100%; height: 560px"` 改为动态高度。在 `option` computed 同级添加：

```typescript
const chartHeight = computed(() => {
  const N = Math.min(props.factorRows?.length ?? 0, 5)
  return (400 + N * 60) + 'px'
})
```

Template 中：

```html
<v-chart ref="chartRef" :option="option" autoresize :style="{ width: '100%', height: chartHeight }" />
```

- [ ] **Step 6: 验证 TypeScript 编译**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | head -30
```

Expected: 无新增类型错误。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/components/charts/CandlestickChart.vue
git commit -m "feat(ui): extend CandlestickChart with dynamic factor row grids"
```

---

### Task 5: KlineViewer 因子选择与参数编辑

**Files:**
- Modify: `frontend/src/pages/klines/KlineViewer.vue`

- [ ] **Step 1: 新增导入和状态定义**

在 `<script setup>` 顶部追加导入：

```typescript
import { NTag, NModal, NForm, NFormItem, NInputNumber, NDrawer, NDrawerContent, NScrollbar } from 'naive-ui'
import { useFactors } from '@/api/factors'
import { useFactorBars, type FactorBarQuery } from '@/api/klines'
```

在现有 `const message = useMessage()` 之后追加因子相关状态：

```typescript
// 因子选择与展示
const FACTOR_COLORS = ['#5dade2', '#e67e22', '#27ae60', '#9b59b6', '#f1c40f']

interface FactorSlot {
  factor_id: string
  display_name: string
  category: string
  params_schema: Record<string, any>
  default_params: Record<string, any>
  params: Record<string, any>
  color: string
}

const selectedFactors = ref<FactorSlot[]>([])
const factorListQuery = ref<{ keyword?: string }>({})
const { data: allFactors } = useFactors(factorListQuery)

// 参数编辑状态
const editingFactorIndex = ref<number | null>(null)
const editingParams = ref<Record<string, any>>({})

// 可选因子（过滤频率 + 已选）
const availableFactors = computed(() => {
  if (!allFactors.value) return []
  const alreadySelected = new Set(selectedFactors.value.map(s => s.factor_id))
  return allFactors.value.filter(
    f => f.supported_freqs.includes(freq.value) && !alreadySelected.has(f.factor_id),
  )
})

const canAddFactor = computed(() => selectedFactors.value.length < 5)

// 每个 slot 位置的查询参数
function slotQuery(index: number): ComputedRef<FactorBarQuery | null> {
  return computed<FactorBarQuery | null>(() => {
    const slot = selectedFactors.value[index]
    if (!slot || !symbol.value.trim()) return null
    const range = freq.value === '1d' ? dailyRange.value : minuteRange.value
    return {
      symbol: symbol.value.trim().toUpperCase(),
      start: toIso(range[0]),
      end: toIso(range[1]),
      freq: freq.value,
    }
  })
}

// 为 5 个 slot 位置各建一个 hook（无 slot 时 disabled）
const slot0Query = slotQuery(0)
const slot1Query = slotQuery(1)
const slot2Query = slotQuery(2)
const slot3Query = slotQuery(3)
const slot4Query = slotQuery(4)

const slotBars = [
  useFactorBars(
    computed(() => selectedFactors.value[0]?.factor_id ?? ''),
    computed(() => selectedFactors.value[0]?.params ?? {}),
    slot0Query,
  ),
  useFactorBars(
    computed(() => selectedFactors.value[1]?.factor_id ?? ''),
    computed(() => selectedFactors.value[1]?.params ?? {}),
    slot1Query,
  ),
  useFactorBars(
    computed(() => selectedFactors.value[2]?.factor_id ?? ''),
    computed(() => selectedFactors.value[2]?.params ?? {}),
    slot2Query,
  ),
  useFactorBars(
    computed(() => selectedFactors.value[3]?.factor_id ?? ''),
    computed(() => selectedFactors.value[3]?.params ?? {}),
    slot3Query,
  ),
  useFactorBars(
    computed(() => selectedFactors.value[4]?.factor_id ?? ''),
    computed(() => selectedFactors.value[4]?.params ?? {}),
    slot4Query,
  ),
]
```

- [ ] **Step 2: 添加因子操作函数**

```typescript
// 添加因子
function addFactor(factorId: string) {
  if (!canAddFactor.value) return
  const factor = allFactors.value?.find(f => f.factor_id === factorId)
  if (!factor) return
  const slot: FactorSlot = {
    factor_id: factor.factor_id,
    display_name: factor.display_name,
    category: factor.category,
    params_schema: factor.params_schema,
    default_params: { ...factor.default_params },
    params: { ...factor.default_params },
    color: FACTOR_COLORS[selectedFactors.value.length],
  }
  selectedFactors.value = [...selectedFactors.value, slot]
}

// 移除因子
function removeFactor(index: number) {
  const next = [...selectedFactors.value]
  next.splice(index, 1)
  // 重新分配颜色
  next.forEach((s, i) => { s.color = FACTOR_COLORS[i] })
  selectedFactors.value = next
}

// 打开参数编辑
function openParamEditor(index: number) {
  editingFactorIndex.value = index
  editingParams.value = { ...selectedFactors.value[index].params }
}

// 应用参数
function applyParams() {
  if (editingFactorIndex.value === null) return
  const idx = editingFactorIndex.value
  const next = [...selectedFactors.value]
  next[idx] = { ...next[idx], params: { ...editingParams.value } }
  selectedFactors.value = next
  editingFactorIndex.value = null
}

// 重置参数为默认值
function resetParams() {
  if (editingFactorIndex.value === null) return
  const slot = selectedFactors.value[editingFactorIndex.value]
  editingParams.value = { ...slot.default_params }
}

// 频率切换时清除不兼容因子
watch(freq, (newFreq) => {
  const removed: string[] = []
  const kept = selectedFactors.value.filter(s => {
    const factor = allFactors.value?.find(f => f.factor_id === s.factor_id)
    const ok = factor?.supported_freqs.includes(newFreq)
    if (!ok) removed.push(s.display_name)
    return ok
  })
  if (removed.length > 0) {
    selectedFactors.value = kept.map((s, i) => ({ ...s, color: FACTOR_COLORS[i] }))
    message.warning(`${removed.join('、')} 不支持当前频率，已自动移除`)
  }
})
```

- [ ] **Step 3: 添加 factorRows computed**

在现有 `chartData` computed 之后追加：

```typescript
const factorRows = computed(() =>
  slotBars
    .map((q, i) => ({ slot: selectedFactors.value[i], data: q.data.value }))
    .filter(x => x.slot && x.data && x.data.dates.length > 0)
    .map(x => ({
      name: x.slot!.display_name,
      color: x.slot!.color,
      dates: x.data!.dates,
      values: x.data!.values,
    }))
)
```

- [ ] **Step 4: 更新模板 —— 工具栏增加因子选择器**

在现有 `n-select`（`adjust`）与 `n-date-picker` 之间增加因子选择器 + chips：

```html
<n-select
  v-model:value="adjust"
  :options="[...]"
  style="width: 160px"
/>

<!-- 因子选择器 -->
<n-select
  v-model:value="selectedFactorToAdd"
  :options="availableFactors.map(f => ({ label: `${f.display_name} (${f.factor_id})`, value: f.factor_id }))"
  placeholder="+ 添加因子"
  :disabled="!canAddFactor"
  filterable
  clearable
  style="width: 180px"
  @update:value="(val: string) => { if (val) { addFactor(val); selectedFactorToAdd = null } }"
/>

<!-- 因子 chips -->
<n-tag
  v-for="(slot, idx) in selectedFactors"
  :key="slot.factor_id"
  :bordered="true"
  :color="{ color: slot.color, borderColor: slot.color }"
  closable
  @close="removeFactor(idx)"
  style="cursor: pointer; margin-right: 4px;"
>
  {{ slot.display_name }}
  <template #avatar>
    <span @click.stop="openParamEditor(idx)" style="cursor: pointer; opacity: 0.7;">&#x2699;</span>
  </template>
</n-tag>
```

在 `<script setup>` 中追加：

```typescript
const selectedFactorToAdd = ref<string | null>(null)
```

- [ ] **Step 5: 更新模板 —— 传递 factorRows 给 CandlestickChart**

在现有 `<candlestick-chart>` 组件上追加 prop：

```html
<candlestick-chart
  :categories="chartData.categories"
  :ohlc="chartData.ohlc"
  :volumes="chartData.volumes"
  :color-mode="colorMode"
  :factor-rows="factorRows"
/>
```

- [ ] **Step 6: 添加参数编辑抽屉**

在 `</n-spin>` 之后、`</div>` 之前追加：

```html
<n-drawer v-model:show="showParamDrawer" :width="360" placement="right">
  <n-drawer-content title="因子参数" closable>
    <template v-if="editingFactorIndex !== null">
      <n-form label-placement="top" :model="editingParams">
        <n-form-item
          v-for="(meta, key) in selectedFactors[editingFactorIndex]?.params_schema ?? {}"
          :key="key"
          :label="`${key} (${meta.type ?? 'int'}${meta.min != null ? ', ' + meta.min + '~' + meta.max : ''})`"
        >
          <n-select
            v-if="meta.options"
            v-model:value="editingParams[key]"
            :options="meta.options.map((o: any) => ({ label: String(o), value: o }))"
          />
          <n-input-number
            v-else
            v-model:value="editingParams[key]"
            :min="meta.min"
            :max="meta.max"
            :step="meta.type === 'float' ? 0.01 : 1"
          />
        </n-form-item>
      </n-form>
      <n-space justify="end" style="margin-top: 16px">
        <n-button quaternary @click="resetParams">重置默认</n-button>
        <n-button type="primary" @click="applyParams">应用</n-button>
      </n-space>
    </template>
  </n-drawer-content>
</n-drawer>
```

在 `<script setup>` 中追加：

```typescript
const showParamDrawer = computed({
  get: () => editingFactorIndex.value !== null,
  set: (v) => { if (!v) editingFactorIndex.value = null },
})
```

- [ ] **Step 7: 验证 TypeScript 编译**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 8: 提交**

```bash
git add frontend/src/pages/klines/KlineViewer.vue
git commit -m "feat(ui): add factor selector, chips, and parameter editing to KlineViewer"
```

---

### Task 6: 集成验证与手动测试

**Files:**
- No new files — run integration tests and manual smoke test

- [ ] **Step 1: 运行后端集成测试**

```bash
cd backend && python -m pytest tests/test_api_factor_bars.py -v
```

Expected: 5 PASS

- [ ] **Step 2: 启动前后端，手动验证关键场景**

启动后端 + 前端后验证：

1. 访问 K 线查看页，输入 `000001.SZ`，日线模式 → 应显示 K 线和成交量
2. 点击 "+添加因子"，搜索 "reversal" → 下拉列表只显示 `supported_freqs` 含 `1d` 的因子
3. 选择 "reversal_n" → chip 出现，成交量下方出现折线 + 零轴虚线
4. 再添加 "momentum_n" → 第二个因子行出现，两行颜色不同
5. 缩放 K 线 → 因子行同步平移
6. 悬停因子行 → tooltip 显示日期 + 因子名 + 4 位小数
7. 点击 chip 上的 ⚙ → 参数抽屉打开 → 修改 n=10 → 应用 → 折线刷新
8. 点击 chip 上的 ✕ → 因子行移除
9. 添加 5 个因子 → "+添加因子" 变灰
10. 切换到分钟线 → 不兼容因子自动清除 → toast 提示

- [ ] **Step 3: 提交（如有修复）**

```bash
git add -A
git commit -m "chore: integration verification fixes"
```

---

## 自检

**1. Spec 覆盖检查：**

| Spec 需求 | 对应 Task |
|-----------|----------|
| 因子选择器（搜索 + 频率过滤） | Task 5 Step 4 |
| 动态添加/删除（chips） | Task 5 Step 2, 4 |
| 频率感知（切换清除） | Task 5 Step 2 (watch freq) |
| 缩放联动 | Task 4 Step 4 (dataZoom all indices) |
| 参数支持（default + gear） | Task 5 Step 2, 6 |
| 零轴参考线 | Task 4 Step 4 (markLine yAxis:0) |
| Tooltip（日期+因子名+4位小数） | Task 4 Step 3 |
| 上限 5 个 | Task 5 Step 1 (canAddFactor) |
| 后端端点 | Task 1 + 2 |
| 图表高度动态调整 | Task 4 Step 2, 5 |

**2. 占位符扫描：** 无 TBD/TODO，所有代码块完整。

**3. 类型一致性：** `FactorSlot` 在 Task 5 定义，`factorRows` prop 在 Task 4 消费；`FactorBarResponse` 在 Task 3 定义，后端在 Task 2 产出一致的 JSON 结构。类型/字段名全链路对齐。
