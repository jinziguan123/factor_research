<script setup lang="ts">
/**
 * 因子排名自相关折线图
 * 单折线，颜色 #7B1FA2（紫色，区分换手率的灰色）
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, DataZoomComponent])

const props = defineProps<{
  series: { dates: string[]; values: (number | null)[] }
}>()

const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 50, right: 20, bottom: 60, top: 20 },
  xAxis: { type: 'category', data: props.series.dates, axisLabel: { rotate: 30, fontSize: 10 } },
  yAxis: { type: 'value', min: -1, max: 1 },
  dataZoom: [
    { type: 'slider', start: 0, end: 100, bottom: 5 },
    { type: 'inside' },
  ],
  series: [
    {
      name: 'Rank 自相关',
      type: 'line',
      data: props.series.values,
      lineStyle: { color: '#7B1FA2', width: 2 },
      itemStyle: { color: '#7B1FA2' },
      symbol: 'none',
      smooth: true,
      areaStyle: { color: 'rgba(123,31,162,0.08)' },
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
