<script setup lang="ts">
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NGrid, NGi, NStatistic, NDataTable,
  NButton, NSpin, NTag, NEmpty, NSpace, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  usePaperAccount, useRebalance,
  type PaperPosition, type PaperTrade,
} from '@/api/paper_trading'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const accountId = computed(() => route.params.accountId as string)
const { data, isLoading } = usePaperAccount(accountId)
const rebalanceMut = useRebalance()

const account = computed(() => data.value?.account)
const positions = computed(() => data.value?.positions ?? [])
const navSeries = computed(() => data.value?.nav_series ?? [])
const trades = computed(() => data.value?.trades ?? [])

const equity = computed(() => ({
  dates: navSeries.value.map(p => p.ts),
  values: navSeries.value.map(p => p.nav),
}))

const fmtWan = (v: number) => (v / 1e4).toFixed(2) + ' 万'
const fmtMoney = (v: number) => v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

async function handleRebalance() {
  try {
    await rebalanceMut.mutateAsync(accountId.value)
    message.success('调仓完成')
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '调仓失败')
  }
}

const posColumns: DataTableColumns<PaperPosition> = [
  { title: '股票代码', key: 'symbol', width: 120 },
  { title: '持仓数量', key: 'qty', width: 120 },
  { title: '成本均价', key: 'avg_price', width: 120, render: r => fmtMoney(r.avg_price) },
]

const tradeColumns: DataTableColumns<PaperTrade> = [
  { title: '时间', key: 'ts', width: 180 },
  { title: '股票代码', key: 'symbol', width: 120 },
  {
    title: '方向',
    key: 'side',
    width: 80,
    render: r => h(NTag, { type: r.side === 'buy' ? 'success' : 'error', size: 'small' }, { default: () => r.side === 'buy' ? '买入' : '卖出' }),
  },
  { title: '数量', key: 'qty', width: 100 },
  { title: '价格', key: 'price', width: 120, render: r => fmtMoney(r.price) },
  { title: '手续费', key: 'fee', width: 100, render: r => fmtMoney(r.fee) },
]
</script>

<template>
  <div>
    <n-page-header
      title="模拟盘详情"
      @back="router.push('/paper-accounts')"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <n-space>
          <n-tag v-if="account" :type="account.status === 'active' ? 'success' : 'default'" size="small" round>
            {{ account.status }}
          </n-tag>
          <n-button
            type="primary"
            :loading="rebalanceMut.isPending.value"
            @click="handleRebalance"
          >
            执行调仓
          </n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <template v-if="account">
        <n-grid :cols="4" :x-gap="12" style="margin-bottom: 16px">
          <n-gi>
            <n-card size="small">
              <n-statistic label="账户名称" :value="account.name" />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic label="初始资金" :value="fmtWan(account.init_cash)" />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic label="当前现金" :value="fmtWan(account.cash)" />
            </n-card>
          </n-gi>
          <n-gi>
            <n-card size="small">
              <n-statistic label="最近调仓" :value="account.last_rebalance_at ?? '—'" />
            </n-card>
          </n-gi>
        </n-grid>

        <n-card title="净值曲线" size="small" style="margin-bottom: 16px">
          <equity-curve-chart v-if="equity.dates.length" :equity="equity" />
          <n-empty v-else description="暂无净值数据，请先执行一次调仓" />
        </n-card>

        <n-card title="当前持仓" size="small" style="margin-bottom: 16px">
          <n-data-table
            v-if="positions.length"
            :columns="posColumns"
            :data="positions"
            :bordered="false"
            size="small"
          />
          <n-empty v-else description="暂无持仓" />
        </n-card>

        <n-card title="成交记录" size="small">
          <n-data-table
            v-if="trades.length"
            :columns="tradeColumns"
            :data="trades"
            :bordered="false"
            size="small"
            :max-height="400"
          />
          <n-empty v-else description="暂无成交记录" />
        </n-card>
      </template>

      <n-empty v-else-if="!isLoading" description="账户不存在" />
    </n-spin>
  </div>
</template>
