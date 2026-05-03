# K线查看页 —— 因子选择与查看功能

**日期**: 2026-05-03
**状态**: 已设计，待实现

## 概述

在 K 线查看页面（`KlineViewer.vue`）增加因子选择与数据查看功能。用户在查看某只股票的日线/分钟线时，可选择支持对应频率的因子，在成交量下方渲染因子折线图，与 K 线共享 X 轴和数据缩放。

## 需求

1. **因子选择器**：工具栏新增可搜索下拉选择器，只列出 `supported_freqs` 包含当前 K 线频率的因子
2. **动态添加/删除**：已选因子以彩色 chip 展示，可点击 x 移除；最多同时展示 5 个因子
3. **频率感知**：切换 K 线频率（1d ↔ 1m）时，自动清除不兼容的因子
4. **缩放联动**：因子行与 K 线、成交量共享 X 轴和 dataZoom，同步平移/缩放
5. **参数支持**：默认使用 `default_params`，每个 chip 旁有 gear 按钮可自定义参数
6. **零轴参考线**：因子折线图含 y=0 虚线参考线
7. **Tooltip**：悬停显示日期 + 因子名称 + 因子值（4 位小数）

## 架构

```
KlineViewer.vue (状态中心)
  ├─ 因子选择器 (naive-ui NSelect)
  ├─ 因子 chips 展示 + 参数编辑抽屉
  ├─ CandlestickChart.vue (单 ECharts 实例)
  │   ├─ K线 grid
  │   ├─ 成交量 grid
  │   └─ 因子 grid[0..N-1] (动态增减)
  └─ useFactorBars() × N (每个因子独立 hook)

后端:
  GET /api/factors/{id}/bars  ← 新端点
    → FactorRegistry.get() → validate freq/params
    → FactorContext(symbols=[单股]) → factor.compute()
    → 提取目标 symbol 列 → {dates, values}
```

## 改动范围

| 文件 | 类型 | 说明 |
|------|------|------|
| `frontend/src/pages/klines/KlineViewer.vue` | 修改 | 因子选择器、chips、参数编辑、selectedFactors 状态 |
| `frontend/src/components/charts/CandlestickChart.vue` | 修改 | 新增 `factorRows` prop，动态 grid/series/yAxis |
| `frontend/src/api/klines.ts` | 修改 | 新增 `useFactorBars` hook |
| `backend/api/routers/factors.py` | 新增路由 | `GET /api/factors/{id}/bars` 端点 |

## CandlestickChart 动态网格布局

图表高度：`400px + N × 60px`（N 为因子数，0~5）

网格分配（百分比定位）：

| N | K线高度 | 成交量 top/height | 因子区 top | 每个因子 height |
|---|---------|-------------------|-----------|----------------|
| 0 | 60% | 64% / 12% | — | — |
| 1 | 52% | 56% / 12% | 70% | 20% |
| 2 | 48% | 52% / 12% | 66% | 13% |
| 3 | 46% | 50% / 12% | 64% | 9% |
| 4 | 44% | 48% / 11% | 61% | 7% |
| 5 | 42% | 46% / 10% | 58% | 6% |

每个因子行包含：
- 1 个 `grid`（left:60, right:60, top/height 如上）
- 1 个 `xAxis`（category，共享 categories 数据，仅最后一行显示 axisLabel）
- 1 个 `yAxis`（scale:true，独立刻度）
- 1 个 `line` series（因子值折线，`null` 值断开）
- 1 条 `markLine`（`yAxis: 0`，灰色虚线）

### factorRows Props

```typescript
interface FactorRowData {
  name: string           // factor display_name
  color: string          // 分配颜色
  dates: string[]        // 与 categories 对齐
  values: (number | null)[]  // null 表示断点
}
```

## 因子选择器 UI

工具栏布局（从左到右）：`[symbol输入] | [频率] [复权] | [+添加因子] | [chip1] [chip2] ... | [刷新]`

- 因子下拉：搜索框 + 列表项显示 `display_name` + `factor_id` + `supported_freqs` 标签
- 未匹配频率的因子灰显不可选
- 已选因子不出现在下拉列表中
- 达到 5 个上限时 "+添加因子" 变灰

### 因子 Chip

```
[display_name] [⚙] [✕]
```

- ⚙：打开参数编辑弹窗（根据 `params_schema` 动态生成表单）
- ✕：移除该因子（同时删除对应图表行）
- 颜色固定 5 色调色板：`#5dade2, #e67e22, #27ae60, #9b59b6, #f1c40f`

