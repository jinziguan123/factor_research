<script setup lang="ts">
/**
 * 因子相关性热力图
 * 轴：因子 id；单元格颜色表示相关系数；配色对称（-1 红 → 0 灰 → +1 黄）。
 * 小矩阵（≤8×8）显示每格数值，>8 只用颜色，避免文字叠到看不清。
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { HeatmapChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent])

const props = defineProps<{
  factorIds: string[]
  values: (number | null)[][]
}>()

// ECharts heatmap data: [[x_idx, y_idx, value], ...]；对齐到 label 数组的索引。
const data = computed(() => {
  const rows: [number, number, number | null][] = []
  for (let i = 0; i < props.values.length; i++) {
    const row = props.values[i]
    for (let j = 0; j < row.length; j++) {
      rows.push([j, i, row[j]])
    }
  }
  return rows
})

const showLabel = computed(() => props.factorIds.length <= 8)

const option = computed(() => ({
  tooltip: {
    position: 'top',
    formatter: (p: any) => {
      const x = props.factorIds[p.data[0]] ?? ''
      const y = props.factorIds[p.data[1]] ?? ''
      const v = p.data[2]
      return `${x} × ${y}<br/>相关系数：${v == null ? '-' : v.toFixed(3)}`
    },
  },
  grid: { left: 120, right: 60, top: 30, bottom: 80 },
  xAxis: {
    type: 'category',
    data: props.factorIds,
    axisLabel: { rotate: 30, fontSize: 11 },
    splitArea: { show: true },
  },
  yAxis: {
    type: 'category',
    data: props.factorIds,
    axisLabel: { fontSize: 11 },
    splitArea: { show: true },
  },
  visualMap: {
    min: -1,
    max: 1,
    calculable: true,
    orient: 'horizontal',
    left: 'center',
    bottom: 10,
    // 对称色带：-1 红，0 白灰，+1 品牌黄。
    inRange: { color: ['#F6465D', '#FFFFFF', '#F0B90B'] },
  },
  series: [
    {
      name: '相关系数',
      type: 'heatmap',
      data: data.value,
      label: {
        show: showLabel.value,
        formatter: (p: any) => (p.data[2] == null ? '' : p.data[2].toFixed(2)),
        fontSize: 11,
      },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.3)' } },
    },
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 360px" />
</template>
