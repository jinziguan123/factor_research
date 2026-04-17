<script setup lang="ts">
/**
 * 因子值分布直方图
 * bins 比 counts 多 1（是边界），取每对相邻 bin 的中点做 X 轴
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

const props = defineProps<{
  data: { bins: number[]; counts: number[] }
}>()

// 取每对相邻 bin 边界的中点作为 X 轴标签
const xLabels = computed(() => {
  const labels: string[] = []
  for (let i = 0; i < props.data.bins.length - 1; i++) {
    const mid = (props.data.bins[i] + props.data.bins[i + 1]) / 2
    labels.push(mid.toFixed(3))
  }
  return labels
})

const option = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 60, right: 20, bottom: 40, top: 20 },
  xAxis: {
    type: 'category',
    data: xLabels.value,
    axisLabel: { rotate: 30, fontSize: 10 },
  },
  yAxis: { type: 'value', name: '频次' },
  series: [
    {
      name: '频次',
      type: 'bar',
      data: props.data.counts,
      itemStyle: { color: '#F0B90B' },
      barMaxWidth: 30,
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
