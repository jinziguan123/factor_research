<script setup lang="ts">
/**
 * K 线图（candlestick + 成交量双图联动）。
 *
 * 轴/数据约定：
 * - x 轴：调用方传入字符串数组（日线 "YYYY-MM-DD"；分钟线 "YYYY-MM-DD HH:MM"）；
 *   直接作为 ECharts category，避免时区 + 非交易时段空白的麻烦。
 * - candlestick data 顺序：[open, close, low, high]（ECharts 约定，与 OHLC 不同）。
 * - 成交量副图用柱状图，颜色跟随涨跌：close>=open 绿，否则红（A 股配色：红涨绿跌，
 *   但本项目 brand 色已经把"红"留给风险 / 回撤，这里用 Binance 风格保持和其它图一致）。
 *
 * 联动：两个 grid 共享 xAxisIndex，dataZoom 同步。
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { CandlestickChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([
  CanvasRenderer,
  CandlestickChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
])

const props = defineProps<{
  /** 时间轴类目（字符串），长度 N。 */
  categories: string[]
  /** OHLC 序列，每条 [open, high, low, close]；调用方统一这个顺序，组件内再重排。 */
  ohlc: [number, number, number, number][]
  /** 成交量序列，长度 N（与 categories 对齐）。 */
  volumes: number[]
}>()

// 上涨绿 / 下跌红（Binance 风格）；与风险色 (#F6465D 红) 保持一致。
const UP_COLOR = '#0ECB81'
const DOWN_COLOR = '#F6465D'

// ECharts 要的是 [open, close, low, high]；重排。
const candleData = computed(() =>
  props.ohlc.map(([o, h, l, c]) => [o, c, l, h]),
)

// 柱子按涨跌上色；open > close 才红。
const volumeData = computed(() =>
  props.volumes.map((v, i) => {
    const [o, , , c] = props.ohlc[i] ?? [0, 0, 0, 0]
    return {
      value: v,
      itemStyle: { color: c >= o ? UP_COLOR : DOWN_COLOR },
    }
  }),
)

const option = computed(() => ({
  animation: false,
  legend: { data: ['K 线', '成交量'], top: 5 },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'cross' },
    backgroundColor: 'rgba(30,32,38,0.95)',
    borderWidth: 0,
    textStyle: { color: '#fff', fontSize: 12 },
  },
  axisPointer: { link: [{ xAxisIndex: 'all' }] },
  // 两个 grid 上下排列：K 线占 70%，成交量占 20%，中间留空给 dataZoom。
  grid: [
    { left: 60, right: 60, top: 40, height: '60%' },
    { left: 60, right: 60, top: '75%', height: '15%' },
  ],
  xAxis: [
    {
      type: 'category',
      data: props.categories,
      boundaryGap: true,
      axisLine: { onZero: false },
      axisLabel: { show: false },
      splitLine: { show: false },
    },
    {
      type: 'category',
      gridIndex: 1,
      data: props.categories,
      boundaryGap: true,
      axisLabel: { rotate: 30, fontSize: 10 },
    },
  ],
  yAxis: [
    { scale: true, splitArea: { show: true } },
    {
      gridIndex: 1,
      scale: true,
      splitNumber: 2,
      axisLabel: { fontSize: 10 },
      axisLine: { show: false },
      splitLine: { show: false },
    },
  ],
  dataZoom: [
    { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    { type: 'slider', xAxisIndex: [0, 1], top: '92%', start: 0, end: 100 },
  ],
  series: [
    {
      name: 'K 线',
      type: 'candlestick',
      data: candleData.value,
      itemStyle: {
        color: UP_COLOR,
        color0: DOWN_COLOR,
        borderColor: UP_COLOR,
        borderColor0: DOWN_COLOR,
      },
    },
    {
      name: '成交量',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: volumeData.value,
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 560px" />
</template>
