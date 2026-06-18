<script setup lang="ts">
/**
 * K 线图（candlestick + 成交量双图联动）。
 *
 * 轴/数据约定：
 * - x 轴：调用方传入字符串数组（日线 "YYYY-MM-DD"；分钟线 "YYYY-MM-DD HH:MM"）；
 *   直接作为 ECharts category，避免时区 + 非交易时段空白的麻烦。
 * - candlestick data 顺序：[open, close, low, high]（ECharts 约定，与 OHLC 不同）。
 * - 成交量副图用柱状图，颜色跟随涨跌：close>=open 用"涨色"，否则"跌色"。
 *
 * 涨跌颜色由 props.colorMode 决定：
 * - 'a-share'（默认）：A 股习惯——红涨绿跌
 * - 'binance'：币圈 / 港美股——绿涨红跌
 *
 * 联动：两个 grid 共享 xAxisIndex，dataZoom 同步。
 *
 * 失焦修复：visibilitychange（alt+tab 回来）时主动 hideTip + resize，
 * 避免 axisPointer 卡在旧坐标、canvas 尺寸错位导致整个图不响应鼠标。
 *
 * dataZoom 管理策略：
 * - dataZoom 不放在 computed option 里，而是通过 dispatchAction 独立管理。
 * - 原因：option 是 computed，VP 变化会触发整个 option 重算 → vue-echarts setOption
 *   → ECharts 内部 reset dataZoom → 缩放位置丢失。
 * - dataZoomRange ref 追踪当前位置，用户操作 slider 时更新，VP 重绘后通过
 *   dispatchAction 恢复。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { CandlestickChart, BarChart, LineChart, CustomChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
  BrushComponent,
  ToolboxComponent,
  MarkPointComponent,
  MarkAreaComponent,
} from 'echarts/components'
import { ScatterChart } from 'echarts/charts'
import VChart from 'vue-echarts'

use([
  CanvasRenderer,
  CandlestickChart,
  BarChart,
  LineChart,
  CustomChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
  BrushComponent,
  ToolboxComponent,
  MarkPointComponent,
  MarkAreaComponent,
])

type ColorMode = 'a-share' | 'binance'

interface ChanlunFx { dt: string; mark: 'top' | 'bottom'; price: number; high: number; low: number }
interface ChanlunBi { sdt: string; edt: string; direction: 'up' | 'down'; high: number; low: number }
interface ChanlunZs { sdt: string; edt: string; zg: number; zd: number; gg: number; dd: number }
interface ChanlunBsp { dt: string; bsp_type: string; price: number }
interface ChanlunOverlay {
  fx_list: ChanlunFx[]
  bi_list: ChanlunBi[]
  zs_list: ChanlunZs[]
  bsp_list: ChanlunBsp[]
}

const props = withDefaults(
  defineProps<{
    /** 时间轴类目（字符串），长度 N。 */
    categories: string[]
    /** OHLC 序列，每条 [open, high, low, close]；调用方统一这个顺序，组件内再重排。 */
    ohlc: [number, number, number, number][]
    /** 成交量序列，长度 N（与 categories 对齐）。 */
    volumes: number[]
    /** 涨跌配色。默认 A 股红涨绿跌；切成 'binance' 是绿涨红跌。 */
    colorMode?: ColorMode
    /** 因子行数据。每个因子一个条目，含名称、颜色、日期、值。 */
    factorRows?: {
      name: string
      color: string
      dates: string[]
      values: (number | null)[]
    }[]
    /** 是否显示成交量剖面。由父组件控制。 */
    showVolumeProfile?: boolean
    /** 框选找相似模式。开启后可在图上拖拽框选一段走势，冒出「找相似」按钮。 */
    selectMode?: boolean
    /** 框选缩放模式。开启后拖拽横向区间 → 缩放到该区间。 */
    zoomSelectMode?: boolean
    /** 缠论叠加数据。传入后在 K 线上叠加分型/笔/中枢/买卖点。 */
    chanlun?: ChanlunOverlay | null
  }>(),
  { colorMode: 'a-share', factorRows: () => [], showVolumeProfile: false, selectMode: false, zoomSelectMode: false, chanlun: null },
)

const emit = defineEmits<{
  (e: 'update:vpData', data: { startIdx: number; endIdx: number } | null): void
  (e: 'find-similar', payload: { start: string; end: string }): void
  (e: 'request-expand'): void

}>()

