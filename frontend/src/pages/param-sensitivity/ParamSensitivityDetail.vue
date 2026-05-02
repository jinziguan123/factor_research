<script setup lang="ts">
/**
 * 参数敏感性扫描详情页。
 * - 顶部：run 基础信息 + 状态 + 进度条；
 * - 成功后：邻域稳定性卡片 + 三线叠加图（IC / Rank IC / 多空 Sharpe）+ 每点指标表格。
 *
 * 图表 / 稳定性 / 表格逻辑全部从原 Preview 页搬过来（异步化只改了数据入口）。
 */
import { computed, h, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NAlert, NDataTable, NEmpty, NSpace, NTag,
  NButton, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent,
  MarkLineComponent, MarkPointComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { useParamSensitivity } from '@/api/param_sensitivity'
import type { ParamSensitivityPoint } from '@/api/param_sensitivity'
import { usePoolNameMap } from '@/api/pools'
import { client } from '@/api/client'
import StatusBadge from '@/components/layout/StatusBadge.vue'

use([
  CanvasRenderer, LineChart,
  GridComponent, TooltipComponent, LegendComponent,
  MarkLineComponent, MarkPointComponent,
])

const route = useRoute()
const router = useRouter()
const message = useMessage()

const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useParamSensitivity(runId)

const { lookup: lookupPoolName } = usePoolNameMap()

const applyingBest = ref(false)
async function applyBestParams() {
  if (!runId.value) return
  applyingBest.value = true
  try {
    const res = await client.post(`/param-sensitivity/${runId.value}/apply-best`)
    const data = res.data
    message.success(`已更新 ${data.factor_id} 默认参数为 ${JSON.stringify(data.new_default_params)}`)
  } catch (e: any) {
    message.error(e?.response?.data?.message || e?.response?.data?.detail || e?.message || '应用失败')
  } finally {
    applyingBest.value = false
  }
}

const isRunning = computed(
  () => run.value?.status === 'pending' || run.value?.status === 'running',
)

const sortedPoints = computed<ParamSensitivityPoint[]>(() => {
  const ps = (run.value?.points ?? []).slice()
  ps.sort((a, b) => a.value - b.value)
  return ps
})

// ---------------------- 邻域稳定性 ----------------------
// "IC 只在单点 peak、邻近掉 30%+" 是经典过拟合信号（见因子手册）。
interface NeighborhoodStability {
  peakValue: number
  peakIc: number
  leftRetain: number | null
  rightRetain: number | null
  worst: number | null
  verdict: 'green' | 'yellow' | 'red' | 'na'
  note: string
}
const stability = computed<NeighborhoodStability | null>(() => {
  const ps = sortedPoints.value
  if (ps.length < 2) return null
  let peakIdx = -1
  let peakIc = -Infinity
  for (let i = 0; i < ps.length; i++) {
    const v = ps[i].ic_mean
    if (v != null && v > peakIc) { peakIc = v; peakIdx = i }
  }
  if (peakIdx < 0 || peakIc <= 0) {
    return {
      peakValue: NaN, peakIc: 0, leftRetain: null, rightRetain: null, worst: null,
      verdict: 'na',
      note: '所有点 IC 非正，无法评估稳定性（因子方向可能反或纯噪声）。',
    }
  }
  const leftIc = peakIdx > 0 ? ps[peakIdx - 1].ic_mean : null
  const rightIc = peakIdx < ps.length - 1 ? ps[peakIdx + 1].ic_mean : null
  const leftRetain = leftIc != null ? leftIc / peakIc : null
  const rightRetain = rightIc != null ? rightIc / peakIc : null
  const retains = [leftRetain, rightRetain].filter((x): x is number => x != null)
  const worst = retains.length ? Math.min(...retains) : null
  let verdict: 'green' | 'yellow' | 'red' | 'na' = 'na'
  let note = ''
  if (worst == null) {
    verdict = 'yellow'
    note = '峰值落在采样区间边界，至少一侧没有邻点可比较——扩大扫描范围才能判断稳定性。'
  } else if (worst >= 0.7) {
    verdict = 'green'
    note = '邻域稳定：±1 步仍保留 ≥70% 峰值 IC，参数位置可信。'
  } else if (worst >= 0.3) {
    verdict = 'yellow'
    note = '邻域部分不稳：±1 步 IC 掉 30%+，建议加密扫描步长或换更抗扰的默认值。'
  } else {
    verdict = 'red'
    note = '邻域极不稳：±1 步 IC 掉 70%+，峰值几乎是单点，强过拟合信号。'
  }
  return {
    peakValue: ps[peakIdx].value,
    peakIc,
    leftRetain, rightRetain, worst,
    verdict, note,
  }
})

function verdictTagType(v: string): 'success' | 'warning' | 'error' | 'default' {
  if (v === 'green') return 'success'
  if (v === 'yellow') return 'warning'
  if (v === 'red') return 'error'
  return 'default'
}
function verdictText(v: string): string {
  if (v === 'green') return '稳定'
  if (v === 'yellow') return '注意'
  if (v === 'red') return '过拟合'
  return '无法评估'
}
function fmtRetain(v: number | null): string {
  return v == null ? '—' : `${(v * 100).toFixed(0)}%`
}

// ---------------------- 图表 ----------------------
const chartOption = computed(() => {
  const ps = sortedPoints.value
  if (!run.value || ps.length === 0) return null
  const xs = ps.map((p) => String(p.value))
  const icData = ps.map((p) => p.ic_mean)
  const rankData = ps.map((p) => p.rank_ic_mean)
  const sharpeData = ps.map((p) => p.long_short_sharpe)
  const defaultVal = run.value.default_value

  const markLines: any[] = []
  if (typeof defaultVal === 'number') {
    markLines.push({
      xAxis: String(defaultVal),
      lineStyle: { color: '#848E9C', type: 'dashed', width: 1 },
      label: { show: true, formatter: `默认 ${defaultVal}`, color: '#606266', fontSize: 11 },
      symbol: 'none',
    })
  }

  const markPoints: any[] = []
  if (stability.value && !Number.isNaN(stability.value.peakValue)) {
    markPoints.push({
      name: '峰值',
      coord: [String(stability.value.peakValue), stability.value.peakIc],
      symbol: 'pin', symbolSize: 32,
      itemStyle: { color: '#F0B90B' },
      label: { formatter: '峰', fontSize: 10, fontWeight: 600, color: '#1E2026' },
    })
  }

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['IC 均值', 'Rank IC 均值', '多空 Sharpe'], top: 8 },
    grid: { left: 50, right: 60, bottom: 50, top: 48 },
    xAxis: {
      type: 'category', data: xs,
      name: run.value.param_name, nameLocation: 'middle', nameGap: 28,
    },
    yAxis: [
      { type: 'value', name: 'IC / Rank IC', position: 'left' },
      { type: 'value', name: '多空 Sharpe', position: 'right' },
    ],
    series: [
      {
        name: 'IC 均值', type: 'line', data: icData,
        lineStyle: { color: '#F0B90B', width: 2 }, itemStyle: { color: '#F0B90B' },
        symbol: 'circle', symbolSize: 8,
        markLine: { silent: true, data: markLines },
        markPoint: markPoints.length ? { silent: true, data: markPoints } : undefined,
      },
      {
        name: 'Rank IC 均值', type: 'line', data: rankData,
        lineStyle: { color: '#1E2026', width: 2 }, itemStyle: { color: '#1E2026' },
        symbol: 'triangle', symbolSize: 8,
      },
      {
        name: '多空 Sharpe', type: 'line', data: sharpeData, yAxisIndex: 1,
        lineStyle: { color: '#06C48E', width: 2, type: 'dashed' },
        itemStyle: { color: '#06C48E' }, symbol: 'diamond', symbolSize: 8,
      },
    ],
  }
})

