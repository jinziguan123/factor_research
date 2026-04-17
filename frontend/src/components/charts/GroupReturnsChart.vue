<script setup lang="ts">
/**
 * 分组累计收益折线图
 * 多条折线（各组），颜色从红到绿
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
  data: { dates: string[]; [key: string]: any }
}>()

// 颜色梯度：g1(底/红) → g5(顶/绿)
const groupColors = ['#F6465D', '#E8853D', '#F0B90B', '#6CB85C', '#0ECB81']

const option = computed(() => {
  // 提取 g1, g2, ... gN 键
  const groupKeys = Object.keys(props.data)
    .filter(k => k.startsWith('g'))
    .sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)))

  const seriesList = groupKeys.map((key, idx) => ({
    name: `第${idx + 1}组`,
    type: 'line' as const,
    data: (props.data[key] as number[]),
    lineStyle: { color: groupColors[idx % groupColors.length], width: 2 },
    itemStyle: { color: groupColors[idx % groupColors.length] },
    symbol: 'none',
    smooth: true,
  }))

  return {
    tooltip: { trigger: 'axis' as const },
    legend: { data: seriesList.map(s => s.name), top: 5 },
    grid: { left: 50, right: 20, bottom: 60, top: 40 },
    xAxis: { type: 'category' as const, data: props.data.dates, axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: 'value' as const },
    dataZoom: [
      { type: 'slider', start: 0, end: 100, bottom: 5 },
      { type: 'inside' },
    ],
    series: seriesList,
  }
})
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