// 价格保留两位小数；成交量直接取整后加千分位（避免把 1,234,567 写成 1.2M 失真）。
function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '-'
  return v.toFixed(2)
}
function fmtVolume(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '-'
  return Math.round(v).toLocaleString('en-US')
}

// 根据 colorMode 决定涨/跌色。A 股红涨绿跌；Binance 风格绿涨红跌。
const colors = computed(() => {
  if (props.colorMode === 'binance') {
    return { up: '#0ECB81', down: '#F6465D' }
  }
  return { up: '#F6465D', down: '#0ECB81' }
})

// Factor grid layout: N → percentage allocation for each region.
// All values are percentages; K-line grid top is '5%' (legend space).
// Gaps: ~2-3% between K-line/volume and volume/first-factor.
const FACTOR_LAYOUT: Record<number, { klineH: number; volTop: number; volH: number; factorTop: number; factorH: number }> = {
  0: { klineH: 62, volTop: 70, volH: 20, factorTop: 0, factorH: 0 },
  1: { klineH: 50, volTop: 58, volH: 16, factorTop: 76, factorH: 18 },
  2: { klineH: 44, volTop: 52, volH: 14, factorTop: 68, factorH: 13 },
  3: { klineH: 38, volTop: 46, volH: 12, factorTop: 62, factorH: 10 },
  4: { klineH: 34, volTop: 42, volH: 11, factorTop: 57, factorH: 9  },
  5: { klineH: 30, volTop: 38, volH: 10, factorTop: 52, factorH: 8  },
}

// ECharts 要的是 [open, close, low, high]；重排。
const candleData = computed(() =>
  props.ohlc.map(([o, h, l, c]) => [o, c, l, h]),
)

// 柱子按涨跌上色；close >= open 用"涨色"。
const volumeData = computed(() =>
  props.volumes.map((v, i) => {
    const [o, , , c] = props.ohlc[i] ?? [0, 0, 0, 0]
    return {
      value: v,
      itemStyle: { color: c >= o ? colors.value.up : colors.value.down },
    }
  }),
)

// ---- Volume Profile (成交量剖面) ----
interface VPBar { y: number; width: number; color: string }
interface VPData { bars: VPBar[]; bucketSize: number; priceMin: number; priceMax: number }

const volumeProfile = ref<VPData | null>(null)
const selectedRange = ref<{ startIdx: number; endIdx: number } | null>(null)

// 框选了至少 2 天 → 显示「找相似」按钮。
const hasSelection = computed(
  () => selectedRange.value != null && selectedRange.value.endIdx > selectedRange.value.startIdx,
)

// 把框选段映射成 trade_date（分钟线 "YYYY-MM-DD HH:MM" 取日期部分），emit 给父组件检索。
function emitFindSimilar() {
  const sel = selectedRange.value
  if (!sel) return
  const lo = Math.max(0, sel.startIdx)
  const hi = Math.min(props.categories.length - 1, sel.endIdx)
  const start = (props.categories[lo] ?? '').split(' ')[0]
  const end = (props.categories[hi] ?? '').split(' ')[0]
  if (!start || !end) return
  emit('find-similar', { start, end })
}

// dataZoom 位置追踪：option 里声明 dataZoom 组件（slider 才能渲染），
// 但 start/end 不写死，由 watch(option) + dispatchAction 在每次重算后恢复。
const dataZoomRange = ref({ start: 0, end: 100 })

