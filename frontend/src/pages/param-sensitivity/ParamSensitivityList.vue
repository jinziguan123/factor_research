<script setup lang="ts">
/**
 * 参数敏感性记录列表。
 * 过滤：因子 / 状态 / limit；操作：查看、中断（进行中）、删除。结构同 CostSensitivityList。
 */
import { computed, h, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPageHeader, NPopconfirm,
  NSelect, NInputNumber, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  useParamSensitivityRuns,
  useDeleteParamSensitivity,
  useAbortParamSensitivity,
} from '@/api/param_sensitivity'
import type { ParamSensitivityRun } from '@/api/param_sensitivity'
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

// 单点 20-60s，整批 5-15 分钟很常见——和 cost_sensitivity 一样轮询，及时反映进度。
const listQuery = useParamSensitivityRuns(listParams)
const { data: runs, isLoading, refetch } = listQuery
const deleteMut = useDeleteParamSensitivity()
const abortMut = useAbortParamSensitivity()

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

// values 列可能是数组或 null（老记录），统一格式成 "10 / 20 / 30 / 40" 展示。
function fmtValues(v: number[] | null | undefined): string {
  if (!v || !Array.isArray(v) || v.length === 0) return '-'
  if (v.length <= 6) return v.join(' / ')
  return `${v.slice(0, 5).join(' / ')} … 共 ${v.length} 个`
}

const columns: DataTableColumns<ParamSensitivityRun> = [
  {
    title: 'Run ID',
    key: 'run_id',
    width: 110,
    render: (row) => h('code', { style: 'font-size: 12px' }, row.run_id.slice(0, 8)),
  },
  { title: '因子', key: 'factor_id', width: 160, ellipsis: { tooltip: true } },
  { title: '扫描参数', key: 'param_name', width: 120, ellipsis: { tooltip: true } },
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
    title: '扫描点',
    key: 'values',
    width: 220,
    ellipsis: { tooltip: true },
    render: (row) => fmtValues(row.values),
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
          onClick: () => router.push(`/param-sensitivity/${row.run_id}`),
        }, { default: () => '查看' }),
      ]
      if (canAbort) {
        buttons.push(
          h(NPopconfirm, {
            onPositiveClick: () => {
              abortMut.mutate(row.run_id, {
                onSuccess: () => message.info('中断请求已发送，最坏需等当前扫描点算完'),
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
    <n-page-header title="参数敏感性" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="router.push('/param-sensitivity/new')">
          + 新建参数扫描
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
