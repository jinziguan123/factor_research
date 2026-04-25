<script setup lang="ts">
/**
 * 财报数据探查（profit / 盈利能力）页：
 *
 * 模式 A — 单股时间序列：选股票 → ROE/毛利率/净利率/EPS/归母净利 5 条曲线 + 原始数据表
 * 模式 B — 季度截面排行：选 report_date + 指标 → TopN/BottomN + 行业中位数对比
 *
 * 接口：
 * - GET /api/fundamentals/metrics
 * - GET /api/fundamentals/profit/quarters
 * - GET /api/fundamentals/profit/series?symbol=
 * - GET /api/fundamentals/profit/cross_section?report_date=&metric=&top=
 */
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NPageHeader, NSpace, NCard, NInput, NSelect, NButton, NTabs, NTabPane,
  NDataTable, NSpin, NAlert, NEmpty, NTag, NInputNumber, NDivider,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'
import { client } from '@/api/client'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([
  CanvasRenderer, LineChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
])

// ---------------------------- 类型 ----------------------------

interface MetricDef { key: string; label: string }
interface QuarterRow { report_date: string; symbols: number }
interface SeriesRow {
  report_date: string
  announcement_date: string | null
  roe_avg: number | null
  np_margin: number | null
  gp_margin: number | null
  eps_ttm: number | null
  net_profit: number | null
}
interface SeriesResp {
  symbol: string
  name: string
  industry_l1: string
  metrics: MetricDef[]
  rows: SeriesRow[]
}
interface CrossItem {
  symbol: string; name: string; industry_l1: string; value: number
}
interface IndustryAgg {
  industry_l1: string; count: number; mean: number; median: number
}
interface CrossResp {
  report_date: string
  metric: string
  metric_label: string
  total: number
  top: CrossItem[]
  bottom: CrossItem[]
  by_industry: IndustryAgg[]
}

// ---------------------------- 全局状态 ----------------------------

const errMsg = ref('')
const metrics = ref<MetricDef[]>([])
const quarters = ref<QuarterRow[]>([])

// ---------------------------- 模式 A：单股时间序列 ----------------------------

const symbolInput = ref('600519.SH')
const seriesData = ref<SeriesResp | null>(null)
const seriesLoading = ref(false)

async function loadSeries() {
  const sym = symbolInput.value.trim().toUpperCase()
  if (!sym) {
    errMsg.value = '请输入股票代码'
    return
  }
  errMsg.value = ''
  seriesLoading.value = true
  try {
    const r = await client.get('/fundamentals/profit/series', { params: { symbol: sym } })
    seriesData.value = r.data
  } catch (e: any) {
    errMsg.value = e?.message || '加载时间序列失败'
    seriesData.value = null
  } finally {
    seriesLoading.value = false
  }
}