### 参数编辑

弹窗/抽屉形式，根据 `params_schema` 渲染表单：
- `type: int` → number input（带 min/max 限制）
- `type: float` → number input（step=0.01）
- `type: enum` → select
- "重置默认"按钮恢复为 `default_params`
- "应用"按钮触发因子数据重新获取

## 频率切换行为

当用户切换 K 线频率时：
1. 遍历 `selectedFactors`，过滤掉 `supported_freqs` 不包含新频率的因子
2. 被移除的因子显示 toast 提示（如 "reversal_n 不支持分钟线，已自动移除"）
3. 保留的因子自动用新频率重新获取数据

## 后端端点

### `GET /api/factors/{factor_id}/bars`

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| symbol | string | 是 | 股票代码，如 `000001.SZ` |
| start | date | 是 | 起始日期 |
| end | date | 是 | 结束日期 |
| freq | string | 是 | `1d` 或 `1m` |
| params | string | 否 | JSON 编码参数字典 |

**校验流程**：
1. `FactorRegistry.get(factor_id)` → 404 如果不存在
2. `freq not in factor.supported_freqs` → 400
3. 解析 params JSON → `factor.validate_params()` → 400 如果非法
4. 解析 symbol（`DataService.resolver`）→ 404 如果未知

**计算流程**：
1. `warmup = factor.required_warmup(params)`
2. `ctx = FactorContext(data, symbols=[symbol], start, end, warmup)`
3. `F = factor.compute(ctx, params)` → 宽表 DataFrame
4. 提取 `F[symbol]` 列 → Series → 转 `{dates, values}`

**响应**：

```json
{
  "factor_id": "reversal_n",
  "symbol": "000001.SZ",
  "freq": "1d",
  "params": {"n": 5},
  "params_hash": "abc123",
  "dates": ["2025-10-01", "2025-10-02", "..."],
  "values": [0.031, -0.012, null, "..."],
  "version": 3
}
```

**缓存策略**：不走 `factor_value_1d` 表缓存。单股 + 有限窗口（日线 ≤ 180 天 / 分钟线 ≤ 5 天），直接 compute 的开销可接受。若后续性能有问题，可加 Redis 短期缓存（key = `factor_id:version:params_hash:symbol:start:end:freq`）。

## 前端数据流

### useFactorBars hook

```typescript
// klines.ts 新增
export function useFactorBars(
  factorId: MaybeRefOrGetter<string>,
  params: MaybeRefOrGetter<Record<string, any>>,
  query: MaybeRefOrGetter<FactorBarQuery | null>,
)
```

- `queryKey`: `['factor-bars', factorId, params, query]`
- `enabled`: `query.symbol` 非空
- `staleTime: 0`（参数/窗口变化立即失效）
- 返回 `{ factor_id, dates, values, version }`

### KlineViewer 中的使用

```typescript
const selectedFactors = ref<FactorSlot[]>([])

// 每个 slot 独立 hook
// 通过 computed 将 slot 的数据查询参数化
// watch slot.params 变化 → 自动 refetch
```

`chartData` computed 扩展：

```typescript
const factorRows = computed(() =>
  selectedFactors.value
    .filter(s => s.data !== null)
    .map(s => ({
      name: s.display_name,
      color: s.color,
      dates: s.data!.dates,
      values: s.data!.values,
    }))
)
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 因子不支持当前频率 | 下拉列表不显示该因子；切换频率时自动移除 |
| 后端 compute 失败 | 因子行显示错误占位，chip 变红，tooltip 显示错误信息 |
| 返回空数据 | 因子行显示 "无数据" 占位文字 |
| params 校验失败 | 参数弹窗内联显示错误信息，禁止提交 |
| symbol 解析失败 | toast 错误提示 |

## 测试要点

1. 切换频率（1d ↔ 1m）时因子列表过滤正确
2. 添加/删除因子 → grid 数量动态变化 → 图表高度调整
3. 参数修改 → refetch → 折线数据更新
4. 5 个因子上限 → "+添加因子" 禁用
5. dataZoom 联动：缩放 K 线区域 → 因子行同步平移
6. tooltip 悬停因子行 → 显示日期 + 因子名 + 4 位小数值
7. 零轴参考线正确显示
8. null 值在折线中正确断开
9. 分钟线不支持 1d-only 因子 → 自动清除
