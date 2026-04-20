<script setup lang="ts">
/**
 * 成本敏感性记录列表。
 * 过滤：因子 / 状态 / limit；操作：查看、删除。结构同 BacktestList。
 */
import { computed, h, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPageHeader, NPopconfirm,
  NSelect, NInputNumber, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  useCostSensitivityRuns,
  useDeleteCostSensitivity,
  useAbortCostSensitivity,
} from '@/api/cost_sensitivity'
import type { CostSensitivityRun } from '@/api/cost_sensitivity'
import { useFactors } from '@/api/factors'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const router = useRouter()
const message = useMessage()

const factorFilter = ref<string | null>(null)
const statusFilter = ref<string | null>(null)
const limit = ref<number>(50)

const listParams = computed<Record<string, any>>(() => ({
  factor_id: factorFilter.value || undefined,
  status: statusFilter.value || undefined,
  limit: limit.value,
}))

// grid 场景尤其适用协作式中断（10 个 cost_bps 点一个一个跑）。
// 列表页轮询让"中断中 → 已中断"的过渡能被及时看到。
const listQuery = useCostSensitivityRuns(listParams)
const { data: runs, isLoading, refetch } = listQuery
const deleteMut = useDeleteCostSensitivity()
const abortMut = useAbortCostSensitivity()

let pollTimer: number | null = null
function maybeStartPolling() {
  const rows = runs.value ?? []
  const hasActive = rows.some((r) =>
    r.status === 'pending' || r.status === 'running' || r.status === 'aborting',
  )
  if (hasActive && pollTimer == null) {
    pollTimer = window.setInterval(() => { refetch() }, 2000)
  } else if (!hasActive && pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
watch(runs, maybeStartPolling, { immediate: true })
onUnmounted(() => { if (pollTimer != null) clearInterval(pollTimer) })

const { data: factors } = useFactors()
const factorOptions = computed(() =>
  (factors.value ?? []).map((f) => ({ label: f.display_name, value: f.factor_id })),
)
const statusOptions = [
  { label: '等待中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '中断中', value: 'aborting' },
  { label: '已中断', value: 'aborted' },
]

const { lookup: lookupPoolName } = usePoolNameMap()

// cost_bps_list 在列表页要么是 JSON 数组要么原样字符串，统一格式成 "0 / 3 / 5 / 10" 展示。
function fmtBpsList(v: number[] | string | undefined): string {
  if (!v) return '-'
  const arr = Array.isArray(v) ? v : JSON.parse(v as string)
  return arr.map((x: number) => `${x}bp`).join(' / ')
}

const columns: DataTableColumns<CostSensitivityRun> = [
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
    title: '成本点',
    key: 'cost_bps_list',
    width: 220,
    render: (row) => fmtBpsList(row.cost_bps_list),
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
          onClick: () => router.push(`/cost-sensitivity/${row.run_id}`),
        }, { default: () => '查看' }),
      ]
      if (canAbort) {
        buttons.push(
          h(NPopconfirm, {
            onPositiveClick: () => {
              abortMut.mutate(row.run_id, {
                onSuccess: () => message.info('中断请求已发送，最坏需等当前成本点算完'),
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
    <n-page-header title="成本敏感性" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="router.push('/cost-sensitivity/new')">
          + 新建敏感性分析
        </n-button>
      </template>
    </n-page-header>

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
      :data="runs ?? []"
      :loading="isLoading"
      :bordered="false"
      :single-line="false"
      :row-key="(row: any) => row.run_id"
    />
  </div>
</template>