// ---------------------- 表格 ----------------------
function fmtNum(v: any, digits = 4): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}

const tableCols: DataTableColumns<ParamSensitivityPoint> = [
  {
    title: '参数值', key: 'value', width: 110,
    render: (row) => {
      const isDefault = typeof run.value?.default_value === 'number'
        && row.value === run.value.default_value
      const isPeak = stability.value && !Number.isNaN(stability.value.peakValue)
        && row.value === stability.value.peakValue
      const tags: any[] = [String(row.value)]
      if (isDefault) tags.push(h(NTag, { size: 'tiny', type: 'info', style: 'margin-left: 4px' }, { default: () => '默认' }))
      if (isPeak) tags.push(h(NTag, { size: 'tiny', type: 'warning', style: 'margin-left: 4px' }, { default: () => '峰值' }))
      return h('span', {}, tags)
    },
  },
  { title: 'IC 均值', key: 'ic_mean', width: 110, render: (r) => fmtNum(r.ic_mean) },
  { title: 'Rank IC 均值', key: 'rank_ic_mean', width: 130, render: (r) => fmtNum(r.rank_ic_mean) },
  { title: 'IC IR', key: 'ic_ir', width: 100, render: (r) => fmtNum(r.ic_ir, 3) },
  { title: '多空 Sharpe', key: 'long_short_sharpe', width: 120, render: (r) => fmtNum(r.long_short_sharpe, 2) },
  {
    title: '多空年化', key: 'long_short_annret', width: 110,
    render: (r) => r.long_short_annret == null ? '-' : `${(r.long_short_annret * 100).toFixed(2)}%`,
  },
  {
    title: '换手率', key: 'turnover_mean', width: 100,
    render: (r) => r.turnover_mean == null ? '-' : `${(r.turnover_mean * 100).toFixed(1)}%`,
  },
  { title: 'IC 样本日', key: 'n_ic_days', width: 100, render: (r) => r.n_ic_days ?? '-' },
  {
    title: '错误', key: 'error', ellipsis: { tooltip: true },
    render: (r) => r.error ? h('span', { style: 'color: #F5222D' }, r.error.slice(0, 80)) : '',
  },
]

