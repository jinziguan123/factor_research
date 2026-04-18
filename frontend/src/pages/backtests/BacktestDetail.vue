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
  type DataTableColumns,
} from 'naive-ui'
import { useBacktest, useEquitySeries, useTradesPage } from '@/api/backtests'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'

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
const { data: tradesData, isLoading: tradesLoading } = useTradesPage(
  runId,
  tradesPage,
  tradesSize,
  isSuccess,
)

// trades 列表表头直接按后端返回的 columns 动态渲染——VectorBT 改列名时前端不用同步。
// NaN / datetime 已在后端标准化为 string / number / null。
const tradesColumns = computed<DataTableColumns<Record<string, any>>>(() => {
  const cols = tradesData.value?.columns ?? []
  return cols.map(c => ({
    title: c,
    key: c,
    ellipsis: { tooltip: true },
    render: (row) => {
      const v = row[c]
      if (v == null) return '-'
      if (typeof v === 'number') {
        // 整数（trade id / status 码）直接展示；小数保留 4 位
        return Number.isInteger(v) ? String(v) : v.toFixed(4)
      }
      return String(v)
    },
  }))
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
