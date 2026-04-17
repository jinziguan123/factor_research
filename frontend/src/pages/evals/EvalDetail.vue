<script setup lang="ts">
/**
 * 评估详情页
 * 自动轮询到完成，展示图表和结构化指标
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NGrid, NGridItem, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NButton, NSpace, NEmpty, NAlert,
} from 'naive-ui'
import { useEval } from '@/api/evals'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import ChartCard from '@/components/charts/ChartCard.vue'
import IcSeriesChart from '@/components/charts/IcSeriesChart.vue'
import TurnoverChart from '@/components/charts/TurnoverChart.vue'
import GroupReturnsChart from '@/components/charts/GroupReturnsChart.vue'
import ValueHistogram from '@/components/charts/ValueHistogram.vue'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: evalRun, isLoading } = useEval(runId)

// 池名映射：详情页把 pool_id 渲染成池名，查不到退化成 #<id>（软删池）。
const { lookup: lookupPoolName } = usePoolNameMap()

// metrics 表整行嵌在 run["metrics"]，payload 又从 payload_json 解嵌到 metrics.payload
const metrics = computed(() => evalRun.value?.metrics ?? null)
const payload = computed(() => metrics.value?.payload ?? null)

// forward_periods 由后端计算决定（默认含 1/5/10），这里取第一个可用周期做默认展示，
// 避免因子配置里没有 "1" 时 IC / Rank IC 图表直接显示"无数据"。
function firstKey(obj: any): string | null {
  if (!obj || typeof obj !== 'object') return null
  const keys = Object.keys(obj)
  return keys.length ? keys[0] : null
}
const icPeriod = computed(() => firstKey(payload.value?.ic))
const rankIcPeriod = computed(() => firstKey(payload.value?.rank_ic))
const icSeries = computed(() => icPeriod.value ? payload.value?.ic?.[icPeriod.value] : null)
const rankIcSeries = computed(() => rankIcPeriod.value ? payload.value?.rank_ic?.[rankIcPeriod.value] : null)

// 横截面指标全空但因子值直方图有数据 → 典型的"池太小 / 有效样本不足"场景。
// 旧评估（修 eval_service 校验之前的 run）会落在这里；新评估会直接 failed 不进详情页。
const crossSectionalEmpty = computed(() => {
  const p = payload.value
  if (!p) return false
  const hasHist = (p.value_hist?.counts?.length ?? 0) > 0
  const tsEmpty = (p.turnover_series?.dates?.length ?? 0) === 0
  const grEmpty = (p.group_returns?.dates?.length ?? 0) === 0
  return hasHist && tsEmpty && grEmpty
})

// 多空有效样本数不足告警：rank 类因子 + 少量 bucket + qcut 退化会让 top 或 bot
// 组频繁缺失，long_short_series 过滤 NaN 后只剩几十天，此时 Sharpe / 年化都是
// 被少数极端日主导的噪声，不能用来评判因子好坏。阈值 30 = 1.5 月交易日，粗略线。
const LS_SAMPLE_WARN_THRESHOLD = 30
const longShortNEffective = computed<number | null>(() => {
  const n = payload.value?.long_short_n_effective
  return typeof n === 'number' ? n : null
})
const longShortSampleInsufficient = computed(() => {
  const n = longShortNEffective.value
  return n !== null && n > 0 && n < LS_SAMPLE_WARN_THRESHOLD
})

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
        <n-descriptions-item label="股票池">
          {{ lookupPoolName(evalRun.pool_id) }}
          <span style="color: #848E9C; font-size: 12px; margin-left: 4px">#{{ evalRun.pool_id }}</span>
        </n-descriptions-item>
        <n-descriptions-item label="日期区间">{{ evalRun.start_date }} ~ {{ evalRun.end_date }}</n-descriptions-item>
        <n-descriptions-item label="创建时间">{{ evalRun.created_at }}</n-descriptions-item>
        <n-descriptions-item label="完成时间">{{ evalRun.finished_at ?? '-' }}</n-descriptions-item>
        <n-descriptions-item label="参数">
          <code style="font-size: 12px">{{ JSON.stringify(paramsDisplay) }}</code>
        </n-descriptions-item>
      </n-descriptions>

      <!-- 横截面指标全空：池太小 / 有效样本不足，提示用户换池 -->
      <n-alert
        v-if="evalRun?.status === 'success' && crossSectionalEmpty"
        type="warning"
        title="横截面指标无法计算"
        style="margin-bottom: 16px"
      >
        本次评估只能算出因子值分布，IC / Rank IC / 分组 / 换手率等指标全为空。
        常见原因：股票池中股票数过少（横截面 IC 至少需要每日 3 只，分组/换手需要 ≥ n_groups）、
        或因子在该窗口内有效样本过少。建议换一个包含更多股票的池后重新评估。
      </n-alert>

      <!-- 图表区域（仅成功时展示） -->
      <template v-if="evalRun?.status === 'success' && payload">
        <n-grid :cols="3" :x-gap="16" :y-gap="16" style="margin-bottom: 24px">
          <!-- IC 累计曲线（自动选第一个可用 forward_period） -->
          <n-grid-item>
            <chart-card :title="`IC (${icPeriod ?? '-'}d)`">
              <ic-series-chart
                v-if="icSeries"
                :series="icSeries"
                :title="`IC (${icPeriod}d)`"
              />
              <n-empty v-else description="无数据" />
            </chart-card>
          </n-grid-item>

          <!-- Rank IC -->
          <n-grid-item>
            <chart-card :title="`Rank IC (${rankIcPeriod ?? '-'}d)`">
              <ic-series-chart
                v-if="rankIcSeries"
                :series="rankIcSeries"
                :title="`Rank IC (${rankIcPeriod}d)`"
              />
              <n-empty v-else description="无数据" />
            </chart-card>
          </n-grid-item>

          <!-- 换手率 -->
          <n-grid-item>
            <chart-card title="换手率">
              <turnover-chart
                v-if="payload.turnover_series"
                :series="payload.turnover_series"
              />
              <n-empty v-else description="无数据" />
            </chart-card>
          </n-grid-item>

          <!-- 分组累计净值 -->
          <n-grid-item>
            <chart-card title="分组累计净值">
              <group-returns-chart
                v-if="payload.group_returns"
                :data="payload.group_returns"
              />
              <n-empty v-else description="无数据" />
            </chart-card>
          </n-grid-item>

          <!-- 多空净值：用专门的净值曲线组件（带基准 y=1 和回撤面积）。
               历史 bug 注记：之前复用 IcSeriesChart，会对已累计的净值再做一次
               cumsum，导致 y 轴飙到 10+ 且 legend 硬编码显示"每日IC/累计IC"。 -->
          <n-grid-item>
            <chart-card title="多空净值">
              <equity-curve-chart
                v-if="payload.long_short_equity?.dates?.length"
                :equity="payload.long_short_equity"
              />
              <n-empty v-else description="无数据" />
            </chart-card>
          </n-grid-item>

          <!-- 因子值分布 -->
          <n-grid-item>
            <chart-card title="因子值分布">
              <value-histogram
                v-if="payload.value_hist"
                :data="payload.value_hist"
              />
              <n-empty v-else description="无数据" />
            </chart-card>
          </n-grid-item>
        </n-grid>
      </template>

      <!-- 结构化指标：独立 v-if（metrics 在 payload 缺失时仍可展示） -->
      <template v-if="evalRun?.status === 'success' && metrics">
        <h3 style="margin-bottom: 12px">评估指标</h3>
        <!-- 多空有效样本 < 30 警示：rank 类因子 + qcut 退化的典型症状。
             Sharpe / 年化在这种情况下会被 1-2 个极端日推成 ±6 / ±160%，不可信。
             用 warning 而非 error：指标仍值得展示，但需告诉用户慎重解读。 -->
        <n-alert
          v-if="longShortSampleInsufficient"
          type="warning"
          title="多空组合有效样本数过少"
          style="margin-bottom: 12px"
        >
          本次评估多空组合实际仅有 <b>{{ longShortNEffective }}</b> 个交易日可算（&lt; {{ LS_SAMPLE_WARN_THRESHOLD }} 天）。
          下方的多空 Sharpe / 年化收益会被少数极端日主导，统计意义非常有限。
          常见原因：因子值只有少量离散值（如 rank 类 / argmax 类），
          qcut 分 5 组时大量 tied → top 或 bot 组频繁缺失。
          建议换因子、减小 n_groups（如 3 组）、或用更大的股票池提高横截面分散度。
        </n-alert>
        <n-descriptions bordered :column="3" label-placement="left">
          <n-descriptions-item label="IC 均值">{{ fmtNum(metrics.ic_mean) }}</n-descriptions-item>
          <n-descriptions-item label="IC IR">{{ fmtNum(metrics.ic_ir) }}</n-descriptions-item>
          <n-descriptions-item label="IC 胜率">{{ fmtPct(metrics.ic_win_rate) }}</n-descriptions-item>
          <n-descriptions-item label="Rank IC 均值">{{ fmtNum(metrics.rank_ic_mean) }}</n-descriptions-item>
          <n-descriptions-item label="多空 Sharpe">{{ fmtNum(metrics.long_short_sharpe, 2) }}</n-descriptions-item>
          <n-descriptions-item label="多空年化收益">{{ fmtPct(metrics.long_short_annret) }}</n-descriptions-item>
          <n-descriptions-item label="平均换手率">{{ fmtPct(metrics.turnover_mean) }}</n-descriptions-item>
          <n-descriptions-item v-if="longShortNEffective !== null" label="多空有效样本数">
            {{ longShortNEffective }} 天
          </n-descriptions-item>
        </n-descriptions>
      </template>
    </n-spin>
  </div>
</template>
