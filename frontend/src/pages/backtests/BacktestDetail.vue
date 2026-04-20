<script setup lang="ts">
/**
 * 回测详情页
 * 运行中轮询状态；成功后在线展示净值曲线 + 分页交易列表（不再只能下载 parquet）。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NButton, NSpace, NAlert, NEmpty, NDataTable, NTag,
  NInput, NDatePicker, NGrid, NGridItem, NFormItem,
  type DataTableColumns,
} from 'naive-ui'
import { useBacktest, useEquitySeries, useTradesPage, type TradesFilter } from '@/api/backtests'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'

// VectorBT `records_readable` 的英文列名 → 中文表头。parquet 列名稳定不走版本号，
// 我们只翻知道的；未识别的列回退展示原列名，避免因 vectorbt 小版本改列就"漏渲染"。
const TRADE_COLUMN_LABELS: Record<string, string> = {
  'Exit Trade Id': '编号',
  'Trade Id': '编号',
  'Column': '股票代码',
  'Size': '数量',
  'Entry Timestamp': '开仓时间',
  'Avg Entry Price': '开仓均价',
  'Entry Price': '开仓均价',
  'Entry Fees': '开仓手续费',
  'Exit Timestamp': '平仓时间',
  'Avg Exit Price': '平仓均价',
  'Exit Price': '平仓均价',
  'Exit Fees': '平仓手续费',
  'PnL': '损益',
  'Return': '收益率',
  'Direction': '方向',
  'Status': '状态',
  'Position Id': '持仓编号',
}

// Direction / Status 是 VectorBT 内置枚举字符串，渲染时翻译成中文；
// 其他值（数值 / 字符串）不动。
const TRADE_VALUE_LABELS: Record<string, Record<string, string>> = {
  Direction: { Long: '多', Short: '空' },
  Status: { Open: '持仓中', Closed: '已平仓' },
}

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: btRun, isLoading } = useBacktest(runId)

// 池名映射：详情页把 pool_id 渲染成池名；查不到（软删 / 列表未载入）保留 #<id>。
const { lookup: lookupPoolName } = usePoolNameMap()

const metrics = computed(() => btRun.value?.metrics ?? null)
const artifacts = computed(() => (btRun.value as any)?.artifacts ?? [])

const isRunning = computed(() =>
  btRun.value?.status === 'pending' || btRun.value?.status === 'running'
)
const isSuccess = computed(() => btRun.value?.status === 'success')

// 只有 success 才拉产物；pending / running / failed 不请求，避免 4xx 噪音
const { data: equityData, isLoading: equityLoading, isError: equityError } =
  useEquitySeries(runId, isSuccess)

const tradesPage = ref(1)
const tradesSize = ref(20)

// --- 筛选 state ---
// symbolInput 是"用户正在输入的值"；tradesFilter 才是"已提交的筛选"，避免每键一下都重查。
// 日期选择器双向绑定的是 naive-ui 的 daterange [startTs, endTs]（毫秒 ts）或 null。
const symbolInput = ref('')
const dateRange = ref<[number, number] | null>(null)
const tradesFilter = ref<TradesFilter>({})

const { data: tradesData, isLoading: tradesLoading, isError: tradesError, error: tradesErrObj } = useTradesPage(
  runId,
  tradesPage,
  tradesSize,
  isSuccess,
  tradesFilter,
)

function tsToYmd(ts: number | null | undefined): string | null {
  if (ts == null) return null
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function applyTradesFilter() {
  tradesPage.value = 1  // 换筛选条件必须回到第 1 页，否则容易停在"超出结果范围"的页号上
  tradesFilter.value = {
    symbol: symbolInput.value.trim() || undefined,
    startDate: tsToYmd(dateRange.value?.[0] ?? null) ?? null,
    endDate: tsToYmd(dateRange.value?.[1] ?? null) ?? null,
  }
}

function resetTradesFilter() {
  symbolInput.value = ''
  dateRange.value = null
  tradesPage.value = 1
  tradesFilter.value = {}
}

// 未识别的英文列也要有合理回退：直接用原列名，保证 vectorbt 改动不会让表头空白。
function columnTitle(c: string): string {
  return TRADE_COLUMN_LABELS[c] ?? c
}

// Return 是小数收益率（0.0123 = 1.23%），单独按 % 渲染；别的数值按 4 位小数。
function renderTradeValue(col: string, v: any): string {
  if (v == null || v === '') return '-'
  const map = TRADE_VALUE_LABELS[col]
  if (map && typeof v === 'string' && v in map) return map[v]
  if (typeof v === 'number') {
    if (col === 'Return') return (v * 100).toFixed(2) + '%'
    return Number.isInteger(v) ? String(v) : v.toFixed(4)
  }
  return String(v)
}

const tradesColumns = computed<DataTableColumns<Record<string, any>>>(() => {
  const cols = tradesData.value?.columns ?? []
  return cols.map(c => ({
    title: columnTitle(c),
    key: c,
    ellipsis: { tooltip: true },
    render: (row) => renderTradeValue(c, row[c]),
  }))
})

// 筛选命中 0 条或后端 400 时，n-data-table 的默认 empty 不够清楚；我们自己贴一层提示。
const tradesFilterErrorMsg = computed<string | null>(() => {
  if (!tradesError.value) return null
  const e: any = tradesErrObj.value
  return e?.response?.data?.message ?? e?.response?.data?.detail ?? e?.message ?? '筛选失败'
})

const tradesPagination = computed(() => ({
  page: tradesPage.value,
  pageSize: tradesSize.value,
  itemCount: tradesData.value?.total ?? 0,
  showSizePicker: true,
  pageSizes: [20, 50, 100],
  prefix: (info: { itemCount?: number }) => `共 ${info.itemCount ?? 0} 条`,
}))

function onPageChange(p: number) { tradesPage.value = p }
function onPageSizeChange(s: number) {
  tradesSize.value = s
  tradesPage.value = 1
}

function fmtPct(v: any, digits = 2): string {
  if (v == null) return '-'
  return typeof v === 'number' ? (v * 100).toFixed(digits) + '%' : String(v)
}
function fmtNum(v: any, digits = 4): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}

function hasArtifact(type: string): boolean {
  return artifacts.value.some((a: any) => a.artifact_type === type)
}

function downloadArtifact(type: string) {
  window.open(`/api/backtests/${runId.value}/${type}`, '_blank')
}
</script>

<template>
  <div>
    <n-page-header
      :title="`回测 ${runId.slice(0, 8)}...`"
      @back="router.back()"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <status-badge v-if="btRun" :status="btRun.status" />
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <!-- 运行中进度 -->
      <n-progress
        v-if="isRunning"
        type="line"
        :percentage="btRun?.status === 'running' ? 50 : 10"
        :show-indicator="false"
        status="warning"
        style="margin-bottom: 16px"
      />

      <!-- 失败提示 -->
      <n-alert v-if="btRun?.status === 'failed'" type="error" title="运行失败" style="margin-bottom: 16px">
        {{ btRun.error || '未知错误' }}
      </n-alert>

      <!-- 任务基本信息 -->
      <n-descriptions v-if="btRun" bordered :column="3" label-placement="left" style="margin-bottom: 24px">
        <n-descriptions-item label="因子">{{ btRun.factor_id }}</n-descriptions-item>
        <n-descriptions-item label="股票池">
          {{ lookupPoolName(btRun.pool_id) }}
          <span style="color: #848E9C; font-size: 12px; margin-left: 4px">#{{ btRun.pool_id }}</span>
        </n-descriptions-item>
        <n-descriptions-item label="日期区间">{{ btRun.start_date }} ~ {{ btRun.end_date }}</n-descriptions-item>
        <n-descriptions-item label="创建时间">{{ btRun.created_at }}</n-descriptions-item>
        <n-descriptions-item label="完成时间">{{ btRun.finished_at ?? '-' }}</n-descriptions-item>
        <n-descriptions-item label="参数">
          <code style="font-size: 12px">{{ JSON.stringify(btRun.params) }}</code>
        </n-descriptions-item>
      </n-descriptions>

      <!-- 成功时展示 -->
      <template v-if="isSuccess">
        <!-- 净值曲线 -->
        <n-card size="small" style="margin-bottom: 16px">
          <template #header>
            <n-space align="center" :size="8">
              <span>净值曲线</span>
              <n-tag v-if="equityData?.sampled" size="small" type="info" :bordered="false">
                已降采样（原 {{ equityData.total }} 点）
              </n-tag>
            </n-space>
          </template>
          <n-spin :show="equityLoading">
            <div v-if="equityError" style="padding: 40px 0">
              <n-empty description="净值数据读取失败，可尝试下载 equity.parquet 查看">
                <template #extra>
                  <n-button
                    v-if="hasArtifact('equity')"
                    size="small"
                    @click="downloadArtifact('equity')"
                  >
                    下载 equity.parquet
                  </n-button>
                </template>
              </n-empty>
            </div>
            <equity-curve-chart
              v-else-if="equityData && equityData.dates.length > 0"
              :equity="{ dates: equityData.dates, values: equityData.values }"
            />
            <n-empty v-else-if="!equityLoading" description="暂无净值数据" style="padding: 40px 0" />
          </n-spin>
        </n-card>

        <!-- Metrics 指标 -->
        <h3 style="margin-bottom: 12px">回测指标</h3>
        <n-descriptions v-if="metrics" bordered :column="3" label-placement="left" style="margin-bottom: 24px">
          <n-descriptions-item label="总收益率">{{ fmtPct(metrics.total_return) }}</n-descriptions-item>
          <n-descriptions-item label="年化收益率">{{ fmtPct(metrics.annual_return) }}</n-descriptions-item>
          <n-descriptions-item label="Sharpe 比率">{{ fmtNum(metrics.sharpe_ratio, 2) }}</n-descriptions-item>
          <n-descriptions-item label="最大回撤">{{ fmtPct(metrics.max_drawdown) }}</n-descriptions-item>
          <n-descriptions-item label="胜率">{{ fmtPct(metrics.win_rate) }}</n-descriptions-item>
          <n-descriptions-item label="交易次数">{{ metrics.trade_count ?? '-' }}</n-descriptions-item>
        </n-descriptions>
        <n-alert v-else type="info" style="margin-bottom: 24px">
          暂无指标数据
        </n-alert>

        <!-- 交易列表 -->
        <n-card title="交易记录" size="small" style="margin-bottom: 16px">
          <!-- 筛选条：股票代码子串 + 开仓时间范围。符合后端"按 Entry Timestamp 过滤"的语义。 -->
          <n-grid :cols="24" :x-gap="12" :y-gap="8" style="margin-bottom: 12px" responsive="screen">
            <n-grid-item :span="8">
              <n-form-item label="股票代码" label-placement="left" :show-feedback="false">
                <n-input
                  v-model:value="symbolInput"
                  placeholder="代码或任意片段，如 000001"
                  clearable
                  @keydown.enter="applyTradesFilter"
                />
              </n-form-item>
            </n-grid-item>
            <n-grid-item :span="10">
              <n-form-item label="开仓日期" label-placement="left" :show-feedback="false">
                <n-date-picker
                  v-model:value="dateRange"
                  type="daterange"
                  clearable
                  style="width: 100%"
                  :actions="['clear', 'confirm']"
                />
              </n-form-item>
            </n-grid-item>
            <n-grid-item :span="6">
              <n-space style="margin-top: 4px">
                <n-button type="primary" size="small" @click="applyTradesFilter">查询</n-button>
                <n-button size="small" @click="resetTradesFilter">重置</n-button>
              </n-space>
            </n-grid-item>
          </n-grid>

          <n-alert v-if="tradesFilterErrorMsg" type="warning" :show-icon="false" style="margin-bottom: 8px">
            {{ tradesFilterErrorMsg }}
          </n-alert>

          <n-data-table
            remote
            :columns="tradesColumns"
            :data="tradesData?.rows ?? []"
            :pagination="tradesPagination"
            :loading="tradesLoading"
            :bordered="false"
            size="small"
            :scroll-x="1200"
            @update:page="onPageChange"
            @update:page-size="onPageSizeChange"
          />
        </n-card>

        <!-- 产物下载：保留，方便用户离线做深度分析 -->
        <h3 style="margin-bottom: 12px">产物下载</h3>
        <n-space>
          <n-button
            v-if="hasArtifact('equity')"
            @click="downloadArtifact('equity')"
            secondary
          >
            equity.parquet
          </n-button>
          <n-button
            v-if="hasArtifact('orders')"
            @click="downloadArtifact('orders')"
            secondary
          >
            orders.parquet
          </n-button>
          <n-button
            v-if="hasArtifact('trades')"
            @click="downloadArtifact('trades')"
            secondary
          >
            trades.parquet
          </n-button>
        </n-space>
      </template>
    </n-spin>
  </div>
</template>
