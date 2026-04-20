<script setup lang="ts">
/**
 * IC 随前瞻期衰减图 + 半衰期展示。
 *
 * 输入复用 payload.ic / payload.rank_ic 的原始结构（{period: {dates, values}}），
 * 在前端就地聚合每周期均值——不走后端的好处是老评估也立刻有图可看，
 * 不用 payload_json schema 扩字段 / 不用强迫重跑。
 *
 * 半衰期语义：从 IC 均值的"实际峰值"开始算（不是默认 T=1）。反转/长周期趋势类
 * 因子 T=1 的 IC 常接近 0，硬当基准会让"半衰期"恒为 null，非常误导。
 * 这里先 argmax 找峰值位置，再往后找首次 ≤ 50% 的位置做线性插值。
 */
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([
  CanvasRenderer, LineChart,
  GridComponent, TooltipComponent, LegendComponent,
  MarkLineComponent, MarkPointComponent,
])

interface PeriodSeries {
  dates: string[]
  values: (number | null)[]
}

const props = defineProps<{
  ic?: Record<string, PeriodSeries> | null
  rankIc?: Record<string, PeriodSeries> | null
}>()

function meanOf(values: (number | null)[] | undefined): number | null {
  if (!values || values.length === 0) return null
  let sum = 0
  let n = 0
  for (const v of values) {
    if (v == null || !Number.isFinite(v)) continue
    sum += v
    n += 1
  }
  return n === 0 ? null : sum / n
}

// 把 {period(str): series} 展平成 [(period_num, mean_ic)]，按 period 升序。
function aggregate(
  dict: Record<string, PeriodSeries> | null | undefined,
): { period: number; mean: number }[] {
  if (!dict) return []
  const rows: { period: number; mean: number }[] = []
  for (const key of Object.keys(dict)) {
    const p = Number(key)
    if (!Number.isFinite(p)) continue
    const m = meanOf(dict[key]?.values)
    if (m == null) continue
    rows.push({ period: p, mean: m })
  }
  rows.sort((a, b) => a.period - b.period)
  return rows
}

// 找峰值：argmax(mean) 中仅考虑 mean > 0 的点。全为非正 → null（因子方向反/纯噪声）。
function findPeak(
  rows: { period: number; mean: number }[],
): { period: number; mean: number; index: number } | null {
  let best: { period: number; mean: number; index: number } | null = null
  for (let i = 0; i < rows.length; i++) {
    const m = rows[i].mean
    if (!(m > 0)) continue
    if (!best || m > best.mean) best = { period: rows[i].period, mean: m, index: i }
  }
  return best
}

/**
 * 从峰值后续点位往后找半衰期：首次 mean ≤ 0.5 * peak 的前瞻期，与峰值位置的距离。
 * 返回：
 *   - 数字：峰值到"跌到 50%"的前瞻期间隔（日）
 *   - 'noDecayInRange'：采样期内未跌到一半（信号抗衰减 / 采样窗太短）
 *   - 'peakAtLast'：峰值就在最后一个采样点，没有后续可看
 *   - null：没有有效峰值（mean 全 ≤ 0）
 */
type HalfLifeResult = number | 'noDecayInRange' | 'peakAtLast' | null

function halfLifeFromPeak(rows: { period: number; mean: number }[]): HalfLifeResult {
  const peak = findPeak(rows)
  if (!peak) return null
  if (peak.index === rows.length - 1) return 'peakAtLast'
  const target = peak.mean / 2
  for (let i = peak.index + 1; i < rows.length; i++) {
    if (rows[i].mean <= target) {
      const a = rows[i - 1]
      const b = rows[i]
      if (a.mean === b.mean) return b.period - peak.period
      const t = (a.mean - target) / (a.mean - b.mean)
      const crossPeriod = a.period + t * (b.period - a.period)
      return crossPeriod - peak.period
    }
  }
  return 'noDecayInRange'
}

const icRows = computed(() => aggregate(props.ic))
const rankIcRows = computed(() => aggregate(props.rankIc))
const icPeak = computed(() => findPeak(icRows.value))
const rankIcPeak = computed(() => findPeak(rankIcRows.value))
const icHalfLife = computed<HalfLifeResult>(() => halfLifeFromPeak(icRows.value))
const rankIcHalfLife = computed<HalfLifeResult>(() => halfLifeFromPeak(rankIcRows.value))

// 合并两组 period 做 X 轴（union 并排序），对齐后两条线同一 X 轴渲染。
const allPeriods = computed(() => {
  const s = new Set<number>()
  for (const r of icRows.value) s.add(r.period)
  for (const r of rankIcRows.value) s.add(r.period)
  return Array.from(s).sort((a, b) => a - b)
})

function alignTo(rows: { period: number; mean: number }[], periods: number[]): (number | null)[] {
  const map = new Map(rows.map((r) => [r.period, r.mean]))
  return periods.map((p) => (map.has(p) ? map.get(p)! : null))
}

const empty = computed(() => allPeriods.value.length < 2)