// values 在 run 顶层和成功后的 points 数组重复，此处直接用 run.values 展示基础信息，
// 还没跑出 points 时就不渲染稳定性 / 图表 / 表格。
// 是否栅格搜索（values_json 是 dict 而非 array）
const isGridSearch = computed(() => {
  const v = run.value?.values
  return v !== null && typeof v === 'object' && !Array.isArray(v)
})

// 栅格搜索结果表格列
const gsColumns: DataTableColumns<any> = [
  { title: '参数', key: 'params_display', width: 200, ellipsis: true },
  {
    title: 'IC Mean', key: 'ic_mean', width: 100,
    render: (r) => (r.ic_mean ?? 0).toFixed(4),
  },
  {
    title: 'Rank IC Mean', key: 'rank_ic_mean', width: 110,
    render: (r) => (r.rank_ic_mean ?? 0).toFixed(4),
  },
  {
    title: 'IC IR', key: 'ic_ir', width: 90,
    render: (r) => (r.ic_ir ?? 0).toFixed(3),
  },
  {
    title: 'Win Rate', key: 'ic_win_rate', width: 90,
    render: (r) => r.ic_win_rate != null ? (r.ic_win_rate * 100).toFixed(1) + '%' : '-',
  },
  { title: '样本天数', key: 'n_dates', width: 80 },
]

const gsResults = computed<any[]>(() => {
  const raw = run.value?.results ?? []
  return raw.map((r: any) => ({
    ...r,
    params_display: r.params ? JSON.stringify(r.params) : (r.error ?? '-'),
  }))
})

const gsBest = computed<any>(() => run.value?.best ?? null)

function fmtValues(v: number[] | Record<string, number[]> | null | undefined): string {
  if (!v) return '-'
  if (Array.isArray(v)) return v.length ? v.join(' / ') : '-'
  // dict 格式（栅格搜索）：{"window":[5,10,20],"skip":[0,5]}
  return Object.entries(v)
    .map(([k, vals]) => `${k}=[${(vals as number[]).join(', ')}]`)
    .join('  ')
}
</script>

