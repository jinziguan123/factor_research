<script setup lang="ts">
/**
 * 实盘信号记录列表。
 * 与 CompositionList 同结构，差异：
 * - 多 method='single' 选项；
 * - 列：as_of_time / use_realtime / 持仓数（top/bot）；
 * - 支持批量删除（沿用 evals 列表的 NSwitch 选择行 + 批删按钮）；
 * - 1.5s 轮询：有 pending/running/aborting 时启动。
 */
import { computed, h, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPageHeader, NPopconfirm,
  NSelect, NInputNumber, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  useSignals,
  useDeleteSignal,
  useAbortSignal,
  useBatchDeleteSignals,
} from '@/api/signals'
import type { SignalRun } from '@/api/signals'
import { usePoolNameMap, usePools } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const router = useRouter()
const message = useMessage()

const poolFilter = ref<number | null>(null)
const methodFilter = ref<string | null>(null)
const statusFilter = ref<string | null>(null)
const asOfDateFilter = ref<string | null>(null)
const limit = ref<number>(50)

const listParams = computed<Record<string, any>>(() => ({
  pool_id: poolFilter.value ?? undefined,
  method: methodFilter.value || undefined,
  status: statusFilter.value || undefined,
  as_of_date: asOfDateFilter.value || undefined,
  limit: limit.value,
}))

const listQuery = useSignals(listParams)
const { data: runs, isLoading, refetch } = listQuery
const deleteMut = useDeleteSignal()
const abortMut = useAbortSignal()
const batchDeleteMut = useBatchDeleteSignals()

const checkedKeys = ref<string[]>([])

function handleBatchDelete() {
  if (checkedKeys.value.length === 0) return
  batchDeleteMut.mutate(checkedKeys.value, {
    onSuccess: (res: any) => {
      message.success(`已删除 ${res.deleted_count} 条记录`)
      checkedKeys.value = []
    },
    onError: (e: any) => message.error(e?.message || '批量删除失败'),
  })
}

// 1.5s 轮询：有运行中 / 中断中任务时启动
let pollTimer: number | null = null
function maybeStartPolling() {
  const rows = runs.value ?? []
  const hasActive = rows.some(
    (r) => r.status === 'pending' || r.status === 'running' || r.status === 'aborting',
  )
  if (hasActive && pollTimer == null) {
    pollTimer = window.setInterval(() => { refetch() }, 1500)
  } else if (!hasActive && pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
watch(runs, maybeStartPolling, { immediate: true })
onUnmounted(() => { if (pollTimer != null) clearInterval(pollTimer) })

const { data: pools } = usePools()
const poolOptions = computed(() =>
  (pools.value ?? []).map((p: any) => ({ label: p.name, value: p.pool_id })),
)
const methodOptions = [
  { label: '单因子 (single)', value: 'single' },
  { label: '等权 (equal)', value: 'equal' },
  { label: 'IC 加权 (ic_weighted)', value: 'ic_weighted' },
  { label: '正交等权 (orthogonal_equal)', value: 'orthogonal_equal' },
]
const statusOptions = [
  { label: '等待中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '中断中', value: 'aborting' },
  { label: '已中断', value: 'aborted' },
]
const { lookup: lookupPoolName } = usePoolNameMap()

function methodLabel(m: string): string {
  switch (m) {
    case 'single': return '单因子'
    case 'equal': return '等权'
    case 'ic_weighted': return 'IC 加权'
    case 'orthogonal_equal': return '正交等权'
    default: return m
  }
}

const columns: DataTableColumns<SignalRun> = [
  { type: 'selection' },
  {
    title: 'Run ID',
    key: 'run_id',
    width: 100,
    render: (row) => h('code', { style: 'font-size: 12px' }, row.run_id.slice(0, 8)),
  },
  {
    title: '因子',
    key: 'factor_items',
    width: 240,
    ellipsis: { tooltip: true },
    render: (row) =>
      h(NSpace, { size: 4 }, {
        default: () =>
          (row.factor_items || []).map((it: any) =>
            h(NTag, { size: 'small', type: 'info', bordered: false }, {
              default: () => it.factor_id,
            }),
          ),
      }),
  },
  {
    title: '方法',
    key: 'method',
    width: 110,
    render: (row) => h(NTag, { size: 'small', bordered: false }, {
      default: () => methodLabel(row.method),
    }),
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => h(StatusBadge, { status: row.status }),
  },
  {
    title: '股票池',
    key: 'pool_id',
    width: 130,
    ellipsis: { tooltip: true },
    render: (row) => h(NTag, { size: 'small', bordered: false }, {
      default: () => lookupPoolName(row.pool_id),
    }),
  },
  {
    title: 'top/bot',
    key: 'holdings',
    width: 90,
    render: (row) => {
      const t = row.n_holdings_top ?? '-'
      const b = row.n_holdings_bot ?? '-'
      return `${t} / ${b}`
    },
  },
  {
    title: '实时',
    key: 'use_realtime',
    width: 60,
    render: (row) => row.use_realtime ? '✓' : '—',
  },
  { title: '触发时刻', key: 'as_of_time', width: 160 },
  { title: '当日', key: 'as_of_date', width: 110 },
  {
    title: '操作',
    key: 'actions',
    width: 200,
    render: (row) => {
      const canAbort = row.status === 'pending' || row.status === 'running'
      const buttons: any[] = [
        h(NButton, {
          size: 'small', quaternary: true, type: 'primary',
          onClick: () => router.push(`/signals/${row.run_id}`),
        }, { default: () => '查看' }),
      ]
      if (canAbort) {
        buttons.push(
          h(NPopconfirm, {
            onPositiveClick: () => {
              abortMut.mutate(row.run_id, {
                onSuccess: () => message.info('中断请求已发送'),
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
          trigger: () => h(NButton, { size: 'small', quaternary: true, type: 'error' }, {
            default: () => '删除',
          }),
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
    <n-page-header title="实盘信号" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="router.push('/signals/new')">
          + 新建信号
        </n-button>
      </template>
    </n-page-header>

    <n-space style="margin-bottom: 16px" align="center">
      <n-select
        v-model:value="poolFilter"
        :options="poolOptions"
        placeholder="股票池"
        clearable filterable
        style="width: 220px"
      />
      <n-select
        v-model:value="methodFilter"
        :options="methodOptions"
        placeholder="方法"
        clearable
        style="width: 200px"
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
        :min="10" :max="500" :step="10"
        style="width: 120px"
      >
        <template #prefix>条数</template>
      </n-input-number>
      <n-popconfirm
        v-if="checkedKeys.length > 0"
        @positive-click="handleBatchDelete"
      >
        <template #trigger>
          <n-button type="error" :loading="batchDeleteMut.isPending.value">
            批量删除 ({{ checkedKeys.length }})
          </n-button>
        </template>
        确认删除选中的 {{ checkedKeys.length }} 条记录？
      </n-popconfirm>
    </n-space>

    <n-data-table
      v-model:checked-row-keys="checkedKeys"
      :columns="columns"
      :data="runs ?? []"
      :loading="isLoading"
      :bordered="false"
      :single-line="false"
      :row-key="(row: any) => row.run_id"
    />
  </div>
</template>
