<script setup lang="ts">
/**
 * 数据健康度仪表盘：跨表 meta 视图。
 *
 * 三块：
 * 1) 顶部：所有同步表的卡片（行数 / 时间范围 / 最新 updated_at）
 * 2) 中部：profit 字段缺失率（按行业 + 按字段双维度热力 / 表）+ 按季度记录数趋势
 * 3) 底部：指数成分概览（活跃数、调整次数、最近 / 最早调整日）
 *
 * 接口：GET /api/data_health/summary、GET /api/data_health/profit_coverage
 */
import { computed, onMounted, ref, h } from 'vue'
import {
  NPageHeader, NSpace, NCard, NGrid, NGi, NStatistic, NTag, NSpin,
  NAlert, NDataTable, NButton, NDivider,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { client } from '@/api/client'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, HeatmapChart, LineChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  VisualMapComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([
  CanvasRenderer, BarChart, HeatmapChart, LineChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, VisualMapComponent,
])

interface TableMeta {
  table: string
  label: string
  missing: boolean
  rows: number
  latest_updated_at: string | null
  time_min: string | null
  time_max: string | null
  extra: Record<string, any>
}
interface IndexSummaryRow {
  index_code: string
  active: number
  adjustments: number
  first_adjustment: string | null
  last_adjustment: string | null
}
interface IndustryRow {
  industry_l1: string
  total: number
  null_rates: Record<string, number>
}
interface QuarterRow {
  report_date: string
  symbols: number
}

const summaryLoading = ref(false)
const showAllIndustries = ref(false)
const TOP_N_INDUSTRIES = 30
const coverageLoading = ref(false)
const tables = ref<TableMeta[]>([])
const indexSummary = ref<IndexSummaryRow[]>([])
const industries = ref<IndustryRow[]>([])
const quarters = ref<QuarterRow[]>([])
const fields = ref<string[]>([])
const errMsg = ref('')

async function loadAll() {
  errMsg.value = ''
  summaryLoading.value = true
  coverageLoading.value = true
  try {
    const [s, c] = await Promise.all([
      client.get('/data_health/summary'),
      client.get('/data_health/profit_coverage'),
    ])
    tables.value = s.data.tables
    indexSummary.value = s.data.index_summary
    industries.value = c.data.by_industry
    quarters.value = c.data.by_quarter
    fields.value = c.data.fields
  } catch (e: any) {
    errMsg.value = e?.message || '加载失败'
  } finally {
    summaryLoading.value = false
    coverageLoading.value = false
  }
}

onMounted(loadAll)

// ---------- 表卡片 ----------

function timeRangeText(t: TableMeta): string {
  if (t.missing) return '表未创建'
  if (!t.time_min && !t.time_max) return '—'
  return `${t.time_min ?? '?'} ~ ${t.time_max ?? '?'}`
}

// ---------- profit 缺失率热力（按行业 × 字段） ----------

// 字段中文名映射
const FIELD_LABELS: Record<string, string> = {
  roe_avg: 'ROE',
  np_margin: '净利率',
  gp_margin: '毛利率',
  net_profit: '净利润',
  eps_ttm: 'EPS_TTM',
  mb_revenue: '主营收入',
  total_share: '总股本',
  liqa_share: '流通股本',
}

// 行业按 total 降序由后端返回；行数太多时只取 Top N（用户可点击切换全部）
const visibleIndustries = computed(() => {
  if (showAllIndustries.value) return industries.value
  return industries.value.slice(0, TOP_N_INDUSTRIES)
})

const heatmapOption = computed(() => {
  const visible = visibleIndustries.value
  if (!visible.length || !fields.value.length) return {}
  const xAxis = fields.value.map(f => FIELD_LABELS[f] || f)
  const yAxis = visible.map(r => r.industry_l1)
  // data: [colIdx, rowIdx, nullRate%]
  const data: [number, number, number][] = []
  visible.forEach((row, ri) => {
    fields.value.forEach((f, ci) => {
      data.push([ci, ri, Math.round(row.null_rates[f] * 1000) / 10])
    })
  })
  return {
    tooltip: {
      formatter: (p: any) => {
        const ind = yAxis[p.value[1]]
        const fld = xAxis[p.value[0]]
        const total = visible[p.value[1]].total
        return `${ind} · ${fld}<br/>缺失率: <b>${p.value[2]}%</b><br/>样本: ${total}`
      },
    },
    grid: { left: 100, right: 30, bottom: 30, top: 40, containLabel: true },
    xAxis: { type: 'category', data: xAxis, splitArea: { show: true }, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'category', data: yAxis, splitArea: { show: true }, axisLabel: { fontSize: 11 } },
    visualMap: {
      min: 0, max: 100, calculable: true,
      orient: 'horizontal', left: 'center', top: 'top',
      inRange: { color: ['#52C41A', '#FAAD14', '#F5222D'] },
      text: ['100%', '0%'], textStyle: { fontSize: 11 },
    },
    series: [{
      name: '缺失率',
      type: 'heatmap',
      data,
      label: {
        show: true,
        formatter: (p: any) => p.value[2] > 0 ? `${p.value[2]}%` : '',
        fontSize: 10,
      },
    }],
  }
})

// ---------- profit 季度记录数趋势 ----------

const quarterlyOption = computed(() => {
  if (!quarters.value.length) return {}
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 30, bottom: 50, top: 30 },
    xAxis: { type: 'category', data: quarters.value.map(q => q.report_date),
             axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: 'value', name: '有数据的股票数' },
    dataZoom: [{ type: 'slider', start: 0, end: 100, bottom: 5 }],
    series: [{
      type: 'bar',
      data: quarters.value.map(q => q.symbols),
      itemStyle: { color: '#F0B90B' },
    }],
  }
})

