<script setup lang="ts">
/**
 * 股票池列表页
 * NDataTable 展示，支持新建/查看/删除
 */
import { h } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPageHeader, NPopconfirm,
} from 'naive-ui'
import { usePools, useDeletePool } from '@/api/pools'
import type { Pool } from '@/api/pools'
import type { DataTableColumns } from 'naive-ui'

const router = useRouter()
const { data: pools, isLoading } = usePools()
const deleteMut = useDeletePool()

const columns: DataTableColumns<Pool> = [
  { title: '名称', key: 'pool_name', width: 180 },
  { title: '描述', key: 'description', ellipsis: { tooltip: true } },
  {
    title: '股票数',
    key: 'symbols_count',
    width: 100,
    // 列表接口返回 symbols_count（后端 LEFT JOIN 聚合），
    // 详情接口才有 symbols 数组——两个字段都兼容下，列表页以 symbols_count 为主。
    render: (row) => h(
      'span', {},
      String(row.symbols_count ?? row.symbols?.length ?? 0),
    ),
  },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作',
    key: 'actions',
    width: 180,
    render: (row) =>
      h(NSpace, {}, {
        default: () => [
          h(NButton, {
            size: 'small',
            quaternary: true,
            type: 'primary',
            onClick: () => router.push(`/pools/${row.pool_id}`),
          }, { default: () => '编辑' }),
          h(NPopconfirm, {
            onPositiveClick: () => deleteMut.mutate(row.pool_id),
          }, {
            trigger: () => h(NButton, { size: 'small', quaternary: true, type: 'error' }, { default: () => '删除' }),
            default: () => `确认删除「${row.pool_name}」？`,
          }),
        ],
      }),
  },
]
</script>

<template>
  <div>
    <n-page-header title="股票池管理" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="router.push('/pools/new')">
          新建股票池
        </n-button>
      </template>
    </n-page-header>

    <n-data-table
      :columns="columns"
      :data="pools ?? []"
      :loading="isLoading"
      :bordered="false"
      :single-line="false"
      :row-key="(row: any) => row.pool_id"
    />
  </div>
</template>
