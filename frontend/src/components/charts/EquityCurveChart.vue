<script setup lang="ts">
/**
 * 净值曲线 + 回撤面积图
 * 净值色 #F0B90B，回撤区域色 #F6465D opacity 0.15
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent])

const props = defineProps<{
  equity: { dates: string[]; values: (number | null)[] }
}>()

// 计算回撤序列
const drawdownValues = computed(() => {
  let peak = -Infinity
  return props.equity.values.map(v => {
    if (v == null) return null
    if (v > peak) peak = v
    return peak > 0 ? (v - peak) / peak : 0
  })
})

const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['净值', '回撤'], top: 5 },
  grid: { left: 60, right: 60, bottom: 60, top: 40 },
  xAxis: { type: 'category', data: props.equity.dates, axisLabel: { rotate: 30, fontSize: 10 } },
  yAxis: [
    { type: 'value', name: '净值', position: 'left' },
    { type: 'value', name: '回撤', position: 'right', inverse: true, axisLabel: { formatter: '{value}' } },
  ],
  dataZoom: [
    { type: 'slider', start: 0, end: 100, bottom: 5 },
    { type: 'inside' },
  ],
  series: [
    {
      name: '净值',
      type: 'line',
      yAxisIndex: 0,
      data: props.equity.values,
      lineStyle: { color: '#F0B90B', width: 2 },
      itemStyle: { color: '#F0B90B' },
      symbol: 'none',
      smooth: true,
    },
    {
      name: '回撤',
      type: 'line',
      yAxisIndex: 1,
      data: drawdownValues.value,
      lineStyle: { color: 'transparent' },
      itemStyle: { color: 'transparent' },
      symbol: 'none',
      areaStyle: { color: 'rgba(246,70,93,0.15)' },
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
