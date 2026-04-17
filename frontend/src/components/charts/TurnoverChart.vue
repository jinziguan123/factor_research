<script setup lang="ts">
/**
 * 换手率折线图
 * 单折线，颜色 #848E9C
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
  yAxis: { type: 'value' },
  dataZoom: [
    { type: 'slider', start: 0, end: 100, bottom: 5 },
    { type: 'inside' },
  ],
  series: [
    {
      name: '换手率',
      type: 'line',
      data: props.series.values,
      lineStyle: { color: '#848E9C', width: 2 },
      itemStyle: { color: '#848E9C' },
      symbol: 'none',
      smooth: true,
      areaStyle: { color: 'rgba(132,142,156,0.08)' },
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