// 两条线各自的 50% 阈值线参考（选较大的那条做阈值线，不重复画两条）。
const peakForLine = computed(() => {
  const candidates = [icPeak.value?.mean, rankIcPeak.value?.mean]
    .filter((v): v is number => v != null && v > 0)
  return candidates.length ? Math.max(...candidates) : null
})

const option = computed(() => {
  const periods = allPeriods.value
  const icSeries = alignTo(icRows.value, periods)
  const rankSeries = alignTo(rankIcRows.value, periods)

  const markLines: any[] = [
    {
      yAxis: 0,
      lineStyle: { color: '#C0C4CC', type: 'dashed', width: 1 },
      label: { show: false },
      symbol: 'none',
    },
  ]
  if (peakForLine.value != null) {
    markLines.push({
      yAxis: peakForLine.value / 2,
      lineStyle: { color: '#F0B90B', type: 'dashed', width: 1 },
      label: {
        show: true,
        position: 'insideEndTop',
        formatter: '50% 峰值',
        color: '#F0B90B',
        fontSize: 11,
      },
      symbol: 'none',
    })
  }

  // 峰值点用 markPoint 标注（仅 IC 序列，避免两个五角星挤一起）。
  const markPoints: any[] = []
  if (icPeak.value) {
    markPoints.push({
      name: '峰值',
      coord: [String(icPeak.value.period), icPeak.value.mean],
      symbol: 'pin',
      symbolSize: 36,
      itemStyle: { color: '#F0B90B' },
      label: {
        formatter: `峰 T=${icPeak.value.period}`,
        color: '#1E2026',
        fontSize: 10,
        fontWeight: 600,
      },
    })
  }

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (ps: any[]) => {
        const head = `前瞻期 ${ps[0].axisValue} 日<br/>`
        const body = ps
          .map((p) => `${p.marker} ${p.seriesName}: ${p.value == null ? '-' : p.value.toFixed(4)}`)
          .join('<br/>')
        return head + body
      },
    },
    legend: { data: ['IC 均值', 'Rank IC 均值'], top: 8 },
    grid: { left: 50, right: 30, bottom: 40, top: 48 },
    xAxis: {
      type: 'category',
      name: '前瞻期（日）',
      nameLocation: 'middle',
      nameGap: 28,
      data: periods.map(String),
    },
    yAxis: { type: 'value', name: '均值' },
    series: [
      {
        name: 'IC 均值',
        type: 'line',
        data: icSeries,
        lineStyle: { color: '#F0B90B', width: 2 },
        itemStyle: { color: '#F0B90B' },
        symbol: 'circle',
        symbolSize: 8,
        markLine: { silent: true, data: markLines },
        markPoint: markPoints.length ? { silent: true, data: markPoints } : undefined,
      },
      {
        name: 'Rank IC 均值',
        type: 'line',
        data: rankSeries,
        lineStyle: { color: '#1E2026', width: 2 },
        itemStyle: { color: '#1E2026' },
        symbol: 'triangle',
        symbolSize: 8,
      },
    ],
  }
})

function fmtHalf(v: HalfLifeResult): string {
  if (v === null) return '—（峰值 ≤ 0，信号方向反/纯噪声）'
  if (v === 'noDecayInRange') return '> 采样上限（信号很抗衰减）'
  if (v === 'peakAtLast') return '峰值在最后一个采样点，无法估计'
  return `${v.toFixed(1)} 日`
}

function peakLabel(p: { period: number; mean: number } | null): string {
  if (!p) return '—'
  return `T=${p.period}（IC=${p.mean.toFixed(4)}）`
}
</script>

<template>
  <div>
    <div style="display: flex; flex-wrap: wrap; gap: 20px 28px; align-items: baseline; margin-bottom: 8px; font-size: 12px; color: #606266">
      <div>IC 峰值：<b style="color: #F0B90B">{{ peakLabel(icPeak) }}</b></div>
      <div>IC 半衰期（自峰值起）：<b style="color: #F0B90B">{{ fmtHalf(icHalfLife) }}</b></div>
      <div>Rank IC 峰值：<b style="color: #1E2026">{{ peakLabel(rankIcPeak) }}</b></div>
      <div>Rank IC 半衰期（自峰值起）：<b style="color: #1E2026">{{ fmtHalf(rankIcHalfLife) }}</b></div>
    </div>
    <div style="font-size: 12px; color: #848E9C; margin-bottom: 4px">
      半衰期语义：从 IC 均值实际峰值那一期开始算、跌到 50% 所需的前瞻期差值。
      理想 ≥ 5 日（信号可以容忍低频调仓）；&lt; 2 日通常意味着极短期噪声。
      峰值不在 T=1 时说明因子真正有效的预测期不是第二天，这时只看"每日 IC"会严重低估因子。
    </div>
    <div v-if="empty" style="padding: 32px; text-align: center; color: #848E9C">
      本次评估只配置了 &lt; 2 个 forward_period，无法绘制衰减曲线。<br />
      建议创建新评估时把 forward_periods 写成多个（如 <code>1, 5, 10</code>）。
    </div>
    <v-chart v-else :option="option" autoresize style="width: 100%; height: 280px" />
  </div>
</template>
