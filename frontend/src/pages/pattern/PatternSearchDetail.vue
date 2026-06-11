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
  usePatternRun, useAbortPatternRun, useCreateWindowSearch,
  useCreateLearnedSearch, useAddLabel, type PatternMatch, type WindowSpec,
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
const createWindow = useCreateWindowSearch()
const createLearned = useCreateLearnedSearch()
const addLabel = useAddLabel()

const isActive = computed(() =>
  ['pending', 'running', 'aborting'].includes(run.value?.status ?? ''))

// learned 任务的形态名（query_json={pattern_name}）；结果可继续标注、回炉重训。
const learnedPattern = computed<string | null>(() => {
  const q = run.value?.query_json
  return (q && !Array.isArray(q) && q.pattern_name) ? q.pattern_name : null
})

// 一键重新检索/重训：走势选股与学习型都能原样重建；截图任务图片未存，回新建页重传。
async function rerun() {
  const r = run.value
  if (!r) return
  try {
    if (r.kind === 'by_window' && Array.isArray(r.query_json) && r.query_json.length > 0) {
      const res = await createWindow.mutateAsync({
        pool_id: r.pool_id, windows: r.query_json as WindowSpec[],
        agg: (r.agg as 'min' | 'mean') ?? 'min', top_k: r.top_k,
      })
      message.success('已按相同条件重新提交')
      router.push(`/pattern/runs/${res.run_id}`)
    } else if (r.kind === 'learned' && learnedPattern.value) {
      const res = await createLearned.mutateAsync({ pattern_name: learnedPattern.value, pool_id: r.pool_id, top_k: r.top_k })
      message.success('已用最新标注重新训练+选股')
      router.push(`/pattern/runs/${res.run_id}`)
    } else {
      message.info('截图任务的图片未保存，请到新建页重新上传')
      router.push('/pattern/new')
    }
  } catch (e: any) {
    message.error(e?.message || '重新检索失败')
  }
}

// learned 结果上的 👍👎：把这只股票的该窗口加为正/反例（喂给同一个形态）。
async function onLabel(m: PatternMatch, value: number) {
  if (!learnedPattern.value) return
  try {
    await addLabel.mutateAsync({
      pattern_name: learnedPattern.value,
      symbol: m.label,
      start: m.start_date ?? undefined,
      end: m.end_date ?? undefined,
      label: value,
    })
    message.success(value === 1 ? '已加为正例（重训后生效）' : '已加为反例')
  } catch (e: any) {
    message.error(e?.message || '标注失败')
  }
}
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
          <n-button
            v-if="run && !isActive"
            size="small" type="primary"
            :loading="createWindow.isPending.value"
            @click="rerun"
          >
            重新检索
          </n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-card v-if="run" style="margin-bottom: 16px">
      <n-descriptions :column="3" label-placement="left" size="small">
        <n-descriptions-item label="类型">
          {{ run.kind === 'by_window' ? '走势选股' : run.kind === 'learned' ? '学习型选股' : '截图检索' }}
        </n-descriptions-item>
        <n-descriptions-item label="股票池">#{{ run.pool_id }}</n-descriptions-item>
        <n-descriptions-item v-if="run.kind !== 'learned'" label="聚合">{{ run.agg }}</n-descriptions-item>
        <n-descriptions-item v-if="run.kind === 'learned'" label="形态">{{ learnedPattern ?? '-' }}</n-descriptions-item>
        <n-descriptions-item v-if="run.kind === 'by_window'" label="查询走势" :span="3">
          {{ (Array.isArray(run.query_json) ? run.query_json : []).map(w => `${w.symbol} ${w.start ?? ''}~${w.end ?? ''}`).join('；') || '-' }}
        </n-descriptions-item>
        <template v-else-if="run.kind !== 'learned'">
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
      <div v-if="queryCurves.length" style="font-size: 12px; opacity: 0.6; margin-bottom: 4px">
        {{ run.kind === 'learned' ? '学到的正例曲线（参考）：' : '系统识别出的查询曲线（每张图一条）：' }}
      </div>
      <n-space :size="12" wrap>
        <div v-for="(c, i) in queryCurves" :key="i" style="width: 200px">
          <div style="font-size: 12px; opacity: 0.5">{{ run.kind === 'learned' ? '正例' : '图' }} {{ i + 1 }}</div>
          <v-chart style="height: 100px" :option="queryOption(c)" autoresize />
        </div>
      </n-space>
      <div v-if="run.kind === 'learned'" style="font-size:12px;opacity:.6;margin:8px 0">
        对下面结果点 👍/👎 继续标注，再点右上「重新检索」即可用新标注重训——越标越准。
      </div>
      <div style="margin-top: 12px">
        <match-result-list
          :matches="run.matches"
          :labelable="run.kind === 'learned'"
          @open="openMatch" @label="onLabel"
        />
      </div>
    </n-card>
  </div>
</template>
