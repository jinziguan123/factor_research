<script setup lang="ts">
/**
 * 多因子合成记录列表。
 * 过滤：池 / method / status / limit；操作：查看、删除。
 */
import { computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPageHeader, NPopconfirm,
  NSelect, NInputNumber, NTag, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useCompositionRuns, useDeleteComposition } from '@/api/compositions'
import type { CompositionRun } from '@/api/compositions'
import { usePoolNameMap, usePools } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const router = useRouter()
const message = useMessage()

const poolFilter = ref<number | null>(null)
const methodFilter = ref<string | null>(null)
const statusFilter = ref<string | null>(null)
const limit = ref<number>(50)

const listParams = computed<Record<string, any>>(() => ({
  pool_id: poolFilter.value ?? undefined,
  method: methodFilter.value || undefined,
  status: statusFilter.value || undefined,
  limit: limit.value,
}))

const { data: runs, isLoading } = useCompositionRuns(listParams)
const deleteMut = useDeleteComposition()

const { data: pools } = usePools()
const poolOptions = computed(() =>
  (pools.value ?? []).map((p: any) => ({ label: p.name, value: p.pool_id })),
)
const methodOptions = [
  { label: '等权 (equal)', value: 'equal' },
  { label: 'IC 加权 (ic_weighted)', value: 'ic_weighted' },
  { label: '正交等权 (orthogonal_equal)', value: 'orthogonal_equal' },
]
const statusOptions = [
  { label: '等待中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
]

const { lookup: lookupPoolName } = usePoolNameMap()

function fmtNum(v: any, digits = 3): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}

const columns: DataTableColumns<CompositionRun> = [
  {
    title: 'Run ID',
    key: 'run_id',
    width: 110,
    render: (row) => h('code', { style: 'font-size: 12px' }, row.run_id.slice(0, 8)),
  },
  {
    title: '因子',
    key: 'factor_items',
    width: 280,
    render: (row) =>
      h(NSpace, { size: 4 }, {
        default: () =>
          (row.factor_items || []).map((it: any) =>
            h(NTag, { size: 'small', type: 'info', bordered: false }, { default: () => it.factor_id }),
          ),
      }),
  },
  {
    title: '方法',
    key: 'method',
    width: 150,
    render: (row) => {
      const label = row.method === 'equal'
        ? '等权'
        : row.method === 'ic_weighted'
          ? 'IC 加权'
          : '正交等权'
      return h(NTag, { size: 'small', bordered: false }, { default: () => label })
    },
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
    width: 160,
    ellipsis: { tooltip: true },
    render: (row) => h(
      NTag,
      { size: 'small', bordered: false },
      { default: () => lookupPoolName(row.pool_id) },
    ),
  },
  { title: 'IC 均值', key: 'ic_mean', width: 110, render: (r) => fmtNum(r.ic_mean, 4) },
  { title: 'IC_IR', key: 'ic_ir', width: 100, render: (r) => fmtNum(r.ic_ir) },
  { title: '多空 Sharpe', key: 'long_short_sharpe', width: 120, render: (r) => fmtNum(r.long_short_sharpe) },
  {
    title: '日期',
    key: 'date_range',
    width: 190,
    render: (row) => `${row.start_date} ~ ${row.end_date}`,
  },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作',
    key: 'actions',
    width: 160,
    render: (row) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(NButton, {
            size: 'small',
            quaternary: true,
            type: 'primary',
            onClick: () => router.push(`/compositions/${row.run_id}`),
          }, { default: () => '查看' }),
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
        ],
      }),
  },
]
</script>

<template>
  <div>
    <n-page-header title="多因子合成" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="router.push('/compositions/new')">
          + 新建合成
        </n-button>
      </template>
    </n-page-header>

    <n-space style="margin-bottom: 16px" align="center">
      <n-select
        v-model:value="poolFilter"
        :options="poolOptions"
        placeholder="股票池"
        clearable
        filterable
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
