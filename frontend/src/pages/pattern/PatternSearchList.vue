<script setup lang="ts">
/**
 * 图形检索记录列表（需求1 by_image 异步任务）。
 * 列出历史检索任务，可进入详情查看结果、删除、或新建一次检索。
 * 有 pending/running/aborting 任务时自动轮询刷新列表。
 */
import { h, watch, onUnmounted } from 'vue'
import {
  NPageHeader, NCard, NButton, NDataTable, NSpace, NPopconfirm,
  useMessage, type DataTableColumns,
} from 'naive-ui'
import { useRouter } from 'vue-router'
import {
  usePatternRuns, useDeletePatternRun, useCreateWindowSearch,
  useCreateLearnedSearch, type PatternRun, type WindowSpec,
} from '@/api/patternSearch'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const router = useRouter()
const message = useMessage()

const { data: runs, refetch } = usePatternRuns()
const del = useDeletePatternRun()
const createWindow = useCreateWindowSearch()
const createLearned = useCreateLearnedSearch()

function learnedPatternOf(r: PatternRun): string | null {
  const q = r.query_json
  return (q && !Array.isArray(q) && q.pattern_name) ? q.pattern_name : null
}

// 一键重新检索：走势选股/学习型都能原样重建重跑；截图任务图片未存，回新建页重传。
async function rerun(r: PatternRun) {
  try {
    if (r.kind === 'by_window' && Array.isArray(r.query_json) && r.query_json.length > 0) {
      const res = await createWindow.mutateAsync({
        pool_id: r.pool_id, windows: r.query_json as WindowSpec[],
        agg: (r.agg as 'min' | 'mean') ?? 'min', top_k: r.top_k,
      })
      message.success('已按相同条件重新提交')
      router.push(`/pattern/runs/${res.run_id}`)
    } else if (r.kind === 'learned' && learnedPatternOf(r)) {
      const res = await createLearned.mutateAsync({ pattern_name: learnedPatternOf(r)!, pool_id: r.pool_id, top_k: r.top_k })
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

// 有活跃任务时轮询列表。
let pollTimer: number | null = null
function maybeStartPolling() {
  const rows = runs.value ?? []
  const hasActive = rows.some(r =>
    r.status === 'pending' || r.status === 'running' || r.status === 'aborting')
  if (hasActive && pollTimer == null) {
    pollTimer = window.setInterval(() => { refetch() }, 1500)
  } else if (!hasActive && pollTimer != null) {
    clearInterval(pollTimer); pollTimer = null
  }
}
watch(runs, maybeStartPolling, { immediate: true })
onUnmounted(() => { if (pollTimer != null) clearInterval(pollTimer) })

async function onDelete(runId: string) {
  try {
    await del.mutateAsync(runId)
    message.success('已删除')
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

const columns: DataTableColumns<PatternRun> = [
  {
    title: 'Run ID', key: 'run_id', width: 110,
    render: (r) => h('code', { style: 'font-size:12px' }, r.run_id.slice(0, 8)),
  },
  { title: '股票池', key: 'pool_id', width: 90, render: (r) => `#${r.pool_id}` },
  {
    title: '类型', key: 'kind', width: 90,
    render: (r) => (r.kind === 'by_window' ? '走势选股' : r.kind === 'learned' ? '学习型' : '截图'),
  },
  {
    title: '查询', key: 'query', ellipsis: { tooltip: true },
    render: (r) => {
      if (r.kind === 'by_window') {
        const ws = Array.isArray(r.query_json) ? r.query_json : []
        return ws.map(w => `${w.symbol} ${w.start ?? ''}~${w.end ?? ''}`).join('；') || '-'
      }
      if (r.kind === 'learned') return `形态：${learnedPatternOf(r) ?? '-'}`
      return (r.image_names ?? []).join('、') || `${r.num_images} 张图`
    },
  },
  {
    title: '状态', key: 'status', width: 140,
    render: (r) => h(StatusBadge, { status: r.status }),
  },
  { title: '进度', key: 'progress', width: 70, render: (r) => `${r.progress}%` },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作', key: 'actions', width: 220,
    render: (r) => h(NSpace, { size: 8 }, () => [
      h(NButton, { size: 'small', onClick: () => router.push(`/pattern/runs/${r.run_id}`) }, () => '查看'),
      h(NButton, { size: 'small', type: 'primary', tertiary: true, onClick: () => rerun(r) }, () => '重新检索'),
      h(NPopconfirm, { onPositiveClick: () => onDelete(r.run_id) }, {
        trigger: () => h(NButton, { size: 'small', type: 'error', tertiary: true }, () => '删除'),
        default: () => '确认删除这条记录？',
      }),
    ]),
  },
]
</script>

<template>
  <div>
    <n-page-header title="图形检索记录" style="margin-bottom: 16px">
      <template #subtitle>图形检索历史任务（截图找相似 / 走势选股）。后台异步执行，可随时回来看结果。</template>
      <template #extra>
        <n-button type="primary" @click="router.push('/pattern/new')">+ 新建图形检索</n-button>
      </template>
    </n-page-header>

    <n-card>
      <n-data-table
        :columns="columns"
        :data="runs ?? []"
        :bordered="false"
        :single-line="false"
        size="small"
        :row-key="(r: PatternRun) => r.run_id"
      />
    </n-card>
  </div>
</template>