function calcVolumeProfile(indices: number[]): VPData | null {
  if (indices.length === 0) return null
  let lo = Infinity, hi = -Infinity
  for (const i of indices) {
    const [, h, l] = props.ohlc[i] ?? [0, 0, 0, 0]
    if (l < lo) lo = l
    if (h > hi) hi = h
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null

  const range = hi - lo
  const bucketCount = 25
  const bucketSize = range / bucketCount
  const buckets = new Array<number>(bucketCount).fill(0)
  const bucketColors = new Array<string>(bucketCount).fill(colors.value.up)

  for (const i of indices) {
    const [o, h, l, c] = props.ohlc[i] ?? [0, 0, 0, 0]
    const v = props.volumes[i] ?? 0
    const typical = (h + l + c) / 3
    const bi = Math.min(Math.floor((typical - lo) / bucketSize), bucketCount - 1)
    buckets[bi] += v
    if (c < o) bucketColors[bi] = colors.value.down
  }

  const maxVol = Math.max(...buckets)
  if (maxVol <= 0) return null

  const bars: VPBar[] = buckets.map((vol, i) => ({
    y: lo + (i + 0.5) * bucketSize,
    width: vol / maxVol,
    color: bucketColors[i],
  }))
  return { bars, bucketSize, priceMin: lo, priceMax: hi }
}

// tooltip.formatter：中文标签 + 两位小数。
function tooltipFormatter(params: any[]): string {
  if (!Array.isArray(params) || params.length === 0) return ''
  const axisValue = params[0].axisValue ?? ''
  const lines: string[] = [`<div style="margin-bottom:4px;font-weight:600">${axisValue}</div>`]
  for (const p of params) {
    if (p.seriesType === 'candlestick') {
      const raw = p.data ?? []
      const open = raw[1]
      const close = raw[2]
      const low = raw[3]
      const high = raw[4]
      const up = Number(close) >= Number(open)
      const color = up ? colors.value.up : colors.value.down
      const arrow = up ? '▲' : '▼'
      lines.push(
        `<div style="display:flex;gap:12px"><span>开</span><span>${fmtPrice(open)}</span></div>`,
        `<div style="display:flex;gap:12px"><span>高</span><span>${fmtPrice(high)}</span></div>`,
        `<div style="display:flex;gap:12px"><span>低</span><span>${fmtPrice(low)}</span></div>`,
        `<div style="display:flex;gap:12px"><span>收</span><span style="color:${color}">${fmtPrice(close)} ${arrow}</span></div>`,
      )
    } else if (p.seriesType === 'bar') {
      const v = typeof p.data === 'object' ? p.data?.value : p.data
      lines.push(
        `<div style="display:flex;gap:12px"><span>量</span><span>${fmtVolume(v)}</span></div>`,
      )
    } else if (p.seriesType === 'line') {
      const v = typeof p.data === 'object' ? (p.data as any)?.value : p.data
      const name = p.seriesName ?? ''
      const valStr = v != null && Number.isFinite(v) ? (v as number).toFixed(4) : '-'
      lines.push(
        `<div style="display:flex;gap:12px"><span>${name}</span><span>${valStr}</span></div>`,
      )
    }
  }
  return lines.join('')
}

// ---- 缠论叠加 series 生成 ----
function buildChanlunSeries(cats: string[], cl: ChanlunOverlay): any[] {
  const catIdx = new Map<string, number>()
  cats.forEach((c, i) => catIdx.set(c, i))

  const series: any[] = []

  // 笔：折线连接各笔端点
  if (cl.bi_list.length > 0) {
    const biPoints: [number, number][] = []
    for (const bi of cl.bi_list) {
      const si = catIdx.get(bi.sdt)
      const ei = catIdx.get(bi.edt)
      if (si == null || ei == null) continue
      const startPrice = bi.direction === 'up' ? bi.low : bi.high
      const endPrice = bi.direction === 'up' ? bi.high : bi.low
      if (biPoints.length === 0 || biPoints[biPoints.length - 1][0] !== si) {
        biPoints.push([si, startPrice])
      }
      biPoints.push([ei, endPrice])
    }
    // 转换为 category 坐标系的稀疏数据
    const biData: (number | null)[] = new Array(cats.length).fill(null)
    for (const [idx, price] of biPoints) biData[idx] = price
    series.push({
      name: '缠论笔',
      type: 'line',
      z: 10,
      symbol: 'circle',
      symbolSize: 4,
      connectNulls: true,
      lineStyle: { color: '#FF6600', width: 1.5 },
      itemStyle: { color: '#FF6600' },
      data: biData,
      silent: true,
    })
  }

  // 中枢：用 markArea 矩形
  if (cl.zs_list.length > 0) {
    const areas = cl.zs_list.map(zs => [
      { xAxis: zs.sdt, yAxis: zs.zd },
      { xAxis: zs.edt, yAxis: zs.zg },
    ])
    series.push({
      name: '缠论中枢',
      type: 'line',
      data: [],
      silent: true,
      markArea: {
        silent: true,
        itemStyle: { color: 'rgba(100, 149, 237, 0.15)', borderColor: 'rgba(100, 149, 237, 0.6)', borderWidth: 1 },
        data: areas,
      },
    })
  }

  // 买卖点：scatter — 同一 dt 的多个信号上下错开展示
  if (cl.bsp_list.length > 0) {
    const BSP_STYLE: Record<string, { symbol: string; color: string; label: string }> = {
      buy1:  { symbol: 'triangle',      color: '#FF2200', label: 'B1' },
      buy2:  { symbol: 'triangle',      color: '#FF6600', label: 'B2' },
      buy3:  { symbol: 'triangle',      color: '#FF9900', label: 'B3' },
      sell1: { symbol: 'pin',           color: '#00CC44', label: 'S1' },
      sell2: { symbol: 'pin',           color: '#00AA88', label: 'S2' },
      sell3: { symbol: 'pin',           color: '#009999', label: 'S3' },
    }
    // 按 dt 分组，计算同一位置的偏移行号
    const dtGroups = new Map<string, { bsp: ChanlunBsp; buyRow: number; sellRow: number }[]>()
    for (const p of cl.bsp_list) {
      if (!catIdx.has(p.dt)) continue
      if (!dtGroups.has(p.dt)) dtGroups.set(p.dt, [])
      dtGroups.get(p.dt)!.push({ bsp: p, buyRow: 0, sellRow: 0 })
    }
    // 为同一 dt 下的买/卖信号分配行号
    for (const items of dtGroups.values()) {
      let buyIdx = 0, sellIdx = 0
      for (const item of items) {
        if (item.bsp.bsp_type.startsWith('buy')) {
          item.buyRow = buyIdx++
        } else {
          item.sellRow = sellIdx++
        }
      }
    }
    const ROW_OFFSET_PX = 24
    const scatterData = Array.from(dtGroups.values()).flat().map(({ bsp: p, buyRow, sellRow }) => {
      const style = BSP_STYLE[p.bsp_type] ?? { symbol: 'circle', color: '#999', label: '?' }
      const isBuy = p.bsp_type.startsWith('buy')
      const row = isBuy ? buyRow : sellRow
      // 买信号向下偏移，卖信号向上偏移；每多一行多偏移 ROW_OFFSET_PX 像素
      const yOffset = isBuy ? (10 + row * ROW_OFFSET_PX) : -(10 + row * ROW_OFFSET_PX)
      return {
        value: [p.dt, p.price],
        symbol: style.symbol,
        symbolSize: 16,
        symbolRotate: isBuy ? 0 : 180,
        symbolOffset: [0, yOffset],
        itemStyle: { color: style.color },
        label: {
          show: true,
          formatter: style.label,
          position: isBuy ? 'bottom' : 'top',
          fontSize: 10,
          fontWeight: 'bold',
          color: style.color,
          offset: [0, isBuy ? row * ROW_OFFSET_PX : -(row * ROW_OFFSET_PX)],
        },
      }
    })
    series.push({
      name: '买卖点',
      type: 'scatter',
      z: 20,
      data: scatterData,
      silent: true,
    })
  }

  // 分型标记：小三角
  if (cl.fx_list.length > 0) {
    const fxData = cl.fx_list
      .filter(f => catIdx.has(f.dt))
      .map(f => ({
        value: [f.dt, f.mark === 'top' ? f.high : f.low],
        symbol: f.mark === 'top' ? 'pin' : 'triangle',
        symbolSize: 8,
        symbolRotate: f.mark === 'top' ? 180 : 0,
        itemStyle: {
          color: f.mark === 'top' ? 'rgba(0,200,80,0.6)' : 'rgba(255,80,0,0.6)',
          borderWidth: 0,
        },
      }))
    series.push({
      name: '分型',
      type: 'scatter',
      z: 8,
      data: fxData,
      silent: true,
    })
  }

  return series
}

// ---- computed option：dataZoom 声明但不含 start/end，由 watch(option) + dispatchAction 恢复位置 ----
const option = computed(() => {
  const N = Math.min(props.factorRows?.length ?? 0, 5)
  const layout = FACTOR_LAYOUT[N]
  const vpOn = props.showVolumeProfile

  // ---- grids ----
  const grids: any[] = [
    { left: 60, right: 60, top: '5%', height: layout.klineH + '%' },
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

  // ---- xAxis ----
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
  // VP: hidden value xAxis on grid 0, for horizontal bar positioning
  const vpXAxisIndex = 2 + N
  xAxes.push({ type: 'value', gridIndex: 0, show: false, min: 0, max: 1 })

  // ---- yAxis ----
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
      splitLine: { show: false },
    })
  }

  // ---- series ----
  const series: any[] = [
    {
      name: 'K 线', type: 'candlestick', data: candleData.value,
      itemStyle: {
        color: colors.value.up, color0: colors.value.down,
        borderColor: colors.value.up, borderColor0: colors.value.down,
      },
      // 大数据优化：超过阈值后走 large 渲染管线，几千~上万根蜡烛也不卡/白屏。
      // candlestick 的 large 模式保留涨跌色（用 series 级 color/color0），不丢配色。
      large: true,
      largeThreshold: 600,
      progressive: 4000,
      progressiveThreshold: 4000,
    },
    {
      name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumeData.value,
      large: true,
      largeThreshold: 600,
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
      // 长区间因子线按 LTTB 降采样渲染，保形又省点。
      sampling: 'lttb',
      lineStyle: { color: row.color, width: 1.5 },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#555', type: 'dashed' as const, width: 1 },
        data: [{ yAxis: 0 }],
      },
    })
  }

  // Volume profile: overlay on K-line grid, same y-axis for perfect price alignment
  if (vpOn && volumeProfile.value) {
    const vp = volumeProfile.value
    const inst = chartRef.value as any
    series.push({
      name: 'VP',
      type: 'custom',
      xAxisIndex: vpXAxisIndex,
      yAxisIndex: 0,
      data: vp.bars.map(b => [b.width, b.y]),
      silent: true,
      renderItem(params: any, api: any) {
        const price = api.value(1)
        const volRatio = api.value(0)
        const gridRect = params.coordSys?.x != null ? params.coordSys : null
        if (!gridRect) return { type: 'group', children: [] }
        const rightEdgeX = gridRect.x + gridRect.width
        const pricePixel = inst ? inst.convertToPixel('grid', [0, price]) : null
        if (!pricePixel) return { type: 'group', children: [] }
        const barPxH = Math.max(1, (api.size ? api.size([0, vp.bucketSize])[1] : 10) * 0.85)
        const maxBarWidth = gridRect.width * 0.3
        const pxWidth = Math.max(0, maxBarWidth * volRatio)
        const bar = vp.bars.find(b => Math.abs(b.y - price) < 0.0001)
        return {
          type: 'rect',
          shape: { x: rightEdgeX - pxWidth, y: pricePixel[1] - barPxH / 2, width: pxWidth, height: barPxH },
          style: {
            fill: bar?.color ?? '#5dade2',
            opacity: 0.45,
            stroke: bar?.color ?? '#5dade2',
            lineWidth: 0.3,
          },
        }
      },
      z: 5,
    })
  }

  // 缠论叠加
  if (props.chanlun) {
    series.push(...buildChanlunSeries(props.categories, props.chanlun))
  }

  // VP / 框选找相似 / 框选缩放 任一开启时启用 brush
  const brushOn = vpOn || props.selectMode || props.zoomSelectMode
  const brushCfg = brushOn ? {
    brush: {
      xAxisIndex: 0,
      brushStyle: { borderWidth: 1, color: 'rgba(90,140,255,0.08)', borderColor: 'rgba(90,140,255,0.4)' },
      outOfBrush: { colorAlpha: 0.35 },
      throttleType: 'debounce',
      throttleDelay: 200,
    },
  } : {}

  // dataZoom 必须声明在 option 里（slider 才能渲染），但 start/end 不影响
  // 实际位置由 watch(option) + dispatchAction 恢复。
  const allXAxisIndices = Array.from({ length: 2 + N }, (_, i) => i)

  return {
    animation: false,
    legend: {
      data: [
        'K 线', '成交量',
        ...(props.factorRows ?? []).map(r => r.name),
        ...(props.chanlun ? ['缠论笔', '分型', '买卖点'] : []),
      ],
      top: 5,
    },
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
    dataZoom: vpOn
      ? [{ type: 'slider', xAxisIndex: allXAxisIndices, top: '94%' }]
      : [
          { type: 'inside', xAxisIndex: allXAxisIndices },
          { type: 'slider', xAxisIndex: allXAxisIndices, top: '94%' },
        ],
    ...brushCfg,
    series,
  }
})

