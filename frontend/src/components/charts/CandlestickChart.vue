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
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
} from 'echarts/components'
import VChart from 'vue-echarts'

use([
  CanvasRenderer,
  CandlestickChart,
  BarChart,
  LineChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
  BrushComponent,
  ToolboxComponent,
])

type ColorMode = 'a-share' | 'binance'

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
  }>(),
  { colorMode: 'a-share', factorRows: () => [], showVolumeProfile: false },
)

const emit = defineEmits<{
  (e: 'update:vpData', data: { startIdx: number; endIdx: number } | null): void
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
  0: { klineH: 55, volTop: 62, volH: 15, factorTop: 0, factorH: 0 },
  1: { klineH: 46, volTop: 54, volH: 13, factorTop: 70, factorH: 18 },
  2: { klineH: 40, volTop: 48, volH: 11, factorTop: 62, factorH: 13 },
  3: { klineH: 36, volTop: 44, volH: 10, factorTop: 57, factorH: 10 },
  4: { klineH: 33, volTop: 41, volH: 9,  factorTop: 53, factorH: 9  },
  5: { klineH: 30, volTop: 38, volH: 8,  factorTop: 49, factorH: 8  },
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
    width: vol / maxVol,  // 归一化到 0-1，由 VP grid 自行缩放
    color: bucketColors[i],
  }))
  return { bars, bucketSize, priceMin: lo, priceMax: hi }
}

// tooltip.formatter：中文标签 + 两位小数。
// ECharts 传入的 params 是数组（trigger: 'axis'），一个元素对应一个 series。
function tooltipFormatter(params: any[]): string {
  if (!Array.isArray(params) || params.length === 0) return ''
  const axisValue = params[0].axisValue ?? ''
  const lines: string[] = [`<div style="margin-bottom:4px;font-weight:600">${axisValue}</div>`]
  for (const p of params) {
    if (p.seriesType === 'candlestick') {
      // p.data = [index, open, close, low, high]；调用 ECharts 拿到的 seriesIndex 下的数据
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
  const allXAxisIndices = Array.from({ length: 2 + N }, (_, i) => i)
  const series: any[] = [
    {
      name: 'K 线', type: 'candlestick', data: candleData.value,
      itemStyle: {
        color: colors.value.up, color0: colors.value.down,
        borderColor: colors.value.up, borderColor0: colors.value.down,
      },
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
        // 用 K 线 y 轴的网格边界来定位
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

  // Only include brush when VP toggle is on
  const brushCfg = vpOn ? {
    brush: {
      xAxisIndex: 0,
      brushStyle: { borderWidth: 1, color: 'rgba(90,140,255,0.08)', borderColor: 'rgba(90,140,255,0.4)' },
      outOfBrush: { colorAlpha: 0.35 },
      throttleType: 'debounce',
      throttleDelay: 200,
    },
  } : {}

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
    dataZoom: vpOn
      ? [{ type: 'slider', xAxisIndex: allXAxisIndices, top: '94%', start: 0, end: 100 }]
      : [
          { type: 'inside', xAxisIndex: allXAxisIndices, start: 0, end: 100 },
          { type: 'slider', xAxisIndex: allXAxisIndices, top: '94%', start: 0, end: 100 },
        ],
    ...brushCfg,
    series,
  }
})

const chartHeight = computed(() => {
  const N = Math.min(props.factorRows?.length ?? 0, 5)
  return (500 + N * 65) + 'px'
})

// ---- Volume Profile brush handler ----
function onBrushSelected(params: any) {
  if (!props.showVolumeProfile) return
  const areas = params?.batch?.[0]?.areas
  if (!areas || areas.length === 0) {
    volumeProfile.value = null
    selectedRange.value = null
    emit('update:vpData', null)
    return
  }
  const range = areas[0]?.coordRange?.[0]
  if (!range || range.length < 2) {
    volumeProfile.value = null
    selectedRange.value = null
    emit('update:vpData', null)
    return
  }
  const startIdx = Math.min(range[0], range[1])
  const endIdx = Math.max(range[0], range[1])
  const indices: number[] = []
  for (let i = startIdx; i <= endIdx; i++) indices.push(i)
  volumeProfile.value = calcVolumeProfile(indices)
  selectedRange.value = { startIdx, endIdx }
  emit('update:vpData', { startIdx, endIdx })
}

// ---- 失焦修复 ----
// 问题：鼠标悬停在 K 线图上时 alt+tab 切走、再切回来，canvas 会卡住不响应鼠标。
// 根因：浏览器 tab 不可见时 ECharts 的 axisPointer/tooltip 状态没有被清，
// 重新 visible 后 pointer 仍以"离开前的坐标"驱动，事件流像断了一样。
// 修复：visibilitychange 变 visible 时主动 hideTip + resize 重建渲染状态。
const chartRef = ref<InstanceType<typeof VChart> | null>(null)

function restoreChart() {
  const inst = chartRef.value as any
  if (!inst) return
  try {
    inst.dispatchAction?.({ type: 'hideTip' })
    inst.dispatchAction?.({ type: 'updateAxisPointer', currTrigger: 'leave' })
    // resize 触发一次全量重绘，修复 canvas 丢事件 / 尺寸漂移。
    inst.resize?.()
  } catch {
    // 如果还没挂载就忽略。
  }
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') restoreChart()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  window.addEventListener('focus', restoreChart)
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('focus', restoreChart)
})

// 颜色切换时 ECharts 会按新 option 重绘，但 tooltip 里残留的旧颜色箭头要顺便清掉。
watch(() => props.colorMode, () => restoreChart())

// VP 关闭时清除选区和数据
watch(() => props.showVolumeProfile, (on) => {
  if (!on) {
    volumeProfile.value = null
    selectedRange.value = null
  }
  // brush 需要通过 dispatchAction 显式激活
  const inst = chartRef.value as any
  if (!inst) return
  if (on) {
    setTimeout(() => {
      inst.dispatchAction?.({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'rect', brushMode: 'single' } })
    }, 100)
  } else {
    inst.dispatchAction?.({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'rect', brushMode: 'clear' } })
  }
})
</script>

<template>
  <v-chart ref="chartRef" :option="option" autoresize :style="{ width: '100%', height: chartHeight }" @brushselected="onBrushSelected" />
</template>
