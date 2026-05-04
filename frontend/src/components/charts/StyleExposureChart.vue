<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent])

const props = defineProps<{
  attribution: {
    exposures: Record<string, (number | null)[]>
    r_squared: (number | null)[]
    dates: string[]
  }
}>()

const COLORS = ['#5dade2', '#e67e22', '#27ae60', '#9b59b6', '#f1c40f']

const styleNames = computed(() => Object.keys(props.attribution.exposures))

function safeMean(arr: (number | null)[]): number {
  const valid = arr.filter((v): v is number => v != null && Number.isFinite(v))
  if (valid.length === 0) return 0
  return valid.reduce((a, b) => a + b, 0) / valid.length
}

const barOption = computed(() => ({
  animation: false,
  tooltip: { trigger: 'axis' as const },
  grid: { left: 50, right: 20, top: 20, bottom: 30 },
  xAxis: {
    type: 'category' as const,
    data: styleNames.value,
    axisLabel: { fontSize: 10 },
  },
  yAxis: {
    type: 'value' as const,
    axisLabel: { formatter: (v: number) => v.toFixed(3), fontSize: 10 },
    splitLine: { show: false },
  },
  series: [{
    type: 'bar' as const,
    data: styleNames.value.map((name, i) => ({
      value: safeMean(props.attribution.exposures[name]),
      itemStyle: { color: COLORS[i % COLORS.length] },
    })),
  }],
}))

const lineOption = computed(() => ({
  animation: false,
  tooltip: { trigger: 'axis' as const },
  legend: {
    data: styleNames.value,
    bottom: 0,
    textStyle: { fontSize: 10 },
  },
  grid: { left: 50, right: 20, top: 20, bottom: 40 },
  xAxis: {
    type: 'category' as const,
    data: props.attribution.dates,
    axisLabel: { show: false },
  },
  yAxis: {
    type: 'value' as const,
    axisLabel: { formatter: (v: number) => v.toFixed(3), fontSize: 10 },
    splitLine: { show: false },
  },
  series: styleNames.value.map((name, i) => ({
    name,
    type: 'line' as const,
    data: props.attribution.exposures[name],
    symbol: 'none' as const,
    lineStyle: { color: COLORS[i % COLORS.length], width: 1.5 },
  })),
}))
</script>

<template>
  <div style="display: flex; gap: 16px">
    <v-chart :option="barOption" autoresize style="flex: 1; height: 200px" />
    <v-chart :option="lineOption" autoresize style="flex: 2; height: 200px" />
  </div>
</template>