const chartHeight = computed(() => {
  const N = Math.min(props.factorRows?.length ?? 0, 5)
  return (620 + N * 65) + 'px'
})

// ---- dataZoom dispatchAction 管理 ----
const chartRef = ref<InstanceType<typeof VChart> | null>(null)

function getInst() {
  return chartRef.value as any
}

/** 通过 dispatchAction 设置 dataZoom（slider + inside） */
function applyDataZoom(start: number, end: number) {
  const inst = getInst()
  if (!inst) return
  inst.dispatchAction?.({
    type: 'dataZoom',
    dataZoomIndex: 0,
    start,
    end,
  })
}

/** 用户拖动 slider 时记录位置 */
function onDataZoom(params: any) {
  const batch = params?.batch
  if (!batch || batch.length === 0) return
  const zoom = batch[0]
  if (zoom?.start != null && zoom?.end != null) {
    dataZoomRange.value = { start: zoom.start, end: zoom.end }
  }
}

// ---- Volume Profile / 框选缩放 brush handler ----
function onBrushSelected(params: any) {
  const areas = params?.batch?.[0]?.areas
  if (!areas || areas.length === 0) return
  const range = areas[0]?.coordRange?.[0]
  if (!range || range.length < 2) return
  const startIdx = Math.min(range[0], range[1])
  const endIdx = Math.max(range[0], range[1])

  // 框选缩放模式：把选区转成 dataZoom 区间，然后清掉 brush 标记
  if (props.zoomSelectMode) {
    const total = props.categories.length
    if (total <= 1 || startIdx === endIdx) return
    const newStart = (startIdx / (total - 1)) * 100
    const newEnd = (endIdx / (total - 1)) * 100
    dataZoomRange.value = { start: newStart, end: newEnd }
    applyDataZoom(newStart, newEnd)
    // 清掉框选标记、重新激活 brush 光标以便下次框选
    setTimeout(() => {
      const inst = getInst()
      inst?.dispatchAction?.({ type: 'brush', areas: [] })
      syncBrushCursor()
    }, 50)
    return
  }

  if (!props.showVolumeProfile && !props.selectMode) return
  selectedRange.value = { startIdx, endIdx }
  // VP 开启时才算成交量剖面；框选找相似模式只需要 selectedRange。
  if (props.showVolumeProfile) {
    const indices: number[] = []
    for (let i = startIdx; i <= endIdx; i++) indices.push(i)
    volumeProfile.value = calcVolumeProfile(indices)
    emit('update:vpData', { startIdx, endIdx })
  }
  // option 重算后 vue-echarts 会 setOption，可能重置 dataZoom；等 setOption 完成后再恢复。
  setTimeout(() => {
    applyDataZoom(dataZoomRange.value.start, dataZoomRange.value.end)
  }, 100)
}

