<script setup lang="ts">
/**
 * 回测创建页
 * 因子选择 → 动态参数 → 股票池 → 日期区间 → 回测参数 → 提交
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NButton, useMessage, NSpin,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateBacktest } from '@/api/backtests'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import ParamsFormRenderer from '@/components/forms/ParamsFormRenderer.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

// 因子列表（按分类分组做 NSelect option group）
const { data: factors, isLoading: factorsLoading } = useFactors()

const factorOptions = computed(() => {
  const groups: Record<string, { label: string; value: string }[]> = {}
  for (const f of factors.value ?? []) {
    const cat = f.category || 'custom'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push({ label: f.display_name, value: f.factor_id })
  }
  return Object.entries(groups).map(([cat, children]) => ({
    type: 'group' as const,
    label: cat,
    key: cat,
    children,
  }))
})

// 表单状态
const selectedFactorId = ref(route.query.factor_id as string || '')
const selectedFactor = useFactor(selectedFactorId)
const factorParams = ref<Record<string, any>>({})
const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const rebalancePeriod = ref(1)
const position = ref('top')
const costBps = ref(3)
const initCash = ref(1e7)

const positionOptions = [
  { label: '做多头部 (top)', value: 'top' },
  { label: '多空对冲 (long_short)', value: 'long_short' },
]

// 当选择因子变化时，用默认参数初始化
watch(() => selectedFactor.data.value, (f) => {
  if (f?.default_params) {
    factorParams.value = { ...f.default_params }
  }
}, { immediate: true })

const createBacktest = useCreateBacktest()

async function handleSubmit() {
  if (!selectedFactorId.value) {
    message.warning('请选择因子')
    return
  }
  if (!poolId.value) {
    message.warning('请选择股票池')
    return
  }
  if (!dateRange.value) {
    message.warning('请选择日期区间')
    return
  }

  const startDate = new Date(dateRange.value[0]).toISOString().slice(0, 10)
  const endDate = new Date(dateRange.value[1]).toISOString().slice(0, 10)

  const body = {
    factor_id: selectedFactorId.value,
    params: factorParams.value,
    pool_id: poolId.value,
    start_date: startDate,
    end_date: endDate,
    n_groups: nGroups.value,
    rebalance_period: rebalancePeriod.value,
    position: position.value,
    cost_bps: costBps.value,
    init_cash: initCash.value,
  }

  const result = await createBacktest.mutateAsync(body)
  message.success('回测任务已提交')
  router.push(`/backtests/${result.run_id}`)
}
</script>

<template>
  <div>
    <n-page-header title="创建回测" @back="router.back()" style="margin-bottom: 16px" />

    <n-form label-placement="left" label-width="120px" style="max-width: 700px">
      <!-- 因子选择 -->
      <n-form-item label="因子" required>
        <n-select
          v-model:value="selectedFactorId"
          :options="factorOptions"
          :loading="factorsLoading"
          placeholder="选择因子"
          filterable
          style="width: 100%"
        />
      </n-form-item>

      <!-- 动态参数表单 -->
      <n-form-item v-if="selectedFactor.data.value?.params_schema" label="因子参数">
        <params-form-renderer
          :schema="selectedFactor.data.value.params_schema"
          v-model="factorParams"
        />
      </n-form-item>

      <!-- 股票池 -->
      <n-form-item label="股票池" required>
        <pool-selector v-model:value="poolId" style="width: 100%" />
      </n-form-item>

      <!-- 日期区间 -->
      <n-form-item label="日期区间" required>
        <n-date-picker
          v-model:value="dateRange"
          type="daterange"
          clearable
          style="width: 100%"
        />
      </n-form-item>

      <!-- 分组数 -->
      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 160px" />
      </n-form-item>

      <!-- 调仓周期 -->
      <n-form-item label="调仓周期(天)">
        <n-input-number v-model:value="rebalancePeriod" :min="1" :max="60" style="width: 160px" />
      </n-form-item>

      <!-- 持仓方式 -->
      <n-form-item label="持仓方式">
        <n-select
          v-model:value="position"
          :options="positionOptions"
          style="width: 240px"
        />
      </n-form-item>

      <!-- 交易成本 -->
      <n-form-item label="交易成本(bps)">
        <n-input-number v-model:value="costBps" :min="0" :max="100" :precision="1" style="width: 160px" />
      </n-form-item>

      <!-- 初始资金 -->
      <n-form-item label="初始资金">
        <n-input-number v-model:value="initCash" :min="10000" :step="1000000" style="width: 200px" />
      </n-form-item>

      <!-- 提交按钮 -->
      <n-form-item>
        <n-button
          type="primary"
          :loading="createBacktest.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交回测
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
