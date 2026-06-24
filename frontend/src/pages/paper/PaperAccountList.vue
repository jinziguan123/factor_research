<script setup lang="ts">
import { h } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDataTable, NSpace, NPopconfirm, NTag, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  usePaperAccounts, useDeletePaperAccount, type PaperAccount,
} from '@/api/paper_trading'

const router = useRouter()
const message = useMessage()
const { data: accounts, isLoading } = usePaperAccounts()
const delMut = useDeletePaperAccount()

const fmtWan = (v: number) => (v / 1e4).toFixed(2) + ' 万'

async function handleDelete(id: string) {
  await delMut.mutateAsync(id)
  message.success('已删除')
}

const columns: DataTableColumns<PaperAccount> = [
  {
    title: '名称',
    key: 'name',
    render: (row) =>
      h(
        NButton,
        {
          text: true,
          type: 'primary',
          onClick: () => router.push(`/paper-accounts/${row.account_id}`),
        },
        { default: () => row.name },
      ),
  },
  { title: '合成方法', key: 'method', width: 120 },
  { title: '股票池', key: 'pool_id', width: 90 },
  { title: '初始资金', key: 'init_cash', width: 120, render: (r) => fmtWan(r.init_cash) },
  { title: '现金', key: 'cash', width: 120, render: (r) => fmtWan(r.cash) },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (r) =>
      h(
        NTag,
        { type: r.status === 'active' ? 'success' : 'default', size: 'small', round: true },
        { default: () => r.status },
      ),
  },
  {
    title: '最近调仓',
    key: 'last_rebalance_at',
    width: 200,
    render: (r) => r.last_rebalance_at ?? '—',
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row) =>
      h(
        NPopconfirm,
        { onPositiveClick: () => handleDelete(row.account_id) },
        {
          trigger: () =>
            h(NButton, { text: true, type: 'error', size: 'small' }, { default: () => '删除' }),
          default: () => '确认删除该模拟盘？持仓 / 净值 / 成交都会一并清除。',
        },
      ),
  },
]
</script>

<template>
  <div>
    <n-space justify="space-between" align="center" style="margin-bottom: 16px">
      <h2 style="margin: 0">模拟盘</h2>
      <n-button type="primary" @click="router.push('/paper-accounts/new')">
        新建模拟盘
      </n-button>
    </n-space>
    <n-data-table
      :columns="columns"
      :data="accounts ?? []"
      :loading="isLoading"
      :bordered="false"
    />
  </div>
</template>
