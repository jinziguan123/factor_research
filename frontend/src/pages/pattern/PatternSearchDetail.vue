<script setup lang="ts">
/**
 * 图形检索任务详情（需求1 by_image）。
 * 轮询任务状态：运行中显示进度条；成功后展示每张识别曲线 + 相似股票列表，
 * 点击某条跳到对应股票 K 线区间。
 */
import { computed } from 'vue'
import {
  NPageHeader, NCard, NProgress, NAlert, NButton, NSpace, NDescriptions,
  NDescriptionsItem, useMessage,
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { useRoute, useRouter } from 'vue-router'
import {
  usePatternRun, useAbortPatternRun, type PatternMatch,
} from '@/api/patternSearch'
import MatchResultList from '@/components/pattern/MatchResultList.vue'
import StatusBadge from '@/components/layout/StatusBadge.vue'

use([CanvasRenderer, LineChart, GridComponent])

const route = useRoute()
const router = useRouter()
const message = useMessage()

const runId = computed(() => String(route.params.runId ?? ''))
const { data: run } = usePatternRun(runId)
const abort = useAbortPatternRun()

const isActive = computed(() =>
  ['pending', 'running', 'aborting'].includes(run.value?.status ?? ''))
const queryCurves = computed<number[][]>(() => run.value?.query_curves ?? [])

async function onAbort() {
  try {
    await abort.mutateAsync(runId.value)
    message.info('已请求中断')
  } catch (e: any) {
    message.error(e?.message || '中断失败')
  }
}

function openMatch(m: PatternMatch) {
  router.push({
    path: '/klines',
    query: {
      symbol: m.label,
      start: m.start_date ?? undefined,
      end: m.end_date ?? undefined,
    },
  })
}

function queryOption(curve: number[]) {
  return {
    animation: false,
    grid: { left: 4, right: 4, top: 4, bottom: 4 },
    xAxis: { type: 'category', show: false, data: curve.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{ type: 'line', data: curve, showSymbol: false, lineStyle: { width: 1.5 } }],
  }
}
</script>

<template>
  <div>
    <n-page-header :title="`检索任务 ${runId.slice(0, 8)}`" style="margin-bottom: 16px" @back="router.push('/pattern')">
      <template #extra>
        <n-space>
          <status-badge v-if="run" :status="run.status" />
          <n-button v-if="isActive" size="small" type="warning" @click="onAbort">中断</n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-card v-if="run" style="margin-bottom: 16px">
      <n-descriptions :column="3" label-placement="left" size="small">
        <n-descriptions-item label="类型">{{ run.kind === 'by_window' ? '走势选股' : '截图检索' }}</n-descriptions-item>
        <n-descriptions-item label="股票池">#{{ run.pool_id }}</n-descriptions-item>
        <n-descriptions-item label="聚合">{{ run.agg }}</n-descriptions-item>
        <n-descriptions-item v-if="run.kind === 'by_window'" label="查询走势" :span="3">
          {{ (run.query_json ?? []).map(w => `${w.symbol} ${w.start ?? ''}~${w.end ?? ''}`).join('；') || '-' }}
        </n-descriptions-item>
        <template v-else>
          <n-descriptions-item label="图数">{{ run.num_images }}</n-descriptions-item>
          <n-descriptions-item label="截图">{{ (run.image_names ?? []).join('、') || '-' }}</n-descriptions-item>
          <n-descriptions-item label="提示">{{ run.hint || '-' }}</n-descriptions-item>
        </template>
        <n-descriptions-item label="创建时间">{{ run.created_at }}</n-descriptions-item>
      </n-descriptions>
    </n-card>

    <n-card v-if="run && isActive" style="margin-bottom: 16px">
      <div style="margin-bottom: 8px; opacity: 0.7">任务执行中，自动刷新…</div>
      <n-progress type="line" :percentage="run.progress" :processing="true" />
    </n-card>

    <n-alert v-if="run && run.status === 'failed'" type="error" title="检索失败" style="margin-bottom: 16px">
      <pre style="white-space: pre-wrap; font-size: 12px; margin: 0">{{ run.error_message }}</pre>
    </n-alert>
    <n-alert v-if="run && run.status === 'aborted'" type="warning" style="margin-bottom: 16px">
      任务已被中断。
    </n-alert>

    <n-card v-if="run && run.status === 'success'">
      <div style="font-size: 12px; opacity: 0.6; margin-bottom: 4px">系统识别出的查询曲线（每张图一条）：</div>
      <n-space :size="12" wrap>
        <div v-for="(c, i) in queryCurves" :key="i" style="width: 200px">
          <div style="font-size: 12px; opacity: 0.5">图 {{ i + 1 }}</div>
          <v-chart style="height: 100px" :option="queryOption(c)" autoresize />
        </div>
      </n-space>
      <div style="margin-top: 12px">
        <match-result-list :matches="run.matches" @open="openMatch" />
      </div>
    </n-card>
  </div>
</template>