// ---- 键盘 ↑/↓ 缩放 ----
const isHovered = ref(false)

function onKeyDown(e: KeyboardEvent) {
  if (!isHovered.value) return
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
  e.preventDefault()
  const { start, end } = dataZoomRange.value
  const span = end - start
  if (span <= 0) return
  const center = (start + end) / 2
  const total = props.categories.length
  const pointPct = total > 1 ? 100 / (total - 1) : 1
  const delta = Math.max(span * 0.1, pointPct)
  let newStart: number, newEnd: number
  if (e.key === 'ArrowUp') {
    const newSpan = span - delta
    if (newSpan < pointPct * 2) return
    newStart = center - newSpan / 2
    newEnd = center + newSpan / 2
  } else {
    if (start <= 0.1 && end >= 99.9) {
      emit('request-expand')
      return
    }
    newStart = center - (span + delta) / 2
    newEnd = center + (span + delta) / 2
  }
  newStart = Math.max(0, newStart)
  newEnd = Math.min(100, newEnd)
  dataZoomRange.value = { start: newStart, end: newEnd }
  applyDataZoom(newStart, newEnd)
}

// ---- 失焦修复 ----
function restoreChart() {
  const inst = getInst()
  if (!inst) return
  try {
    inst.dispatchAction?.({ type: 'hideTip' })
    inst.dispatchAction?.({ type: 'updateAxisPointer', currTrigger: 'leave' })
    inst.resize?.()
  } catch {
    // 如果还没挂载就忽略。
  }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') restoreChart()
}

