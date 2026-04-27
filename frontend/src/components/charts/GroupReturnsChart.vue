<script setup lang="ts">
/**
 * 分组净值 / 收益折线图。
 *
 * 默认 ``cumulative=true``：把后端 ``payload.group_returns`` 里"每日各组算术
 * 平均收益"在前端做 ``cumprod(1+r)``，得到从 1 起步的累积净值曲线（直观可读，
 * 与"分组累计净值"标题语义一致）。
 *
 * 当数据本身已经是累积值时（如 Alphalens extras 的
 * ``group_cumulative_returns`` 已在后端做过 ``(1+r).cumprod()``），传
 * ``cumulative={false}`` 跳过二次累积。
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

const props = withDefaults(
  defineProps<{
    data: { dates: string[]; [key: string]: any }
    cumulative?: boolean
  }>(),
  { cumulative: true },
)

// 颜色梯度：g1(底/红) → g5(顶/绿)
const groupColors = ['#F6465D', '#E8853D', '#F0B90B', '#6CB85C', '#0ECB81']

/** 把 daily-return 序列变成从 1 起步的累积净值。NaN 视为 0（停牌日不影响净值）。 */
function toCumNetValue(daily: (number | null)[]): number[] {
  const out: number[] = []
  let v = 1
  for (const r of daily) {
    const x = r == null || Number.isNaN(r) ? 0 : r
    v = v * (1 + x)
    out.push(v)
  }
  return out
}

const option = computed(() => {
  // 提取 g1, g2, ... gN 键
  const groupKeys = Object.keys(props.data)
    .filter(k => k.startsWith('g'))
    .sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)))

  const seriesList = groupKeys.map((key, idx) => {
    const raw = props.data[key] as (number | null)[]
    return {
      name: `第${idx + 1}组`,
      type: 'line' as const,
      data: props.cumulative ? toCumNetValue(raw) : (raw as number[]),
      lineStyle: { color: groupColors[idx % groupColors.length], width: 2 },
      itemStyle: { color: groupColors[idx % groupColors.length] },
      symbol: 'none',
      smooth: true,
    }
  })

  return {
    tooltip: { trigger: 'axis' as const },
    legend: { data: seriesList.map(s => s.name), top: 5 },
    grid: { left: 50, right: 20, bottom: 60, top: 40 },
    xAxis: { type: 'category' as const, data: props.data.dates, axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: 'value' as const, scale: true },
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