// ECharts 选项：5 条曲线，左轴比例(0~1)、右轴金额(net_profit)
// 因为指标量纲差异大（ROE 几个点 vs 净利润几十亿），把 net_profit 单独放右轴
const seriesOption = computed(() => {
  const d = seriesData.value
  if (!d || !d.rows.length) return null
  const dates = d.rows.map(r => r.report_date)
  const RATIO_KEYS = ['roe_avg', 'np_margin', 'gp_margin']
  const ABSOLUTE_KEYS = ['eps_ttm', 'net_profit']

  const ratioSeries = RATIO_KEYS.map(k => {
    const meta = d.metrics.find(m => m.key === k)!
    return {
      name: meta.label,
      type: 'line',
      yAxisIndex: 0,
      connectNulls: false,
      smooth: false,
      symbol: 'circle',
      symbolSize: 6,
      data: d.rows.map(r => (r as any)[k]),
    }
  })
  const epsSeries = {
    name: d.metrics.find(m => m.key === 'eps_ttm')!.label,
    type: 'line',
    yAxisIndex: 1,
    connectNulls: false,
    symbol: 'triangle',
    symbolSize: 7,
    data: d.rows.map(r => r.eps_ttm),
  }
  const npSeries = {
    name: d.metrics.find(m => m.key === 'net_profit')!.label,
    type: 'bar',
    yAxisIndex: 2,
    data: d.rows.map(r => r.net_profit),
    itemStyle: { color: 'rgba(240,185,11,0.35)' },
    barWidth: 14,
  }

  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { textStyle: { color: '#bbb' }, top: 0 },
    grid: { left: 60, right: 70, top: 36, bottom: 50 },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { color: '#999', rotate: 45 },
    },
    yAxis: [
      {
        type: 'value', name: '比率', position: 'left',
        axisLabel: {
          color: '#999',
          formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: '#333' } },
      },
      {
        type: 'value', name: 'EPS', position: 'right',
        axisLabel: { color: '#999' },
        splitLine: { show: false },
      },
      {
        type: 'value', name: '净利润', position: 'right', offset: 60,
        axisLabel: {
          color: '#999',
          formatter: (v: number) => {
            if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
            if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`
            return v.toString()
          },
        },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'slider', xAxisIndex: 0, height: 18, bottom: 4 },
    ],
    series: [...ratioSeries, epsSeries, npSeries],
  }
})

const seriesCols = computed<DataTableColumns<SeriesRow>>(() => {
  const d = seriesData.value
  if (!d) return []
  return [
    { title: '报告期', key: 'report_date', width: 110 },
    { title: '披露日', key: 'announcement_date', width: 110,
      render: (r) => r.announcement_date || '-' },
    ...d.metrics.map(m => ({
      title: m.label, key: m.key, width: 130,
      render: (r: any) => {
        const v = r[m.key]
        if (v === null || v === undefined) return h('span', { style: 'color:#666' }, '-')
        if (m.key === 'net_profit') {
          if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
          return v.toLocaleString()
        }
        if (m.key === 'eps_ttm') return v.toFixed(4)
        return `${(v * 100).toFixed(2)}%`
      },
    })),
  ]
})

// ---------------------------- 模式 B：季度截面 ----------------------------

const selectedQuarter = ref<string | null>(null)
const selectedMetric = ref<string>('roe_avg')
const topN = ref<number>(30)
const crossData = ref<CrossResp | null>(null)
const crossLoading = ref(false)

const quarterOptions = computed<SelectOption[]>(() =>
  quarters.value.map(q => ({
    label: `${q.report_date}（${q.symbols} 只）`,
    value: q.report_date,
  })))

const metricOptions = computed<SelectOption[]>(() =>
  metrics.value.map(m => ({ label: m.label, value: m.key })))

async function loadCross() {
  if (!selectedQuarter.value || !selectedMetric.value) return
  errMsg.value = ''
  crossLoading.value = true
  try {
    const r = await client.get('/fundamentals/profit/cross_section', {
      params: {
        report_date: selectedQuarter.value,
        metric: selectedMetric.value,
        top: topN.value,
      },
    })
    crossData.value = r.data
  } catch (e: any) {
    errMsg.value = e?.message || '加载季度截面失败'
    crossData.value = null
  } finally {
    crossLoading.value = false
  }
}

watch([selectedQuarter, selectedMetric], () => loadCross())

const isPercentMetric = computed(() => {
  const m = selectedMetric.value
  return m === 'roe_avg' || m === 'np_margin' || m === 'gp_margin'
})

function formatMetricValue(v: number | null) {
  if (v === null || v === undefined) return '-'
  const m = selectedMetric.value
  if (m === 'roe_avg' || m === 'np_margin' || m === 'gp_margin') {
    return `${(v * 100).toFixed(2)}%`
  }
  if (m === 'eps_ttm') return v.toFixed(4)
  // net_profit
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)} 万`
  return v.toLocaleString()
}

const crossCols = computed<DataTableColumns<CrossItem>>(() => [
  { title: '#', key: 'rank', width: 60,
    render: (_r, i) => (i + 1).toString() },
  {
    title: '股票', key: 'symbol', width: 200,
    render: (r) => h('span', {}, [
      r.symbol, ' ', h('span', { style: 'color:#888' }, r.name),
    ]),
  },
  { title: '行业', key: 'industry_l1' },
  {
    title: '数值', key: 'value', width: 140,
    render: (r) => formatMetricValue(r.value),
  },
])

// 行业中位数 / 均值条形图：取 count >= 3 的行业 Top 25（避免单股行业占据榜首）
const industryChartOption = computed(() => {
  const d = crossData.value
  if (!d || !d.by_industry.length) return null
  const filtered = d.by_industry.filter(x => x.count >= 3).slice(0, 25)
  const labels = filtered.map(x => x.industry_l1)
  const medians = filtered.map(x => x.median)
  const means = filtered.map(x => x.mean)
  const valFmt = (v: number) => isPercentMetric.value
    ? `${(v * 100).toFixed(2)}%`
    : (Math.abs(v) >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v.toFixed(2))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any[]) =>
        `${params[0].name}<br/>` +
        params.map(p => `${p.marker} ${p.seriesName}: ${valFmt(p.value)}`).join('<br/>'),
    },
    legend: { textStyle: { color: '#bbb' }, top: 0 },
    grid: { left: 200, right: 30, top: 36, bottom: 30 },
    xAxis: {
      type: 'value',
      axisLabel: {
        color: '#999',
        formatter: (v: number) => isPercentMetric.value
          ? `${(v * 100).toFixed(0)}%`
          : (Math.abs(v) >= 1e8 ? `${(v / 1e8).toFixed(0)}亿` : v.toFixed(2)),
      },
      splitLine: { lineStyle: { color: '#333' } },
    },
    yAxis: {
      type: 'category',
      data: labels.slice().reverse(),
      axisLabel: { color: '#bbb' },
    },
    series: [
      { name: '中位数', type: 'bar', data: medians.slice().reverse(),
        itemStyle: { color: '#F0B90B' } },
      { name: '均值', type: 'bar', data: means.slice().reverse(),
        itemStyle: { color: '#4f8cf7' } },
    ],
  }
})

