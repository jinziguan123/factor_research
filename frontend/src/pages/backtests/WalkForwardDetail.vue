<script setup lang="ts">
/**
 * 样本外验证（Walk-Forward）结果页。
 * 复用回测 run（payload.method='walk_forward'）：展示 IS/OOS IC 衰减 + OOS 权益曲线 +
 * OOS 风险收益指标 + 逐窗口明细。衰减比 < 0.5 或变号给出过拟合告警。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NGrid, NGi, NStatistic, NDataTable,
  NAlert, NSpin, NTag, NEmpty,
} from 'naive-ui'
import { useBacktest } from '@/api/backtests'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'

const route = useRoute()
const router = useRouter()
const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useBacktest(runId)

const status = computed(() => run.value?.status)
const payload = computed<any>(() => run.value?.payload ?? null)
const icDecay = computed<any>(() => payload.value?.ic_decay ?? null)
const summary = computed<any>(() => payload.value?.summary ?? null)
const windows = computed<any[]>(() => payload.value?.windows ?? [])
const oosEquity = computed<any>(() => payload.value?.oos_equity ?? null)

function pct(v: any) { return v == null ? '—' : (Number(v) * 100).toFixed(2) + '%' }
function num(v: any, d = 4) { return v == null ? '—' : Number(v).toFixed(d) }

const overfit = computed<{ type: 'error' | 'warning' | 'success'; msg: string } | null>(() => {
  const r = icDecay.value?.ic_decay_ratio
  if (r == null) return null
  if (r < 0) return { type: 'error', msg: '样本外 IC 与样本内反号 —— 强过拟合信号' }
  if (r < 0.5) return { type: 'warning', msg: '样本外 IC 较样本内衰减超过一半 —— 疑似过拟合' }
  return { type: 'success', msg: '样本外 IC 衰减可控 —— 相对稳健' }
})

const windowColumns: any[] = [
  { title: '窗口', key: 'window', width: 64 },
  { title: '测试区间', key: 'test', render: (r: any) => `${r.test_start} ~ ${r.test_end}` },
  { title: '股票数', key: 'n_stocks', width: 80 },
  { title: 'OOS收益', key: 'total_return', render: (r: any) => pct(r.total_return) },
  { title: '训练IC', key: 'train_ic', render: (r: any) => num(r.train_ic) },
  { title: '测试IC', key: 'test_ic', render: (r: any) => num(r.test_ic) },
]

function statusType(s?: string) {
  if (s === 'success') return 'success'
  if (s === 'failed') return 'error'
  if (s === 'aborted') return 'warning'
  return 'info'
}
</script>

<template>
  <div>
    <n-page-header
      title="样本外验证结果"
      @back="router.push('/backtests/walk-forward')"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <n-tag :type="statusType(status)">{{ status }}</n-tag>
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <n-alert v-if="status === 'pending' || status === 'running'" type="info">
        任务运行中，页面每 1.5 秒自动刷新…
      </n-alert>
      <n-alert v-else-if="status === 'failed'" type="error">
        任务失败：{{ run?.error || '未知错误' }}
      </n-alert>

      <div v-else-if="payload">
        <n-alert v-if="overfit" :type="overfit.type" :show-icon="true" style="margin-bottom: 16px">
          {{ overfit.msg }}
        </n-alert>

        <!-- IC 衰减 -->
        <n-grid :cols="4" :x-gap="12" style="margin-bottom: 12px">
          <n-gi><n-card size="small"><n-statistic label="样本内 IC" :value="num(icDecay?.is_ic_mean)" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="样本外 IC" :value="num(icDecay?.oos_ic_mean)" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="IC 衰减比 (OOS/IS)" :value="num(icDecay?.ic_decay_ratio, 2)" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="OOS IC IR" :value="num(icDecay?.oos_ic_ir, 2)" /></n-card></n-gi>
        </n-grid>

        <!-- OOS 风险收益 -->
        <n-grid :cols="4" :x-gap="12" style="margin-bottom: 16px">
          <n-gi><n-card size="small"><n-statistic label="OOS 总收益" :value="pct(summary?.total_return)" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="OOS 年化" :value="pct(summary?.annual_return)" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="OOS Sharpe" :value="num(summary?.sharpe_ratio, 2)" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="OOS 最大回撤" :value="pct(summary?.max_drawdown)" /></n-card></n-gi>
        </n-grid>

        <n-card title="样本外权益曲线 (OOS Equity)" size="small" style="margin-bottom: 16px">
          <equity-curve-chart v-if="oosEquity && oosEquity.dates?.length" :equity="oosEquity" />
          <n-empty v-else description="无权益数据" />
        </n-card>

        <n-card title="逐窗口明细" size="small">
          <n-data-table :columns="windowColumns" :data="windows" :bordered="false" size="small" />
        </n-card>
      </div>

      <n-empty v-else description="暂无结果" />
    </n-spin>
  </div>
</template>
