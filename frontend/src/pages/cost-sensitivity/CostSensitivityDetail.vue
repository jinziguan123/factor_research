<script setup lang="ts">
/**
 * 成本敏感性分析详情页。
 * - 顶部：run 基础信息 + 状态 + 进度条；
 * - 成功后：敏感曲线（三条：年化、Sharpe、总周转）+ 每点指标表格。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NAlert, NDataTable, NEmpty, NGrid, NGridItem,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { useCostSensitivity } from '@/api/cost_sensitivity'
import type { SensitivityPoint } from '@/api/cost_sensitivity'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useCostSensitivity(runId)

const { lookup: lookupPoolName } = usePoolNameMap()

const isRunning = computed(
  () => run.value?.status === 'pending' || run.value?.status === 'running',
)

const points = computed<SensitivityPoint[]>(() => run.value?.points ?? [])

function fmtPct(v: any, digits = 2): string {
  if (v == null) return '-'
  return typeof v === 'number' ? (v * 100).toFixed(digits) + '%' : String(v)
}
function fmtNum(v: any, digits = 3): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}

// 敏感曲线：x = cost_bps；三条 y 轴对应语义不同的指标，分开两幅图渲染更清晰。
const returnChartOption = computed(() => {
  const pts = points.value
  return {
    title: { text: '成本 → 年化收益 / Sharpe', textStyle: { fontSize: 14, color: '#1E2026' } },
    tooltip: { trigger: 'axis' },
    legend: { data: ['年化收益', 'Sharpe'], top: 30 },
    grid: { left: 60, right: 60, bottom: 40, top: 70 },
    xAxis: {
      type: 'category',
      name: 'cost (bps)',
      data: pts.map((p) => String(p.cost_bps)),
    },
    yAxis: [
      { type: 'value', name: '年化收益', axisLabel: { formatter: (v: number) => (v * 100).toFixed(1) + '%' } },
      { type: 'value', name: 'Sharpe' },
    ],
    series: [
      {
        name: '年化收益',
        type: 'line',
        yAxisIndex: 0,
        data: pts.map((p) => p.annual_return),
        lineStyle: { color: '#F0B90B', width: 2 },
        itemStyle: { color: '#F0B90B' },
        symbol: 'circle',
        symbolSize: 8,
        connectNulls: true,
      },
      {
        name: 'Sharpe',
        type: 'line',
        yAxisIndex: 1,
        data: pts.map((p) => p.sharpe_ratio),
        lineStyle: { color: '#1E2026', width: 2 },
        itemStyle: { color: '#1E2026' },
        symbol: 'circle',
        symbolSize: 8,
        connectNulls: true,
      },
    ],
  }
})

const turnoverChartOption = computed(() => {
  const pts = points.value
  return {
    title: { text: '成本 → 总周转', textStyle: { fontSize: 14, color: '#1E2026' } },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 40, bottom: 40, top: 60 },
    xAxis: {
      type: 'category',
      name: 'cost (bps)',
      data: pts.map((p) => String(p.cost_bps)),
    },
    yAxis: { type: 'value', name: '周转倍数' },
    series: [
      {
        name: '总周转',
        type: 'line',
        data: pts.map((p) => p.turnover_total),
        areaStyle: { color: 'rgba(240, 185, 11, 0.25)' },
        lineStyle: { color: '#F0B90B', width: 2 },
        itemStyle: { color: '#F0B90B' },
        symbol: 'circle',
        symbolSize: 8,
        connectNulls: true,
      },
    ],
  }
})

const columns: DataTableColumns<SensitivityPoint> = [
  {
    title: '成本 (bps)',
    key: 'cost_bps',
    width: 110,
    render: (row) => `${row.cost_bps}bp`,
  },
  { title: '年化收益', key: 'annual_return', width: 120, render: (r) => fmtPct(r.annual_return) },
  { title: '总收益', key: 'total_return', width: 120, render: (r) => fmtPct(r.total_return) },
  { title: 'Sharpe', key: 'sharpe_ratio', width: 100, render: (r) => fmtNum(r.sharpe_ratio) },
  { title: '最大回撤', key: 'max_drawdown', width: 120, render: (r) => fmtPct(r.max_drawdown) },
  { title: '胜率', key: 'win_rate', width: 100, render: (r) => fmtPct(r.win_rate) },
  { title: '交易笔数', key: 'trade_count', width: 100 },
  { title: '总周转', key: 'turnover_total', width: 120, render: (r) => fmtNum(r.turnover_total, 2) },
]

// 解读提示：年化收益在"成本 0 → 20bp"间衰减 >50% 说明策略几乎完全被成本吃掉。
// 页面把这个判断作为 NAlert 警示，不强依赖任何魔法数，只在有明显衰减时才展示。
const decayAlert = computed(() => {
  const pts = points.value
  if (pts.length < 2) return null
  const best = pts[0]?.annual_return
  const worst = pts[pts.length - 1]?.annual_return
  if (best == null || worst == null) return null
  if (best <= 0) return null // 本身就亏不叫"被成本吃掉"
  const ratio = worst / best
  if (ratio < 0.5) {
    return `在 ${pts[pts.length - 1].cost_bps}bp 下年化收益仅剩 ${pts[0].cost_bps}bp 水平的 ${(
      ratio * 100
    ).toFixed(0)}%，策略对交易成本高度敏感，实盘前请审慎评估。`
  }
  return null
})
</script>

<template>
  <div>
    <n-page-header
      :title="`敏感性 ${runId.slice(0, 8)}...`"
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
          <n-descriptions-item label="股票池">{{ lookupPoolName(run.pool_id) }}</n-descriptions-item>
          <n-descriptions-item label="日期">{{ run.start_date }} ~ {{ run.end_date }}</n-descriptions-item>
          <n-descriptions-item label="分组数">{{ run.n_groups }}</n-descriptions-item>
          <n-descriptions-item label="调仓周期">{{ run.rebalance_period }} 天</n-descriptions-item>
          <n-descriptions-item label="持仓方式">{{ run.position }}</n-descriptions-item>
          <n-descriptions-item label="成本点" :span="3">
            <span v-if="Array.isArray(run.cost_bps_list)">
              {{ (run.cost_bps_list as number[]).map(x => x + 'bp').join(' / ') }}
            </span>
            <span v-else>{{ run.cost_bps_list }}</span>
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

      <n-alert v-if="decayAlert" type="warning" style="margin-bottom: 16px">
        {{ decayAlert }}
      </n-alert>

      <n-card v-if="run?.status === 'success' && points.length > 0" title="敏感曲线" style="margin-bottom: 16px">
        <n-grid :cols="2" :x-gap="16" responsive="screen" item-responsive>
          <n-grid-item span="1 m:1">
            <v-chart :option="returnChartOption" autoresize style="width: 100%; height: 340px" />
          </n-grid-item>
          <n-grid-item span="1 m:1">
            <v-chart :option="turnoverChartOption" autoresize style="width: 100%; height: 340px" />
          </n-grid-item>
        </n-grid>
      </n-card>

      <n-card v-if="run?.status === 'success' && points.length > 0" title="每点指标">
        <n-data-table
          :columns="columns"
          :data="points"
          :bordered="false"
          :single-line="false"
          :row-key="(row: any) => row.cost_bps"
        />
      </n-card>

      <n-empty
        v-if="run?.status === 'success' && points.length === 0"
        description="结果为空（可能窗口内无可交易数据）"
      />
    </n-spin>
  </div>
</template>