const industryCols: DataTableColumns<IndustryAgg> = [
  { title: '行业', key: 'industry_l1' },
  { title: '样本数', key: 'count', width: 100 },
  { title: '均值', key: 'mean', width: 140,
    render: (r) => formatMetricValue(r.mean) },
  { title: '中位数', key: 'median', width: 140,
    render: (r) => formatMetricValue(r.median) },
]

// ---------------------------- 生命周期 ----------------------------

async function loadMeta() {
  try {
    const [m, q] = await Promise.all([
      client.get('/fundamentals/metrics'),
      client.get('/fundamentals/profit/quarters'),
    ])
    metrics.value = m.data
    quarters.value = q.data
    if (q.data.length && !selectedQuarter.value) {
      // 默认选最新一个有 ≥ 100 只样本的季度（避免选到刚开始预披露的最新季）
      const richQuarter = q.data.find((qq: QuarterRow) => qq.symbols >= 200) || q.data[0]
      selectedQuarter.value = richQuarter.report_date
    }
  } catch (e: any) {
    errMsg.value = e?.message || '加载元数据失败'
  }
}

onMounted(async () => {
  await loadMeta()
  await loadSeries()
})
</script>

<template>
  <div>
    <n-page-header title="财报数据探查" style="margin-bottom: 16px">
      <template #subtitle>
        baostock profit (PIT) — 单股时间序列 / 季度截面排行
      </template>
    </n-page-header>

    <n-alert v-if="errMsg" type="error" :show-icon="false" style="margin-bottom: 12px">
      {{ errMsg }}
    </n-alert>

    <n-tabs type="line" animated default-value="series">
      <!-- ========== 模式 A：单股时间序列 ========== -->
      <n-tab-pane name="series" tab="单股时间序列">
        <n-card size="small" style="margin-bottom: 12px">
          <n-space :size="12" align="center" wrap>
            <span style="color:#888">股票代码</span>
            <n-input
              v-model:value="symbolInput"
              placeholder="如 600519.SH"
              style="width: 200px"
              @keyup.enter="loadSeries"
            />
            <n-button type="primary" :loading="seriesLoading" @click="loadSeries">
              加载
            </n-button>
            <span v-if="seriesData" style="color:#888">
              {{ seriesData.symbol }} <b style="color:#F0B90B">{{ seriesData.name }}</b>
              · {{ seriesData.industry_l1 }} · {{ seriesData.rows.length }} 期
            </span>
          </n-space>
        </n-card>

        <n-card size="small" title="盈利能力曲线" style="margin-bottom: 12px">
          <n-spin :show="seriesLoading">
            <div v-if="seriesOption" style="height: 420px">
              <VChart :option="seriesOption" autoresize />
            </div>
            <n-empty v-else-if="!seriesLoading" description="尚无该股票的财报数据" />
          </n-spin>
        </n-card>

        <n-card size="small" title="原始记录">
          <n-spin :show="seriesLoading">
            <n-data-table
              v-if="seriesData && seriesData.rows.length"
              :columns="seriesCols"
              :data="seriesData.rows"
              :bordered="false"
              :single-line="false"
              size="small"
              :pagination="{ pageSize: 12 }"
            />
            <n-empty v-else-if="!seriesLoading" description="无数据" />
          </n-spin>
        </n-card>
      </n-tab-pane>

      <!-- ========== 模式 B：季度截面 ========== -->
      <n-tab-pane name="cross" tab="季度截面排行">
        <n-card size="small" style="margin-bottom: 12px">
          <n-space :size="12" align="center" wrap>
            <span style="color:#888">报告期</span>
            <n-select
              v-model:value="selectedQuarter"
              :options="quarterOptions"
              style="width: 240px"
              filterable
              placeholder="加载中..."
            />
            <span style="color:#888">指标</span>
            <n-select
              v-model:value="selectedMetric"
              :options="metricOptions"
              style="width: 220px"
            />
            <span style="color:#888">TopN/BottomN 各</span>
            <n-input-number v-model:value="topN" :min="5" :max="100" style="width: 100px" />
            <n-button :loading="crossLoading" @click="loadCross">刷新</n-button>
            <span v-if="crossData" style="color:#888">
              当季 <b>{{ crossData.total }}</b> 只样本
            </span>
          </n-space>
        </n-card>

        <n-spin :show="crossLoading">
          <n-card size="small" title="行业中位数 / 均值对比" style="margin-bottom: 12px">
            <n-alert type="info" :show-icon="false" style="margin-bottom: 8px">
              仅显示样本数 ≥ 3 的行业（按中位数降序，最多 25 个），过滤极小样本噪声。
            </n-alert>
            <div v-if="industryChartOption" style="height: 600px">
              <VChart :option="industryChartOption" autoresize />
            </div>
            <n-empty v-else-if="!crossLoading" description="无数据" />
          </n-card>

          <n-space :size="12" align="start" style="width: 100%">
            <n-card size="small" :title="crossData ? `Top ${crossData.top.length}（${crossData.metric_label}）` : 'Top'" style="flex:1">
              <n-data-table
                v-if="crossData"
                :columns="crossCols"
                :data="crossData.top"
                :bordered="false"
                :single-line="false"
                size="small"
                :pagination="false"
                max-height="520"
              />
            </n-card>
            <n-card size="small" :title="crossData ? `Bottom ${crossData.bottom.length}` : 'Bottom'" style="flex:1">
              <n-data-table
                v-if="crossData"
                :columns="crossCols"
                :data="crossData.bottom"
                :bordered="false"
                :single-line="false"
                size="small"
                :pagination="false"
                max-height="520"
              />
            </n-card>
          </n-space>

          <n-divider />

          <n-card size="small" title="行业聚合（全部样本）">
            <n-data-table
              v-if="crossData && crossData.by_industry.length"
              :columns="industryCols"
              :data="crossData.by_industry"
              :bordered="false"
              :single-line="false"
              size="small"
              :pagination="{ pageSize: 30 }"
            />
            <n-empty v-else-if="!crossLoading" description="无数据" />
          </n-card>
        </n-spin>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>
