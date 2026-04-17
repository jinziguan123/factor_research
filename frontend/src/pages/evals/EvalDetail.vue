<script setup lang="ts">
/**
 * 评估详情页
 * 自动轮询到完成，展示图表和结构化指标
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NGrid, NGridItem, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NButton, NSpace, NEmpty, NAlert,
} from 'naive-ui'
import { useEval } from '@/api/evals'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import IcSeriesChart from '@/components/charts/IcSeriesChart.vue'
import TurnoverChart from '@/components/charts/TurnoverChart.vue'
import GroupReturnsChart from '@/components/charts/GroupReturnsChart.vue'
import ValueHistogram from '@/components/charts/ValueHistogram.vue'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: evalRun, isLoading } = useEval(runId)

// metrics 表整行嵌在 run["metrics"]，payload 又从 payload_json 解嵌到 metrics.payload
const metrics = computed(() => evalRun.value?.metrics ?? null)
const payload = computed(() => metrics.value?.payload ?? null)

// 后端 SELECT * 直出 params_json 列（JSON 字符串），这里解析一次供展示
const paramsDisplay = computed(() => {
  const raw = evalRun.value?.params_json
  if (!raw) return {}
  try { return JSON.parse(raw) } catch { return raw }
})

const isRunning = computed(() =>
  evalRun.value?.status === 'pending' || evalRun.value?.status === 'running'
)

// 格式化指标值
function fmtNum(v: any, digits = 4): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}
function fmtPct(v: any, digits = 2): string {
  if (v == null) return '-'
  return typeof v === 'number' ? (v * 100).toFixed(digits) + '%' : String(v)
}
</script>

<template>
  <div>
    <n-page-header
      :title="`评估 ${runId.slice(0, 8)}...`"
      @back="router.back()"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <n-space align="center">
          <status-badge v-if="evalRun" :status="evalRun.status" />
          <n-button
            v-if="evalRun?.status === 'success'"
            type="primary"
            secondary
            size="small"
            @click="router.push(`/backtests/new?factor_id=${evalRun.factor_id}&prefill_eval=${runId}`)"
          >
            拿这套参数去回测
          </n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <!-- 运行中进度 -->
      <n-progress
        v-if="isRunning"
        type="line"
        :percentage="evalRun?.status === 'running' ? 50 : 10"
        :show-indicator="false"
        status="warning"
        style="margin-bottom: 16px"
      />

      <!-- 失败提示 -->
      <n-alert v-if="evalRun?.status === 'failed'" type="error" title="运行失败" style="margin-bottom: 16px">
        {{ evalRun.error_message || '未知错误' }}
      </n-alert>

      <!-- 任务基本信息 -->
      <n-descriptions v-if="evalRun" bordered :column="3" label-placement="left" style="margin-bottom: 24px">
        <n-descriptions-item label="因子">{{ evalRun.factor_id }}</n-descriptions-item>
        <n-descriptions-item label="股票池">{{ evalRun.pool_id }}</n-descriptions-item>
        <n-descriptions-item label="日期区间">{{ evalRun.start_date }} ~ {{ evalRun.end_date }}</n-descriptions-item>
        <n-descriptions-item label="创建时间">{{ evalRun.created_at }}</n-descriptions-item>
        <n-descriptions-item label="完成时间">{{ evalRun.finished_at ?? '-' }}</n-descriptions-item>
        <n-descriptions-item label="参数">
          <code style="font-size: 12px">{{ JSON.stringify(paramsDisplay) }}</code>
        </n-descriptions-item>
      </n-descriptions>

      <!-- 图表区域（仅成功时展示） -->
      <template v-if="evalRun?.status === 'success' && payload">
        <n-grid :cols="3" :x-gap="16" :y-gap="16" style="margin-bottom: 24px">
          <!-- IC 累计曲线 -->
          <n-grid-item>
            <n-card title="IC (1d)" size="small">
              <ic-series-chart
                v-if="payload.ic?.['1']"
                :series="payload.ic['1']"
                title="IC (1d)"
              />
              <n-empty v-else description="无数据" />
            </n-card>
          </n-grid-item>

          <!-- Rank IC -->
          <n-grid-item>
            <n-card title="Rank IC (1d)" size="small">
              <ic-series-chart
                v-if="payload.rank_ic?.['1']"
                :series="payload.rank_ic['1']"
                title="Rank IC (1d)"
              />
              <n-empty v-else description="无数据" />
            </n-card>
          </n-grid-item>

          <!-- 换手率 -->
          <n-grid-item>
            <n-card title="换手率" size="small">
              <turnover-chart
                v-if="payload.turnover_series"
                :series="payload.turnover_series"
              />
              <n-empty v-else description="无数据" />
            </n-card>
          </n-grid-item>

          <!-- 分组累计净值 -->
          <n-grid-item>
            <n-card title="分组累计净值" size="small">
              <group-returns-chart
                v-if="payload.group_returns"
                :data="payload.group_returns"
              />
              <n-empty v-else description="无数据" />
            </n-card>
          </n-grid-item>

          <!-- 多空净值 -->
          <n-grid-item>
            <n-card title="多空净值" size="small">
              <ic-series-chart
                v-if="payload.long_short_equity"
                :series="payload.long_short_equity"
                title="多空净值"
              />
              <n-empty v-else description="无数据" />
            </n-card>
          </n-grid-item>

          <!-- 因子值分布 -->
          <n-grid-item>
            <n-card title="因子值分布" size="small">
              <value-histogram
                v-if="payload.value_hist"
                :data="payload.value_hist"
              />
              <n-empty v-else description="无数据" />
            </n-card>
          </n-grid-item>
        </n-grid>
      </template>

      <!-- 结构化指标：独立 v-if（metrics 在 payload 缺失时仍可展示） -->
      <template v-if="evalRun?.status === 'success' && metrics">
        <h3 style="margin-bottom: 12px">评估指标</h3>
        <n-descriptions bordered :column="3" label-placement="left">
          <n-descriptions-item label="IC 均值">{{ fmtNum(metrics.ic_mean) }}</n-descriptions-item>
          <n-descriptions-item label="IC IR">{{ fmtNum(metrics.ic_ir) }}</n-descriptions-item>
          <n-descriptions-item label="IC 胜率">{{ fmtPct(metrics.ic_win_rate) }}</n-descriptions-item>
          <n-descriptions-item label="Rank IC 均值">{{ fmtNum(metrics.rank_ic_mean) }}</n-descriptions-item>
          <n-descriptions-item label="多空 Sharpe">{{ fmtNum(metrics.long_short_sharpe, 2) }}</n-descriptions-item>
          <n-descriptions-item label="多空年化收益">{{ fmtPct(metrics.long_short_annret) }}</n-descriptions-item>
          <n-descriptions-item label="平均换手率">{{ fmtPct(metrics.turnover_mean) }}</n-descriptions-item>
        </n-descriptions>
      </template>
    </n-spin>
  </div>
</template>
