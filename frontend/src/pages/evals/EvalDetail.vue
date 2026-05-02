<script setup lang="ts">
/**
 * 评估详情页
 * 自动轮询到完成，展示图表和结构化指标
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NGrid, NGridItem, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NButton, NSpace, NEmpty, NAlert, NCard, NTag,
  NTable, NModal, NInput, NSelect, NFormItem, NTabs, NTabPane, useMessage,
} from 'naive-ui'
import { useEval } from '@/api/evals'
import { useFactorLineage } from '@/api/factors'
import { useNegateFactor, useEvolveFactor } from '@/api/factor_assistant'
import { usePoolNameMap, usePools } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import ChartCard from '@/components/charts/ChartCard.vue'
import IcSeriesChart from '@/components/charts/IcSeriesChart.vue'
import TurnoverChart from '@/components/charts/TurnoverChart.vue'
import GroupReturnsChart from '@/components/charts/GroupReturnsChart.vue'
import ValueHistogram from '@/components/charts/ValueHistogram.vue'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'
import IcDecayChart from '@/components/charts/IcDecayChart.vue'
import RankAutocorrChart from '@/components/charts/RankAutocorrChart.vue'

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: evalRun, isLoading } = useEval(runId)
const message = useMessage()
const activeTab = ref<'cross_section' | 'time_series'>('cross_section')

// 池名映射：详情页把 pool_id 渲染成池名，查不到退化成 #<id>（软删池）。
const { lookup: lookupPoolName } = usePoolNameMap()

// L2.A：诊断里出现"取负号"建议时，给一键反向按钮
const negateMut = useNegateFactor()
const showNegateAction = computed(() => {
  const fb = evalRun.value?.feedback_text
  return !!fb && (fb.includes('取负号') || fb.includes('试将因子取负'))
})

// L2.D：因子进化 dialog
const evolveOpen = ref(false)
const evolveExtraHint = ref('')
const evolveAutoEvalPoolId = ref<number | null>(null)
const { data: poolsData } = usePools()
const evolvePoolOptions = computed(() =>
  (poolsData.value ?? []).map((p: any) => ({ label: p.pool_name, value: p.pool_id })),
)
const evolveMut = useEvolveFactor()

// 拉同链 SOTA 信息——用户在非 SOTA 因子的评估页点🧬时给出建议
const evolveFactorIdRef = computed(() => evalRun.value?.factor_id ?? '')
const { data: evolveLineage } = useFactorLineage(evolveFactorIdRef)
const sotaSuggestion = computed(() => {
  const ln = evolveLineage.value
  if (!ln || !ln.same_root_sota) return null
  if (ln.same_root_sota === ln.factor_id) return null  // 当前已是 SOTA
  return ln.same_root_sota
})

function openEvolveDialog() {
  // 预选当前评估池作为 default 评估池
  evolveAutoEvalPoolId.value = evalRun.value?.pool_id ?? null
  evolveExtraHint.value = ''
  evolveOpen.value = true
}

async function handleEvolveSubmit() {
  if (!evalRun.value) return
  try {
    const res = await evolveMut.mutateAsync({
      parent_factor_id: evalRun.value.factor_id,
      parent_eval_run_id: runId.value,
      extra_hint: evolveExtraHint.value.trim() || null,
      auto_eval_pool_id: evolveAutoEvalPoolId.value,
    })
    message.success(`已生成 v${res.generation}：${res.factor_id}`)
    evolveOpen.value = false
    if (res.auto_eval_run_id) {
      router.push(`/evals/${res.auto_eval_run_id}`)
    } else {
      router.push(`/factors/${res.factor_id}`)
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '进化失败')
  }
}

async function handleNegateFactor() {
  if (!evalRun.value) return
  try {
    const res = await negateMut.mutateAsync({
      factor_id: evalRun.value.factor_id,
      auto_eval_pool_id: evalRun.value.pool_id,  // 沿用当前评估池跑反向版
    })
    message.success(`已生成反向因子 ${res.factor_id}，跳转查看`)
    if (res.auto_eval_run_id) {
      router.push(`/evals/${res.auto_eval_run_id}`)
    } else {
      router.push(`/factors/${res.factor_id}`)
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '反向因子生成失败')
  }
}

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

// 个股时序评估数据
const tsData = computed(() => payload.value?.time_series ?? null)
const tsSummary = computed(() => tsData.value?.summary ?? null)
const tsTopN = computed(() => (tsData.value?.top_n?.data ?? []) as any[])
const tsBottomN = computed(() => (tsData.value?.bottom_n?.data ?? []) as any[])
function tsRowSymbol(row: any): string { return row._index ?? row.symbol ?? '-' }

// 因子体检卡片数据：后端 _build_health 产出的 {overall, items[]}。
// 老的 run 没这段，payload.health 会是 undefined → 直接不渲染卡片。
interface HealthItem {
  key: string
  label: string
  value: number
  display: string
  level: 'green' | 'yellow' | 'red'
  message: string
}
interface HealthPayload {
  overall: 'green' | 'yellow' | 'red'
  items: HealthItem[]
}
const health = computed<HealthPayload | null>(() => {
  const h = payload.value?.health
  if (!h || !Array.isArray(h.items)) return null
  return h as HealthPayload
})

// Naive UI NTag 的 type 映射：green/yellow/red → success/warning/error
function levelTagType(level: string): 'success' | 'warning' | 'error' | 'default' {
  if (level === 'green') return 'success'
  if (level === 'yellow') return 'warning'
  if (level === 'red') return 'error'
  return 'default'
}
function overallBadge(level: string): { type: 'success' | 'warning' | 'error'; text: string } {
  if (level === 'red') return { type: 'error', text: '存在严重风险' }
  if (level === 'yellow') return { type: 'warning', text: '有可疑信号' }
  return { type: 'success', text: '整体健康' }
}

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

// ---------------------- 样本内 / 样本外（train/test）对比 ----------------------
// payload 里 split_date 字段存在才渲染，老评估记录继续无此对比卡。
interface IcSummary {
  ic_mean?: number | null
  ic_std?: number | null
  ic_ir?: number | null
  ic_win_rate?: number | null
  ic_t_stat?: number | null
}
const hasSplit = computed(() => !!payload.value?.split_date)
const icSummaryTrain = computed<IcSummary>(() => payload.value?.ic_summary_train ?? {})
const icSummaryTest = computed<IcSummary>(() => payload.value?.ic_summary_test ?? {})
const rankIcSummaryTrain = computed<IcSummary>(() => payload.value?.rank_ic_summary_train ?? {})
const rankIcSummaryTest = computed<IcSummary>(() => payload.value?.rank_ic_summary_test ?? {})

// 判断 train/test 两段 IC 是否"显著分歧"：符号翻转 或 幅度差 > 50%。
// 触发时在对比卡右上角出红色标签提醒用户"样本外失效"。
function icDiverged(train?: number | null, test?: number | null): boolean {
  if (train == null || test == null) return false
  if (Math.abs(train) < 1e-6 || Math.abs(test) < 1e-6) return false
  if (Math.sign(train) !== Math.sign(test)) return true
  const ratio = Math.abs(test) / Math.abs(train)
  return ratio < 0.5 || ratio > 2.0
}
const icMeanDiverged = computed(() =>
  icDiverged(icSummaryTrain.value.ic_mean, icSummaryTest.value.ic_mean)
)
const rankIcMeanDiverged = computed(() =>
  icDiverged(rankIcSummaryTrain.value.ic_mean, rankIcSummaryTest.value.ic_mean)
)
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

      <!-- L1.2 借鉴 RD-Agent 反馈三元组：service 写的诊断 + 改进建议（success/failed 都可能写） -->
      <n-alert
        v-if="evalRun?.feedback_text"
        type="default"
        title="📋 评估诊断"
        :show-icon="false"
        style="margin-bottom: 16px; line-height: 1.7"
      >
        <div style="white-space: pre-wrap">{{ evalRun.feedback_text }}</div>

        <!-- L2.A：诊断里建议"取负号"时给一键反向按钮 -->
        <div v-if="showNegateAction" style="margin-top: 12px">
          <n-button
            type="primary"
            size="small"
            :loading="negateMut.isPending.value"
            @click="handleNegateFactor"
          >
            🔄 一键生成反向因子（{{ evalRun!.factor_id }}_neg）并自动评估
          </n-button>
          <span style="color: #848E9C; font-size: 12px; margin-left: 8px">
            AST 改写、不调 LLM；新因子在同一池子立即跑 60 天 IC
          </span>
        </div>

        <!-- L2.D：进化下一代按钮（success 状态总展示） -->
        <div
          v-if="evalRun?.status === 'success'"
          style="margin-top: 12px; padding-top: 12px; border-top: 1px dashed #DDD"
        >
          <n-button
            type="info"
            size="small"
            @click="openEvolveDialog"
          >
            🧬 基于本次反馈进化下一代
          </n-button>
          <span style="color: #848E9C; font-size: 12px; margin-left: 8px">
            LLM 根据指标 + 诊断 + 你的额外指令生成下一代
          </span>
        </div>
      </n-alert>

      <!-- L2.D 进化对话框 -->
      <n-modal
        v-model:show="evolveOpen"
        preset="dialog"
        title="🧬 因子进化下一代"
        positive-text="生成"
        negative-text="取消"
        style="width: 600px"
        :positive-button-props="{ loading: evolveMut.isPending.value }"
        @positive-click="handleEvolveSubmit"
      >
        <!-- SOTA 建议（仅当本链有 SOTA 且当前不是 SOTA 时显示） -->
        <n-alert
          v-if="sotaSuggestion"
          type="warning"
          :show-icon="false"
          style="margin-bottom: 12px"
        >
          💡 本链 SOTA 是
          <a
            style="cursor: pointer; color: #5AC8FA; font-weight: 600"
            @click="router.push(`/factors/${sotaSuggestion}`)"
          >⭐ {{ sotaSuggestion }}</a>。
          按 RD-Agent 思想，进化通常应**从 SOTA 出发**继承当前最优；
          建议先去 ⭐ 因子的评估页再点 🧬。仍要从 <code>{{ evalRun?.factor_id }}</code>
          进化也可以——下面继续生成即可（会形成分叉）。
        </n-alert>

        <n-form-item label="父代评估">
          <code>{{ evalRun?.factor_id }}</code>
          <span style="color: #848E9C; font-size: 12px; margin-left: 8px">
            run {{ runId.slice(0, 8) }}
          </span>
        </n-form-item>
        <n-form-item label="额外指令（可选）">
          <n-input
            v-model:value="evolveExtraHint"
            type="textarea"
            placeholder="例：想要更短窗口 / 加 EMA 平滑 / 在熊市段更稳"
            :autosize="{ minRows: 2, maxRows: 4 }"
            maxlength="500"
            show-count
          />
        </n-form-item>
        <n-form-item label="自动评估池（可选）">
          <n-select
            v-model:value="evolveAutoEvalPoolId"
            :options="evolvePoolOptions"
            placeholder="留空跳过自动评估"
            clearable
            filterable
            style="width: 320px"
          />
        </n-form-item>
        <n-alert type="info" size="small" :show-icon="false" style="margin-top: 8px">
          LLM 会拿到（父代假设 + 评估指标 + 诊断 + 你的指令）来生成下一代。
          factor_id 自动 = <code>&lt;root&gt;_evo&lt;N&gt;</code>，由后端按同链最大代号 + 1 计算。
        </n-alert>
      </n-modal>

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

      <!-- Tab 切换：横截面评估 / 个股时序 -->
      <n-tabs v-if="evalRun?.status === 'success'" v-model:value="activeTab" type="segment" style="margin-bottom: 16px">
        <n-tab-pane name="cross_section" tab="横截面评估">
      <!-- 横截面指标全空：池太小 / 有效样本不足，提示用户换池 -->
      <n-alert
        v-if="crossSectionalEmpty"
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

        <!-- IC 衰减 / 半衰期：仅当至少有 IC 或 Rank IC 的多周期数据时渲染。
             数据来源复用 payload.ic / payload.rank_ic，前端就地算均值 + 半衰期，
             不走后端——老评估无需重跑也能看。 -->
        <n-card
          v-if="payload.ic || payload.rank_ic"
          title="IC 随前瞻期衰减 / 半衰期"
          size="small"
          style="margin-bottom: 24px"
        >
          <ic-decay-chart :ic="payload.ic" :rank-ic="payload.rank_ic" />
        </n-card>

        <!-- Alphalens 增强视角：排名自相关 / 分组累积净值(去均值) / Alpha-Beta -->
        <n-card
          v-if="payload.alphalens"
          title="Alphalens 增强视角"
          size="small"
          style="margin-bottom: 24px"
        >
          <n-grid :cols="3" :x-gap="16" :y-gap="16">
            <n-grid-item>
              <chart-card title="因子排名自相关">
                <rank-autocorr-chart
                  v-if="payload.alphalens.rank_autocorrelation"
                  :series="payload.alphalens.rank_autocorrelation"
                />
                <n-empty v-else description="无数据" />
              </chart-card>
            </n-grid-item>

            <n-grid-item>
              <chart-card title="分组累积净值（去均值）">
                <!-- alphalens 在后端已经 (1+r).cumprod() 过，前端再 cumprod 会重复累积。 -->
                <group-returns-chart
                  v-if="payload.alphalens.group_cumulative_returns"
                  :data="payload.alphalens.group_cumulative_returns"
                  :cumulative="false"
                />
                <n-empty v-else description="无数据" />
              </chart-card>
            </n-grid-item>

            <n-grid-item v-if="payload.alphalens.alpha_beta">
              <n-card size="small" title="Factor Alpha / Beta">
                <n-descriptions bordered :column="1" label-placement="left">
                  <n-descriptions-item label="年化 Alpha">
                    {{ fmtPct(payload.alphalens.alpha_beta.annualized_alpha) }}
                  </n-descriptions-item>
                  <n-descriptions-item label="日 Alpha">
                    {{ fmtNum(payload.alphalens.alpha_beta.alpha, 6) }}
                  </n-descriptions-item>
                  <n-descriptions-item label="Beta">
                    {{ fmtNum(payload.alphalens.alpha_beta.beta) }}
                  </n-descriptions-item>
                </n-descriptions>
              </n-card>
            </n-grid-item>
          </n-grid>
        </n-card>
      </template>

      <!-- 因子体检卡片：跨年 IC 稳定性 / 横截面独特值率 / qcut 满组率 / 多空样本比 /
           换手率水平 的红黄绿诊断。在看"评估指标"数字前先看这里能否相信数字。-->
      <template v-if="evalRun?.status === 'success' && health">
        <n-card
          size="small"
          style="margin-bottom: 24px"
        >
          <template #header>
            <n-space align="center">
              <span>因子体检</span>
              <n-tag :type="overallBadge(health.overall).type" size="small" round>
                {{ overallBadge(health.overall).text }}
              </n-tag>
            </n-space>
          </template>
          <n-grid :cols="5" :x-gap="12" :y-gap="12" responsive="screen" item-responsive>
            <n-grid-item
              v-for="item in health.items"
              :key="item.key"
              span="5 s:5 m:5 l:1 xl:1 2xl:1"
            >
              <div class="health-cell">
                <div class="health-cell-head">
                  <span class="health-cell-label">{{ item.label }}</span>
                  <n-tag :type="levelTagType(item.level)" size="small" round>
                    {{ item.level === 'green' ? '正常' : item.level === 'yellow' ? '注意' : '异常' }}
                  </n-tag>
                </div>
                <div class="health-cell-display">{{ item.display }}</div>
                <div class="health-cell-msg">{{ item.message }}</div>
              </div>
            </n-grid-item>
          </n-grid>
        </n-card>
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

        <!-- 样本内 / 样本外对比：仅当 eval 时传了 split_date 才渲染 -->
        <template v-if="hasSplit">
          <h3 style="margin-top: 24px; margin-bottom: 12px">
            样本内 / 样本外对比
            <span style="color: #848E9C; font-size: 12px; font-weight: normal">
              （切分日：{{ payload?.split_date }}）
            </span>
          </h3>
          <n-alert
            v-if="icMeanDiverged || rankIcMeanDiverged"
            type="warning"
            style="margin-bottom: 12px"
          >
            训练段 / 测试段 IC 出现显著分歧（符号翻转或幅度相差 2 倍以上），
            因子在样本外可能失效，谨慎使用。
          </n-alert>
          <!--
            这里刻意不再用 n-descriptions：后者是 label+content 成对渲染的语义，强行
            把它当 3 列网格用会出现"label 列空占位 / content 位串到下一格"的错位。
            改用 n-table 简单 3 列真表格：指标名 | 训练段 | 测试段。
          -->
          <n-table :bordered="true" :single-line="false" size="small">
            <thead>
              <tr>
                <th style="width: 180px">指标</th>
                <th>
                  训练段
                  <span style="color: #848E9C; font-size: 12px; margin-left: 4px">
                    [start, {{ payload?.split_date }})
                  </span>
                </th>
                <th>
                  测试段
                  <span style="color: #848E9C; font-size: 12px; margin-left: 4px">
                    [{{ payload?.split_date }}, end]
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  IC 均值
                  <n-tag
                    v-if="icMeanDiverged"
                    type="warning"
                    size="small"
                    round
                    style="margin-left: 6px"
                  >分歧</n-tag>
                </td>
                <td>{{ fmtNum(icSummaryTrain.ic_mean) }}</td>
                <td>{{ fmtNum(icSummaryTest.ic_mean) }}</td>
              </tr>
              <tr>
                <td>IC IR</td>
                <td>{{ fmtNum(icSummaryTrain.ic_ir) }}</td>
                <td>{{ fmtNum(icSummaryTest.ic_ir) }}</td>
              </tr>
              <tr>
                <td>IC 胜率</td>
                <td>{{ fmtPct(icSummaryTrain.ic_win_rate) }}</td>
                <td>{{ fmtPct(icSummaryTest.ic_win_rate) }}</td>
              </tr>
              <tr>
                <td>
                  Rank IC 均值
                  <n-tag
                    v-if="rankIcMeanDiverged"
                    type="warning"
                    size="small"
                    round
                    style="margin-left: 6px"
                  >分歧</n-tag>
                </td>
                <td>{{ fmtNum(rankIcSummaryTrain.ic_mean) }}</td>
                <td>{{ fmtNum(rankIcSummaryTest.ic_mean) }}</td>
              </tr>
              <tr>
                <td>Rank IC IR</td>
                <td>{{ fmtNum(rankIcSummaryTrain.ic_ir) }}</td>
                <td>{{ fmtNum(rankIcSummaryTest.ic_ir) }}</td>
              </tr>
            </tbody>
          </n-table>
        </template>
      </template>
        </n-tab-pane>
        <n-tab-pane name="time_series" tab="个股时序">
      <!-- 个股时序评估：对每只股票独立计算 IC / Hit Rate / 自相关 -->
      <template v-if="tsSummary">
        <h3 style="margin-bottom: 12px">个股时序评估</h3>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
          横截面 IC 回答"今天谁比谁好"，个股时序 IC 回答"这只票上因子持续有效吗"。
          时序 IC &gt; 0 表示因子值高时该股票确实倾向于上涨，是因子对个股稳定性的直接证据。
        </n-alert>

        <!-- 汇总统计 -->
        <n-card size="small" style="margin-bottom: 16px">
          <template #header>时序评估汇总</template>
          <n-grid :cols="6" :x-gap="12" responsive="screen">
            <n-grid-item span="6 s:3 m:2 l:1">
              <div class="ts-stat">
                <div class="ts-stat-label">个股时序 IC 均值</div>
                <div class="ts-stat-value" :style="{ color: (tsSummary.ts_ic_mean ?? 0) > 0 ? '#18A058' : '#D03050' }">
                  {{ fmtNum(tsSummary.ts_ic_mean) }}
                </div>
              </div>
            </n-grid-item>
            <n-grid-item span="6 s:3 m:2 l:1">
              <div class="ts-stat">
                <div class="ts-stat-label">IC &gt; 0 占比</div>
                <div class="ts-stat-value">{{ fmtPct(tsSummary.ts_ic_positive_ratio) }}</div>
              </div>
            </n-grid-item>
            <n-grid-item span="6 s:3 m:2 l:1">
              <div class="ts-stat">
                <div class="ts-stat-label">IC 横截面标准差</div>
                <div class="ts-stat-value">{{ fmtNum(tsSummary.ts_ic_std) }}</div>
              </div>
            </n-grid-item>
            <n-grid-item span="6 s:3 m:2 l:1">
              <div class="ts-stat">
                <div class="ts-stat-label">平均方向正确率</div>
                <div class="ts-stat-value">{{ fmtPct(tsSummary.ts_hit_rate_mean) }}</div>
              </div>
            </n-grid-item>
            <n-grid-item span="6 s:3 m:2 l:1">
              <div class="ts-stat">
                <div class="ts-stat-label">平均因子自相关</div>
                <div class="ts-stat-value">{{ fmtNum(tsSummary.ts_autocorr_mean) }}</div>
              </div>
            </n-grid-item>
            <n-grid-item span="6 s:3 m:2 l:1">
              <div class="ts-stat">
                <div class="ts-stat-label">有效股票数</div>
                <div class="ts-stat-value">{{ tsSummary.ts_n_stocks }}</div>
              </div>
            </n-grid-item>
          </n-grid>
        </n-card>

        <!-- Top 30 股票明细 -->
        <n-card size="small" style="margin-bottom: 16px">
          <template #header>
            个股时序 IC Top 30（因子在这些股票上最有效）
          </template>
          <n-table :bordered="true" :single-line="false" size="small">
            <thead>
              <tr>
                <th>#</th>
                <th>股票</th>
                <th>时序 IC</th>
                <th>方向正确率</th>
                <th>因子自相关</th>
                <th>有效样本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in tsTopN.slice(0, 30)" :key="tsRowSymbol(row) ?? idx">
                <td style="color: #848E9C">{{ idx + 1 }}</td>
                <td><code>{{ tsRowSymbol(row) }}</code></td>
                <td :style="{ color: (row.ts_ic ?? 0) > 0 ? '#18A058' : '#D03050' }">
                  {{ fmtNum(row.ts_ic) }}
                </td>
                <td>{{ fmtPct(row.hit_rate) }}</td>
                <td>{{ fmtNum(row.autocorr) }}</td>
                <td style="color: #848E9C">{{ row.n_samples }}</td>
              </tr>
            </tbody>
          </n-table>
        </n-card>

        <!-- Bottom 30 股票明细 -->
        <n-card size="small" style="margin-bottom: 16px">
          <template #header>
            个股时序 IC Bottom 30（因子在这些股票上最无效或反向）
            <n-tag type="warning" size="small" style="margin-left: 8px">列为负值表示因子方向在该股上反向</n-tag>
          </template>
          <n-table :bordered="true" :single-line="false" size="small">
            <thead>
              <tr>
                <th>#</th>
                <th>股票</th>
                <th>时序 IC</th>
                <th>方向正确率</th>
                <th>因子自相关</th>
                <th>有效样本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in tsBottomN.slice(0, 30)" :key="tsRowSymbol(row) ?? idx">
                <td style="color: #848E9C">{{ idx + 1 }}</td>
                <td><code>{{ tsRowSymbol(row) }}</code></td>
                <td :style="{ color: (row.ts_ic ?? 0) > 0 ? '#18A058' : '#D03050' }">
                  {{ fmtNum(row.ts_ic) }}
                </td>
                <td>{{ fmtPct(row.hit_rate) }}</td>
                <td>{{ fmtNum(row.autocorr) }}</td>
                <td style="color: #848E9C">{{ row.n_samples }}</td>
              </tr>
            </tbody>
          </n-table>
        </n-card>
      </template>

      <n-empty v-else description="时序评估数据不可用（可能因样本不足被跳过）" style="padding: 40px" />
        </n-tab-pane>
      </n-tabs>

    </n-spin>
  </div>
</template>

<style scoped>
.ts-stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--n-border-color, #eee);
  border-radius: 6px;
  height: 100%;
  min-height: 72px;
}
.ts-stat-label {
  font-size: 12px;
  color: var(--n-text-color-3, #848E9C);
}
.ts-stat-value {
  font-size: 20px;
  font-weight: 600;
}
.health-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--n-border-color, #eee);
  border-radius: 6px;
  height: 100%;
  min-height: 112px;
}
.health-cell-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.health-cell-label {
  font-size: 13px;
  color: var(--n-text-color-2, #606266);
}
.health-cell-display {
  font-size: 18px;
  font-weight: 600;
  word-break: break-all;
}
.health-cell-msg {
  font-size: 12px;
  color: var(--n-text-color-3, #848E9C);
  line-height: 1.45;
}
</style>
