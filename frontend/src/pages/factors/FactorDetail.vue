<script setup lang="ts">
/**
 * 因子详情页
 * 展示因子信息 + 历史评估列表 + 操作按钮
 */
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NDescriptions, NDescriptionsItem, NTag, NSpace,
  NButton, NDataTable, NSpin,
} from 'naive-ui'
import { useFactor } from '@/api/factors'
import { useEvals } from '@/api/evals'
import type { EvalRun } from '@/api/evals'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import type { DataTableColumns } from 'naive-ui'

const route = useRoute()
const router = useRouter()

const factorId = computed(() => route.params.factorId as string)
const { data: factor, isLoading } = useFactor(factorId)

// 历史评估列表
const evalParams = computed(() => ({ factor_id: factorId.value }))
const { data: evals, isLoading: evalsLoading } = useEvals(evalParams)

const evalColumns: DataTableColumns<EvalRun> = [
  { title: 'Run ID', key: 'run_id', width: 200, ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => h(StatusBadge, { status: row.status }),
  },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) => h(NButton, {
      size: 'small',
      quaternary: true,
      type: 'primary',
      onClick: () => router.push(`/evals/${row.run_id}`),
    }, { default: () => '查看' }),
  },
]
</script>

<template>
  <div>
    <n-page-header
      :title="factor?.display_name ?? '加载中...'"
      @back="router.push('/factors')"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <n-space>
          <n-button
            type="primary"
            @click="router.push(`/evals/new?factor_id=${factorId}`)"
          >
            新评估
          </n-button>
          <n-button
            secondary
            @click="router.push(`/backtests/new?factor_id=${factorId}`)"
          >
            新回测
          </n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <n-descriptions v-if="factor" bordered :column="2" label-placement="left" style="margin-bottom: 24px">
        <n-descriptions-item label="因子 ID">{{ factor.factor_id }}</n-descriptions-item>
        <n-descriptions-item label="分类">{{ factor.category }}</n-descriptions-item>
        <n-descriptions-item label="描述" :span="2">{{ factor.description || '-' }}</n-descriptions-item>
        <n-descriptions-item label="支持频率">
          <n-space>
            <n-tag v-for="f in factor.supported_freqs" :key="f" size="small">{{ f }}</n-tag>
          </n-space>
        </n-descriptions-item>
        <n-descriptions-item label="版本">v{{ factor.version ?? 1 }}</n-descriptions-item>
        <n-descriptions-item label="默认参数" :span="2">
          <code style="font-size: 12px">{{ JSON.stringify(factor.default_params) }}</code>
        </n-descriptions-item>
        <n-descriptions-item label="参数 Schema" :span="2">
          <code style="font-size: 12px">{{ JSON.stringify(factor.params_schema) }}</code>
        </n-descriptions-item>
      </n-descriptions>
    </n-spin>

    <!-- 历史评估列表 -->
    <h3 style="margin-bottom: 12px">历史评估</h3>
    <n-data-table
      :columns="evalColumns"
      :data="evals ?? []"
      :loading="evalsLoading"
      :bordered="false"
      :single-line="false"
      :row-key="(row: any) => row.run_id"
    />
  </div>
</template>
