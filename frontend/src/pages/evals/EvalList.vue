<script setup lang="ts">
/**
 * 评估结果列表
 * 过滤器：因子 / 状态 / limit；表格支持查看、删除、重跑
 */
import { computed, h, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPageHeader, NPopconfirm,
  NSelect, NInputNumber, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useEvals, useDeleteEval, useAbortEval } from '@/api/evals'
import type { EvalRun } from '@/api/evals'
import { useFactors } from '@/api/factors'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const router = useRouter()
const message = useMessage()

// ---- 过滤器 ----
const factorFilter = ref<string | null>(null)
const statusFilter = ref<string | null>(null)
const limit = ref<number>(50)

// 传给 useEvals 的响应式参数。undefined 值 axios 会自动跳过，不会拼到 query string。
const listParams = computed<Record<string, any>>(() => ({
  factor_id: factorFilter.value || undefined,
  status: statusFilter.value || undefined,
  limit: limit.value,
}))

// 运行中 / 排队中的行要继续轮询，让用户能看到中断后状态更新。
// 列表页无 refetchInterval，这里按需起一个：只在存在 pending/running/aborting 时轮询。
const listQuery = useEvals(listParams)
const { data: evals, isLoading, refetch } = listQuery
const deleteMut = useDeleteEval()
const abortMut = useAbortEval()

// 简单的 1.5s 轮询：有运行中 / 中断中的任务时启动定时器，全终态时停。
// 用 setInterval 而不是 useQuery refetchInterval，因为 useEvals 的 queryKey 含响应式
// params，改造成"条件 refetch"比嵌入式 refetchInterval 更直观。
let pollTimer: number | null = null
function maybeStartPolling() {
  const rows = evals.value ?? []
  const hasActive = rows.some((r) =>
    r.status === 'pending' || r.status === 'running' || r.status === 'aborting',
  )
  if (hasActive && pollTimer == null) {
    pollTimer = window.setInterval(() => { refetch() }, 1500)
  } else if (!hasActive && pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
watch(evals, maybeStartPolling, { immediate: true })
onUnmounted(() => { if (pollTimer != null) clearInterval(pollTimer) })

// ---- 下拉选项 ----
const { data: factors } = useFactors()
// NSelect 的 SelectMixedOption 不允许 value: null；原来 "全部因子/全部状态" 这样的哨兵项
// 编译通不过。这里删掉哨兵，改由 `clearable` 的 × 按钮表达"清空=全部"，语义等价。
const factorOptions = computed(() =>
  (factors.value ?? []).map(f => ({ label: f.display_name, value: f.factor_id }))
)
const statusOptions = [
  { label: '等待中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '中断中', value: 'aborting' },
  { label: '已中断', value: 'aborted' },
]

// 池名映射：pool_id → pool_name；查不到（软删 / 列表未载入）回退到 "#<id>"。
const { lookup: lookupPoolName } = usePoolNameMap()

// ---- 表格 ----
const columns: DataTableColumns<EvalRun> = [
  {
    title: 'Run ID',
    key: 'run_id',
    width: 110,
    render: (row) => h('code', { style: 'font-size: 12px' }, row.run_id.slice(0, 8)),
  },
  { title: '因子', key: 'factor_id', width: 160, ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => h(StatusBadge, { status: row.status }),
  },
  {
    title: '股票池',
    key: 'pool_id',
    width: 160,
    ellipsis: { tooltip: true },
    render: (row) => h(
      NTag,
      { size: 'small', bordered: false },
      { default: () => lookupPoolName(row.pool_id) },
    ),
  },
  {
    title: '日期区间',
    key: 'date_range',
    width: 200,
    render: (row) => `${row.start_date} ~ ${row.end_date}`,
  },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) => {
      const canAbort = row.status === 'pending' || row.status === 'running'
      const buttons: any[] = [
        h(NButton, {
          size: 'small',
          quaternary: true,
          type: 'primary',
          onClick: () => router.push(`/evals/${row.run_id}`),
        }, { default: () => '查看' }),
      ]
      if (canAbort) {
        // 中断按钮只对 pending/running 出现；aborting 态已经"正在中断"按了也没意义，
        // 后端虽然做了幂等兜底但 UI 上干脆藏起来避免用户误以为"按了没反应"。
        buttons.push(
          h(NPopconfirm, {
            onPositiveClick: () => {
              abortMut.mutate(row.run_id, {
                onSuccess: () => message.info('中断请求已发送，最坏需等当前阶段结束'),
                onError: (e: any) =>
                  message.error(e?.response?.data?.detail || e?.message || '中断失败'),
              })
            },
          }, {
            trigger: () => h(NButton, {
              size: 'small', quaternary: true, type: 'warning',
            }, { default: () => '中断' }),
            default: () => `确认中断 ${row.run_id.slice(0, 8)}...？`,
          }),
        )
      }
      buttons.push(
        h(NPopconfirm, {
          onPositiveClick: () => {
            deleteMut.mutate(row.run_id, {
              onSuccess: () => message.success('已删除'),
              onError: (e: any) => message.error(e?.message || '删除失败'),
            })
          },
        }, {
          trigger: () => h(NButton, { size: 'small', quaternary: true, type: 'error' }, { default: () => '删除' }),
          default: () => `确认删除 ${row.run_id.slice(0, 8)}...？`,
        }),
      )
      return h(NSpace, { size: 4 }, { default: () => buttons })
    },
  },
]
</script>

<template>
  <div>
    <n-page-header title="评估记录" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="router.push('/evals/new')">
          + 新建评估
        </n-button>
      </template>
    </n-page-header>

    <!-- 过滤器 -->
    <n-space style="margin-bottom: 16px" align="center">
      <n-select
        v-model:value="factorFilter"
        :options="factorOptions"
        placeholder="因子"
        clearable
        filterable
        style="width: 220px"
      />
      <n-select
        v-model:value="statusFilter"
        :options="statusOptions"
        placeholder="状态"
        clearable
        style="width: 140px"
      />
      <n-input-number
        v-model:value="limit"
        :min="10"
        :max="500"
        :step="10"
        style="width: 120px"
      >
        <template #prefix>条数</template>
      </n-input-number>
    </n-space>

    <n-data-table
      :columns="columns"
      :data="evals ?? []"
      :loading="isLoading"
      :bordered="false"
      :single-line="false"
      :row-key="(row: any) => row.run_id"
    />
  </div>
</template>
