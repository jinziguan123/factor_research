<script setup lang="ts">
/**
 * 评估创建页
 * 因子选择 → 动态参数 → 股票池 → 日期区间 → forward_periods → n_groups → 提交
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NDynamicTags, NButton, NSpace, useMessage, NSpin,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateEval } from '@/api/evals'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import ParamsFormRenderer from '@/components/forms/ParamsFormRenderer.vue'
import type { Factor } from '@/api/factors'

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
const forwardPeriods = ref<string[]>(['1', '5', '10'])
const nGroups = ref(5)

// 当选择因子变化时，用默认参数初始化
watch(() => selectedFactor.data.value, (f) => {
  if (f?.default_params) {
    factorParams.value = { ...f.default_params }
  }
}, { immediate: true })

const createEval = useCreateEval()

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

  // 日期转为 YYYY-MM-DD
  const startDate = new Date(dateRange.value[0]).toISOString().slice(0, 10)
  const endDate = new Date(dateRange.value[1]).toISOString().slice(0, 10)

  const body = {
    factor_id: selectedFactorId.value,
    params: factorParams.value,
    pool_id: poolId.value,
    start_date: startDate,
    end_date: endDate,
    forward_periods: forwardPeriods.value.map(Number).filter(n => n > 0),
    n_groups: nGroups.value,
  }

  const result = await createEval.mutateAsync(body)
  message.success('评估任务已提交')
  router.push(`/evals/${result.run_id}`)
}
</script>

<template>
  <div>
    <n-page-header title="创建评估" @back="router.back()" style="margin-bottom: 16px" />

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

      <!-- Forward Periods -->
      <n-form-item label="前瞻期数">
        <n-dynamic-tags v-model:value="forwardPeriods" />
      </n-form-item>

      <!-- 分组数 -->
      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 120px" />
      </n-form-item>

      <!-- 提交按钮 -->
      <n-form-item>
        <n-button
          type="primary"
          :loading="createEval.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交评估
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
