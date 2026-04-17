<script setup lang="ts">
/**
 * IC 序列图表
 * 柱状图（每日 IC）+ 折线（累计 IC），含 dataZoom
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent])

const props = defineProps<{
  series: { dates: string[]; values: (number | null)[] }
  title?: string
}>()

// 计算累计 IC
const cumValues = computed(() => {
  let cum = 0
  return props.series.values.map(v => {
    if (v == null) return null
    cum += v
    return cum
  })
})

const option = computed(() => ({
  title: props.title ? { text: props.title, textStyle: { fontSize: 14, color: '#1E2026' } } : undefined,
  tooltip: { trigger: 'axis' },
  legend: { data: ['每日IC', '累计IC'], top: 30 },
  grid: { left: 50, right: 50, bottom: 60, top: 60 },
  xAxis: { type: 'category', data: props.series.dates, axisLabel: { rotate: 30, fontSize: 10 } },
  yAxis: { type: 'value' },
  dataZoom: [
    { type: 'slider', start: 0, end: 100, bottom: 5 },
    { type: 'inside' },
  ],
  series: [
    {
      name: '每日IC',
      type: 'bar',
      data: props.series.values,
      itemStyle: { color: '#F0B90B' },
      barMaxWidth: 6,
    },
    {
      name: '累计IC',
      type: 'line',
      data: cumValues.value,
      lineStyle: { color: '#1E2026', width: 2 },
      itemStyle: { color: '#1E2026' },
      symbol: 'none',
      smooth: true,
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