// ---- 挂载后初始化 dataZoom ----
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  window.addEventListener('focus', restoreChart)
  window.addEventListener('keydown', onKeyDown)
  // 首次挂载后设置 dataZoom；读 dataZoomRange 而非硬编码 0/100，
  // 这样父组件若已通过 setFocusZoom 预设了聚焦区间，首帧就能定位到位。
  nextTick(() => {
    const inst = getInst()
    if (!inst) return
    const { start, end } = dataZoomRange.value
    inst.dispatchAction?.({
      type: 'dataZoom',
      dataZoomIndex: 0,
      start,
      end,
    })
  })
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('focus', restoreChart)
  window.removeEventListener('keydown', onKeyDown)
})

// 颜色切换时 ECharts 会按新 option 重绘，但 tooltip 里残留的旧颜色箭头要顺便清掉。
watch(() => props.colorMode, () => restoreChart())

// VP / 框选找相似 / 框选缩放 任一开启 → 激活 brush 光标；都关闭 → 清光标与选区。
function syncBrushCursor() {
  const inst = getInst()
  if (!inst) return
  const on = props.showVolumeProfile || props.selectMode || props.zoomSelectMode
  setTimeout(() => {
    inst.dispatchAction?.({
      type: 'takeGlobalCursor', key: 'brush',
      brushOption: on ? { brushType: 'rect', brushMode: 'single' } : { brushType: 'rect', brushMode: 'clear' },
    })
  }, 100)
}

