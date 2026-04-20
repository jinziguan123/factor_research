<script setup lang="ts">
/**
 * 参数敏感性（MVP）：单页面"表单 + 结果"一体。
 *
 * 扫 factor 的一个超参在 N 个取值下的评估指标，同步返回。没有列表 / 没有中断，
 * 用户扫完截图或写到评估备忘即可。要做持久化再升级到 runs 表。
 *
 * 重点 UX：
 * - 参数下拉自动从 factor.params_schema 出；values 默认生成"等距 7 点扫描"，
 *   用户可增删（NDynamicTags）。
 * - 结果区用双 Y 轴同时画 IC / Rank IC / 多空 Sharpe，并标注默认参数位置 +
 *   峰值位置，给"邻域稳定性百分比"做顶部数字卡片。
 */
import { computed, h, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NDynamicTags, NButton, NSpin, NAlert, NCard, NSpace, NTag,
  NDataTable, NEmpty, useMessage,
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
import { useFactors, useFactor } from '@/api/factors'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import {
  usePreviewParamSensitivity,
  type ParamSensitivityResult,
  type ParamSensitivityPoint,
} from '@/api/param_sensitivity'

use([
  CanvasRenderer, LineChart,
  GridComponent, TooltipComponent, LegendComponent,
  MarkLineComponent, MarkPointComponent,
])

const router = useRouter()
const message = useMessage()

// ---------------------- 表单状态 ----------------------
const { data: factors, isLoading: factorsLoading } = useFactors()
const factorOptions = computed(() => {
  const groups: Record<string, { label: string; value: string }[]> = {}
  for (const f of factors.value ?? []) {
    const cat = f.category || 'custom'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push({ label: f.display_name, value: f.factor_id })
  }
  return Object.entries(groups).map(([cat, children]) => ({
    type: 'group' as const, label: cat, key: cat, children,
  }))
})

const selectedFactorId = ref('')
const selectedFactor = useFactor(selectedFactorId)

const paramOptions = computed(() => {
  const schema = selectedFactor.data.value?.params_schema ?? {}
  return Object.entries(schema).map(([key, entry]: [string, any]) => ({
    label: `${key}${entry?.desc ? `（${entry.desc}）` : ''}`,
    value: key,
  }))
})
const selectedParam = ref<string | null>(null)

// 默认值列表：从 schema 的 min/max/default 推 7 个等距扫描点，覆盖 default 的两侧。
// 没有 min/max 时退化成 [default*0.5, default*0.75, default, default*1.25, ...]。
function suggestValues(schemaEntry: any, defaultValue: any): string[] {
  if (schemaEntry && typeof schemaEntry.min === 'number' && typeof schemaEntry.max === 'number') {
    const { min, max } = schemaEntry
    const n = 7
    const step = (max - min) / (n - 1)
    return Array.from({ length: n }, (_, i) => {
      const v = min + step * i
      return schemaEntry.type === 'int' ? String(Math.round(v)) : v.toFixed(3)
    })
  }
  if (typeof defaultValue === 'number') {
    const d = defaultValue
    const mul = [0.5, 0.75, 1, 1.25, 1.5]
    return mul.map((m) =>
      (schemaEntry?.type === 'int') ? String(Math.max(1, Math.round(d * m))) : (d * m).toFixed(3),
    )
  }
  return ['10', '20', '30', '40', '50']
}

const valuesInput = ref<string[]>([])
// 切因子 / 切参数时自动填充扫描点，保留用户手改后的状态。
watch([() => selectedParam.value, () => selectedFactor.data.value], ([paramKey, factor]) => {
  if (!paramKey || !factor) return
  const schema = factor.params_schema ?? {}
  const entry = schema[paramKey]
  const def = factor.default_params?.[paramKey]
  valuesInput.value = suggestValues(entry, def)
})

const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const forwardPeriodsInput = ref<string[]>(['1', '5', '10'])

// ---------------------- 提交 / 结果 ----------------------
const { mutateAsync, isPending } = usePreviewParamSensitivity()
const result = ref<ParamSensitivityResult | null>(null)
const errorMsg = ref<string>('')

async function handleSubmit() {
  errorMsg.value = ''
  if (!selectedFactorId.value) return message.warning('请选择因子')
  if (!selectedParam.value) return message.warning('请选择要扫的参数')
  if (!poolId.value) return message.warning('请选择股票池')
  if (!dateRange.value) return message.warning('请选择日期区间')

  const vals = Array.from(new Set(
    valuesInput.value.map((s) => Number(s)).filter((x) => Number.isFinite(x)),
  )).sort((a, b) => a - b)
  if (vals.length < 2) return message.warning('至少需要 2 个不同的扫描点')
  if (vals.length > 15) return message.warning('扫描点过多（>15），建议控制在 5-10 个')

  const fwdPeriods = forwardPeriodsInput.value
    .map((s) => Number(s)).filter((x) => Number.isFinite(x) && x > 0)
  if (fwdPeriods.length === 0) return message.warning('请填写至少 1 个前瞻期')

  const body = {
    factor_id: selectedFactorId.value,
    param_name: selectedParam.value,
    values: vals,
    pool_id: poolId.value,
    start_date: new Date(dateRange.value[0]).toISOString().slice(0, 10),
    end_date: new Date(dateRange.value[1]).toISOString().slice(0, 10),
    n_groups: nGroups.value,
    forward_periods: fwdPeriods,
  }
  try {
    const res = await mutateAsync(body)
    result.value = res
    message.success(`扫完 ${res.points.length} 个点`)
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail ?? e?.message ?? '扫描失败'
    message.error(errorMsg.value)
  }
}

// ---------------------- 结果分析 ----------------------
// 邻域稳定性：按 value 升序排的点数组里找 ic_mean 峰值，相邻两点 IC 的最小保留率。
// "IC 只在单点 peak、邻近掉 30%+" 是经典过拟合信号（见因子手册）。
interface NeighborhoodStability {
  peakValue: number
  peakIc: number
  leftRetain: number | null  // 左邻点 IC / 峰值 IC（null 表示峰值已在左边界）
  rightRetain: number | null
  worst: number | null
  verdict: 'green' | 'yellow' | 'red' | 'na'
  note: string
}

const sortedPoints = computed<ParamSensitivityPoint[]>(() => {
  const ps = (result.value?.points ?? []).slice()
  ps.sort((a, b) => a.value - b.value)
  return ps
})
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

// ---------------------- 图表 ----------------------
const chartOption = computed(() => {
  const ps = sortedPoints.value
  if (!result.value || ps.length === 0) return null
  const xs = ps.map((p) => String(p.value))
  const icData = ps.map((p) => p.ic_mean)
  const rankData = ps.map((p) => p.rank_ic_mean)
  const sharpeData = ps.map((p) => p.long_short_sharpe)
  const defaultVal = result.value.default_value

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
      name: result.value.param_name, nameLocation: 'middle', nameGap: 28,
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
      const isDefault = typeof result.value?.default_value === 'number'
        && row.value === result.value.default_value
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
</script>

<template>
  <div>
    <n-page-header title="参数敏感性扫描" style="margin-bottom: 16px">
      <template #subtitle>
        扫一个超参数在 N 个取值下的 IC / Sharpe，判断是否过拟合到单点
      </template>
    </n-page-header>

    <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
      扫描是同步的：每个点约 20-60 秒，5 个点的典型扫描约 2-3 分钟。扫完直接看结果，
      <b>不会落库不会有历史</b>——适合研究阶段"一次定稿"的判断，不适合需要反复翻阅的场景。
      想看历史请去评估记录页重新跑单点评估。
    </n-alert>

    <n-card title="扫描配置" size="small" style="margin-bottom: 16px">
      <n-form label-placement="left" label-width="120px" style="max-width: 760px">
        <n-form-item label="因子" required>
          <n-select
            v-model:value="selectedFactorId"
            :options="factorOptions"
            :loading="factorsLoading"
            placeholder="选择因子"
            filterable
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item label="扫描参数" required>
          <n-select
            v-model:value="selectedParam"
            :options="paramOptions"
            :disabled="!selectedFactorId || paramOptions.length === 0"
            :placeholder="selectedFactorId ? (paramOptions.length ? '选择要扫的参数' : '该因子无 params_schema') : '先选因子'"
            style="width: 100%"
          />
        </n-form-item>
        <n-form-item label="扫描点">
          <n-dynamic-tags v-model:value="valuesInput" />
        </n-form-item>
        <n-form-item label="股票池" required>
          <pool-selector v-model:value="poolId" style="width: 100%" />
        </n-form-item>
        <n-form-item label="日期区间" required>
          <n-date-picker v-model:value="dateRange" type="daterange" clearable style="width: 100%" />
        </n-form-item>
        <n-form-item label="分组数">
          <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 160px" />
        </n-form-item>
        <n-form-item label="前瞻期（日）">
          <n-dynamic-tags v-model:value="forwardPeriodsInput" />
        </n-form-item>
        <n-form-item>
          <n-space>
            <n-button
              type="primary"
              :loading="isPending"
              @click="handleSubmit"
              style="border-radius: 20px; padding: 0 32px"
            >
              {{ isPending ? '扫描中（可能需要 1-3 分钟）…' : '开始扫描' }}
            </n-button>
            <n-button quaternary @click="router.push('/docs/factor-guide')">
              看"参数敏感性"术语解释
            </n-button>
          </n-space>
        </n-form-item>
      </n-form>
    </n-card>

    <n-spin :show="isPending" description="因子每个参数值都要重新计算一次，请耐心等…">
      <template v-if="result">
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
            size="small"
          />
        </n-card>
      </template>

      <n-alert v-else-if="errorMsg" type="error" :show-icon="false">
        {{ errorMsg }}
      </n-alert>
    </n-spin>
  </div>
</template>