<template>
  <div>
    <n-page-header
      :title="`参数扫描 ${runId.slice(0, 8)}...`"
      @back="router.back()"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <status-badge v-if="run" :status="run.status" />
      </template>
    </n-page-header>

    <n-spin :show="isLoading && !run">
      <n-card v-if="run" title="基础信息" style="margin-bottom: 16px">
        <n-descriptions :column="3" bordered>
          <n-descriptions-item label="因子">{{ run.factor_id }}</n-descriptions-item>
          <n-descriptions-item label="扫描参数">{{ run.param_name }}</n-descriptions-item>
          <n-descriptions-item label="股票池">{{ lookupPoolName(run.pool_id) }}</n-descriptions-item>
          <n-descriptions-item label="日期">{{ run.start_date }} ~ {{ run.end_date }}</n-descriptions-item>
          <n-descriptions-item label="分组数">{{ run.n_groups }}</n-descriptions-item>
          <n-descriptions-item label="前瞻期">{{ (run.forward_periods ?? []).join(' / ') || '-' }}</n-descriptions-item>
          <n-descriptions-item label="扫描点" :span="3">
            {{ fmtValues(run.values) }}
          </n-descriptions-item>
          <n-descriptions-item
            v-if="run.default_value != null"
            label="默认值"
            :span="3"
          >
            {{ run.default_value }}
          </n-descriptions-item>
        </n-descriptions>

        <div v-if="isRunning" style="margin-top: 16px">
          <n-progress
            type="line"
            :percentage="run.progress || 0"
            :status="run.status === 'failed' ? 'error' : 'default'"
          />
        </div>
        <n-alert v-if="run.status === 'failed'" type="error" style="margin-top: 16px">
          <pre style="white-space: pre-wrap; font-size: 12px">{{ run.error_message }}</pre>
        </n-alert>
      </n-card>

      <!-- 栅格搜索结果 -->
      <template v-if="isGridSearch && run?.status === 'success'">
        <n-card v-if="gsBest" size="small" style="margin-bottom: 16px">
          <template #header>
            <n-space align="center">
              <span>最优参数组合</span>
              <n-tag type="success" size="small" round>BEST</n-tag>
            </n-space>
          </template>
          <template #header-extra>
            <n-button
              size="small"
              type="primary"
              :loading="applyingBest"
              @click="applyBestParams"
            >
              应用为默认参数
            </n-button>
          </template>
          <div style="font-size: 14px; margin-bottom: 8px">
            <code style="font-size: 15px; font-weight: 600">{{ gsBest.params ? JSON.stringify(gsBest.params) : '-' }}</code>
          </div>
          <n-space :wrap="true" :size="16">
            <span>IC Mean: <b>{{ (gsBest.ic_mean ?? 0).toFixed(4) }}</b></span>
            <span>Rank IC: <b>{{ (gsBest.rank_ic_mean ?? 0).toFixed(4) }}</b></span>
            <span>IC IR: <b>{{ (gsBest.ic_ir ?? 0).toFixed(3) }}</b></span>
            <span>Win Rate: <b>{{ gsBest.ic_win_rate != null ? (gsBest.ic_win_rate * 100).toFixed(1) + '%' : '-' }}</b></span>
            <span>样本: <b>{{ gsBest.n_dates ?? '-' }} 天</b></span>
          </n-space>
        </n-card>

        <n-card size="small" style="margin-bottom: 16px">
          <template #header>
            全部组合（{{ gsResults.length }}，按 {{ run.optimize_by ?? 'ic_mean' }} 排序）
          </template>
          <n-data-table
            :columns="gsColumns"
            :data="gsResults"
            :bordered="false"
            :single-line="false"
            size="small"
            :max-height="500"
            :row-key="(_r: any, idx: number) => idx"
          />
        </n-card>
      </template>

      <template v-if="!isGridSearch && run?.status === 'success' && sortedPoints.length > 0">
        <!-- 稳定性摘要 -->
        <n-card size="small" style="margin-bottom: 16px">
          <template #header>
            <n-space align="center">
              <span>邻域稳定性</span>
              <n-tag
                v-if="stability"
                :type="verdictTagType(stability.verdict)"
                size="small" round
              >
                {{ verdictText(stability.verdict) }}
              </n-tag>
            </n-space>
          </template>
          <div v-if="stability" style="display: flex; flex-wrap: wrap; gap: 20px 32px; font-size: 13px; color: #606266">
            <div>
              IC 峰值：
              <b style="color: #F0B90B">
                {{ Number.isNaN(stability.peakValue) ? '—' : `${stability.peakValue} ( IC=${stability.peakIc.toFixed(4)} )` }}
              </b>
            </div>
            <div>左邻点保留：<b>{{ fmtRetain(stability.leftRetain) }}</b></div>
            <div>右邻点保留：<b>{{ fmtRetain(stability.rightRetain) }}</b></div>
            <div>最差邻点保留：<b>{{ fmtRetain(stability.worst) }}</b></div>
            <div style="flex-basis: 100%; color: #848E9C">{{ stability.note }}</div>
          </div>
        </n-card>

        <!-- 图表 -->
        <n-card title="指标 vs 参数值" size="small" style="margin-bottom: 16px">
          <v-chart v-if="chartOption" :option="chartOption" autoresize style="width: 100%; height: 320px" />
          <n-empty v-else description="无数据" />
        </n-card>

        <!-- 数据表 -->
        <n-card title="扫描明细" size="small">
          <n-data-table
            :columns="tableCols"
            :data="sortedPoints"
            :bordered="false"
            :single-line="false"
            :row-key="(row: any) => row.value"
            size="small"
          />
        </n-card>
      </template>

      <n-empty
        v-if="run?.status === 'success' && sortedPoints.length === 0"
        description="结果为空（可能所有扫描点都失败了，请看 error 列）"
      />
    </n-spin>
  </div>
</template>
