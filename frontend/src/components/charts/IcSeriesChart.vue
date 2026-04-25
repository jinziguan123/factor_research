<script setup lang="ts">
/**
 * IC 序列图表
 * 柱状图（每日 IC）+ 折线（累计 IC）+ N 日移动平均，含 dataZoom
 *
 * 为什么 MA 放在前端算：MA 是"已有序列的滚动平均"，前后端算法完全等价；前端计
 * 算可让用户未来轻松切换窗口而不需改 payload 结构。默认 [5, 20] 两档覆盖"近端
 * 波动"和"中期趋势"两个常用解读视角。
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

const props = withDefaults(
  defineProps<{
    series: { dates: string[]; values: (number | null)[] }
    title?: string
    /** N 日移动平均窗口；传 [] 可隐藏 MA 折线。默认 [5, 20]。 */
    maWindows?: number[]
  }>(),
  { maWindows: () => [5, 20] },
)

// 计算累计 IC
const cumValues = computed(() => {
  let cum = 0
  return props.series.values.map(v => {
    if (v == null) return null
    cum += v
    return cum
  })
})

// 计算 N 日移动平均（skip NaN；窗口内有效样本 < 一半时给 null，避免早期抖动）。
function rollingMean(values: (number | null)[], window: number): (number | null)[] {
  const out: (number | null)[] = []
  const minValid = Math.ceil(window / 2)
  for (let i = 0; i < values.length; i++) {
    const lo = Math.max(0, i - window + 1)
    let sum = 0
    let n = 0
    for (let j = lo; j <= i; j++) {
      const v = values[j]
      if (v != null) {
        sum += v
        n += 1
      }
    }
    out.push(n >= minValid ? sum / n : null)
  }
  return out
}

// MA 折线的颜色轮盘：蓝 / 红 / 紫，后续如需更多窗口会循环复用。
const MA_COLORS = ['#2979FF', '#D12020', '#7B1FA2']

const maSeries = computed(() =>
  props.maWindows.map((w, i) => ({
    name: `${w}日MA`,
    type: 'line' as const,
    data: rollingMean(props.series.values, w),
    lineStyle: { color: MA_COLORS[i % MA_COLORS.length], width: 1.5, type: 'dashed' as const },
    itemStyle: { color: MA_COLORS[i % MA_COLORS.length] },
    symbol: 'none' as const,
    smooth: false,
  })),
)

const legendData = computed(() => [
  '每日IC',
  '累计IC',
  ...props.maWindows.map(w => `${w}日MA`),
])

const option = computed(() => ({
  title: props.title ? { text: props.title, textStyle: { fontSize: 14, color: '#1E2026' } } : undefined,
  tooltip: { trigger: 'axis' },
  legend: { data: legendData.value, top: 30, itemGap: 8 },
  // grid.top 从 60 → 140：评估详情页 3 列 grid 下卡片 ~260px 宽，4 个 legend 项
  // （每日IC / 累计IC / 5日MA / 20日MA）会被压成 4 行（每行约 22px），需要额外
  // ~80px 空间才不会和 yAxis 刻度重叠。
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
    ...maSeries.value,
  ],
}))
</script>

<template>
  <v-chart :option="option" autoresize style="width: 100%; height: 320px" />
</template>