// ---------- 指数成分表 ----------

const indexCols: DataTableColumns<IndexSummaryRow> = [
  { title: '指数', key: 'index_code', width: 130, render: (r) => h(NTag, { type: 'info', size: 'small' }, { default: () => r.index_code }) },
  { title: '当前活跃', key: 'active', width: 100 },
  { title: '历次调整', key: 'adjustments', width: 100 },
  { title: '最早调整日', key: 'first_adjustment', width: 130 },
  { title: '最近调整日', key: 'last_adjustment', width: 130 },
]
</script>

<template>
  <div>
    <n-page-header title="数据健康度" style="margin-bottom: 16px">
      <template #subtitle>
        所有同步表的元信息 + 缺失率 + 指数成分概览
      </template>
      <template #extra>
        <n-button :loading="summaryLoading || coverageLoading" @click="loadAll">
          刷新
        </n-button>
      </template>
    </n-page-header>

    <n-alert v-if="errMsg" type="error" :show-icon="false" style="margin-bottom: 12px">
      {{ errMsg }}
    </n-alert>

    <!-- 1. 表卡片 -->
    <n-card title="一、同步表概览" size="small" style="margin-bottom: 16px">
      <n-spin :show="summaryLoading">
        <n-grid :x-gap="12" :y-gap="12" cols="1 s:1 m:2 l:3" responsive="screen">
          <n-gi v-for="t in tables" :key="t.table">
            <n-card size="small" :style="t.missing ? 'opacity: 0.5' : ''">
              <n-space vertical :size="4">
                <n-space justify="space-between" align="center">
                  <span style="font-weight: 600">{{ t.label }}</span>
                  <n-tag size="tiny" :type="t.missing ? 'warning' : 'success'">
                    {{ t.missing ? '未建表' : t.table }}
                  </n-tag>
                </n-space>
                <n-statistic label="行数" :value="t.rows.toLocaleString()" />
                <div style="color: #888; font-size: 12px">
                  时间范围: {{ timeRangeText(t) }}
                </div>
                <div v-if="t.latest_updated_at" style="color: #888; font-size: 12px">
                  最后更新: {{ t.latest_updated_at }}
                </div>
                <div v-for="(v, k) in t.extra" :key="k" style="color: #888; font-size: 12px">
                  {{ k }}: {{ v }}
                </div>
              </n-space>
            </n-card>
          </n-gi>
        </n-grid>
      </n-spin>
    </n-card>

    <!-- 2. profit 字段缺失率 + 季度趋势 -->
    <n-card title="二、财报数据覆盖率" size="small" style="margin-bottom: 16px">
      <n-spin :show="coverageLoading">
        <n-space vertical :size="20">
          <div v-if="!industries.length" style="text-align: center; color: #888; padding: 24px">
            尚无 fr_fundamental_profit 数据，跑完一次 sync_profit 再回来。
          </div>
          <template v-else>
            <div>
              <n-divider title-placement="left" style="margin-top: 0">
                <span style="font-weight: 600">2.1 字段缺失率（行业 × 指标）</span>
              </n-divider>
              <n-alert type="info" :show-icon="false" style="margin-bottom: 8px">
                颜色越红代表该行业该字段缺失越多。<b>资本市场服务 / 货币金融服务 等金融业 gp_margin 普遍 90%+ 缺失</b>
                — 因为这类公司没有"营业成本"会计科目；季报普遍缺 mb_revenue（主营收入只在年报和半年报披露）。
              </n-alert>
              <n-space justify="space-between" align="center" style="margin-bottom: 8px">
                <span style="color: #888; font-size: 12px">
                  共 {{ industries.length }} 个一级行业，按样本量降序；
                  当前显示 {{ visibleIndustries.length }} 个
                </span>
                <n-button size="small" tertiary @click="showAllIndustries = !showAllIndustries">
                  {{ showAllIndustries ? `收起到 Top ${TOP_N_INDUSTRIES}` : `显示全部 ${industries.length} 个行业` }}
                </n-button>
              </n-space>
              <v-chart :option="heatmapOption" autoresize
                       :style="{ width: '100%', height: Math.max(visibleIndustries.length * 28 + 80, 320) + 'px' }" />
            </div>

            <div>
              <n-divider title-placement="left">
                <span style="font-weight: 600">2.2 各季度有数据的股票数</span>
              </n-divider>
              <v-chart :option="quarterlyOption" autoresize style="width: 100%; height: 280px" />
            </div>
          </template>
        </n-space>
      </n-spin>
    </n-card>

    <!-- 3. 指数成分概览 -->
    <n-card title="三、指数成分概览" size="small">
      <n-spin :show="summaryLoading">
        <n-data-table
          :columns="indexCols"
          :data="indexSummary"
          :bordered="false"
          :single-line="false"
          size="small"
        />
        <div v-if="!indexSummary.length" style="text-align: center; color: #888; padding: 16px">
          尚无 fr_index_constituent 数据。
        </div>
      </n-spin>
    </n-card>
  </div>
</template>