watch([() => props.showVolumeProfile, () => props.selectMode, () => props.zoomSelectMode], ([vp, sel]) => {
  if (!vp) volumeProfile.value = null        // VP 关 → 清成交量剖面叠加
  if (!vp && !sel) selectedRange.value = null  // 两个都关 → 连选区一起清
  syncBrushCursor()
})

// option 变化后（VP 添加/移除、数据更新等），恢复 dataZoom 位置
// vue-echarts 通过 nextTick 异步调 setOption → ECharts 重建 dataZoom 重置到 0-100
// 用 100ms setTimeout 等 setOption 完成后再 dispatchAction 恢复。
// 必须在 setTimeout 内读 dataZoomRange——外部 setFocusZoom 可能在这 100ms 内更新它。
watch(option, () => {
  setTimeout(() => {
    const { start, end } = dataZoomRange.value
    applyDataZoom(start, end)
    syncBrushCursor()
  }, 100)
})

/** 外部聚焦：父组件在数据到位后调用，将 dataZoom 定位到指定日期区间。 */
function setFocusZoom(startDate: string, endDate: string): boolean {
  const cats = props.categories
  if (!cats.length) return false
  const lo = cats.findIndex(c => c >= startDate)
  let hi = -1
  for (let i = cats.length - 1; i >= 0; i--) {
    if (cats[i] <= endDate) { hi = i; break }
  }
  if (lo < 0 || hi <= lo) return false
  const n = Math.max(cats.length - 1, 1)
  const s = (lo / n) * 100
  const e = (hi / n) * 100
  dataZoomRange.value = { start: s, end: e }
  applyDataZoom(s, e)
  setTimeout(() => applyDataZoom(s, e), 120)
  return true
}

defineExpose({ setFocusZoom })
</script>

<template>
  <div class="kline-wrap" @mouseenter="isHovered = true" @mouseleave="isHovered = false">
    <button v-if="hasSelection" class="find-similar-btn" @click="emitFindSimilar">
      🔍 找相似
    </button>
    <v-chart ref="chartRef" :option="option" autoresize :style="{ width: '100%', height: chartHeight }" @brushselected="onBrushSelected" @datazoom="onDataZoom" />
  </div>
</template>

<style scoped>
.kline-wrap { position: relative; }
.find-similar-btn {
  /* 放左上角：避开右边缘的 VP 成交量剖面条与顶部居中的图例。 */
  position: absolute; top: 6px; left: 70px; z-index: 10;
  padding: 4px 12px; font-size: 13px; cursor: pointer;
  background: #2080f0; color: #fff; border: none; border-radius: 4px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.2);
}
.find-similar-btn:hover { background: #4098fc; }
</style>
