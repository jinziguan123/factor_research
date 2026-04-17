<script setup lang="ts">
/**
 * 工作台首页
 * 统计卡片 + 最近评估/回测表格 + 快捷入口
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NGrid, NGridItem, NCard, NStatistic, NDataTable, NButton, NSpace,
} from 'naive-ui'
import { useFactors } from '@/api/factors'
import { usePools } from '@/api/pools'
import { useEvals } from '@/api/evals'
import { useBacktests } from '@/api/backtests'
import type { DataTableColumns } from 'naive-ui'

const router = useRouter()

const { data: factors } = useFactors()
const { data: pools } = usePools()
const { data: evals } = useEvals({ limit: 5 })
const { data: backtests } = useBacktests({ limit: 5 })

const factorCount = computed(() => factors.value?.length ?? 0)
const poolCount = computed(() => pools.value?.length ?? 0)
const evalCount = computed(() => evals.value?.length ?? 0)
const btCount = computed(() => backtests.value?.length ?? 0)

const evalColumns: DataTableColumns = [
  { title: '因子', key: 'factor_id', width: 160 },
  { title: '状态', key: 'status', width: 80 },
  { title: '创建时间', key: 'created_at', width: 180 },
]

const btColumns: DataTableColumns = [
  { title: '因子', key: 'factor_id', width: 160 },
  { title: '状态', key: 'status', width: 80 },
  { title: '创建时间', key: 'created_at', width: 180 },
]
</script>

<template>
  <div>
    <h2 style="margin-bottom: 20px">工作台</h2>

    <!-- 统计卡片 -->
    <n-grid :cols="4" :x-gap="16" :y-gap="16" style="margin-bottom: 24px">
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="已注册因子" :value="factorCount" />
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="股票池" :value="poolCount" />
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="最近评估" :value="evalCount" />
        </n-card>
      </n-grid-item>
      <n-grid-item>
        <n-card size="small">
          <n-statistic label="最近回测" :value="btCount" />
        </n-card>
      </n-grid-item>
    </n-grid>

    <!-- 快捷入口 -->
    <n-space style="margin-bottom: 24px">
      <n-button type="primary" @click="router.push('/evals/new')">新建评估</n-button>
      <n-button type="primary" @click="router.push('/backtests/new')">新建回测</n-button>
      <n-button secondary @click="router.push('/factors')">因子列表</n-button>
      <n-button secondary @click="router.push('/pools')">股票池</n-button>
    </n-space>

    <n-grid :cols="2" :x-gap="16" :y-gap="16">
      <!-- 最近评估 -->
      <n-grid-item>
        <n-card title="最近评估" size="small">
          <n-data-table
            :columns="evalColumns"
            :data="evals ?? []"
            :bordered="false"
            size="small"
            :pagination="false"
            :row-props="(row: any) => ({ style: 'cursor: pointer', onClick: () => router.push(`/evals/${row.run_id}`) })"
          />
        </n-card>
      </n-grid-item>

      <!-- 最近回测 -->
      <n-grid-item>
        <n-card title="最近回测" size="small">
          <n-data-table
            :columns="btColumns"
            :data="backtests ?? []"
            :bordered="false"
            size="small"
            :pagination="false"
            :row-props="(row: any) => ({ style: 'cursor: pointer', onClick: () => router.push(`/backtests/${row.run_id}`) })"
          />
        </n-card>
      </n-grid-item>
    </n-grid>
  </div>
</template>
