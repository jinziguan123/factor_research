<script setup lang="ts">
/**
 * 样本外验证（Walk-Forward 滚动回测）创建页。
 * 滚动训练窗 + 测试窗：每个测试窗只用其之前的数据计算因子，输出连续 OOS 权益曲线，
 * 并对比训练段/测试段 IC 衰减以识别过拟合。后端复用 POST /api/backtests/walk-forward。
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NButton, NAlert, useMessage,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateWalkForward } from '@/api/backtests'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import ParamsFormRenderer from '@/components/forms/ParamsFormRenderer.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const { data: factors, isLoading: factorsLoading } = useFactors()

const factorOptions = computed(() => {
  const groups: Record<string, { label: string; value: string }[]> = {}
  for (const f of factors.value ?? []) {
    const cat = f.category || 'custom'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push({ label: f.display_name, value: f.factor_id })
  }
  return Object.entries(groups).map(([cat, children]) => ({
    type: 'group' as const, label: cat, key: cat, children,
  }))
})

const selectedFactorId = ref(route.query.factor_id as string || '')
const selectedFactor = useFactor(selectedFactorId)
const factorParams = ref<Record<string, any>>({})
const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const trainDays = ref(252)
const testDays = ref(63)
const stepDays = ref<number | null>(null)
const autoHint = ref('')

function recommendWindows(range: [number, number]) {
  const totalCalDays = (range[1] - range[0]) / (1000 * 60 * 60 * 24)
  const totalTradeDays = Math.round(totalCalDays * 252 / 365)
  if (totalTradeDays < 120) {
    autoHint.value = `区间约 ${totalTradeDays} 个交易日，太短，无法切出有效窗口`
    return
  }
  // 目标：3~8 个不重叠窗口，训练窗 ≈ 1 年，测试窗 ≈ 训练窗的 1/4
  let train: number, test: number
  if (totalTradeDays <= 504) {
    // ≤2年：紧凑模式
    test = Math.max(21, Math.round(totalTradeDays * 0.12))
    train = Math.round(totalTradeDays * 0.5)
  } else if (totalTradeDays <= 1260) {
    // 2~5年：标准模式
    train = 252
    test = 63
  } else {
    // >5年：宽裕模式
    train = Math.min(504, Math.round(totalTradeDays * 0.35))
    test = Math.round(train / 4)
  }
  const nWindows = Math.floor((totalTradeDays - train) / test)
  trainDays.value = train
  testDays.value = test
  stepDays.value = null
  autoHint.value = `区间约 ${totalTradeDays} 个交易日，推荐训练 ${train} / 测试 ${test}，约 ${nWindows} 个窗口`
}

watch(dateRange, (v) => {
  if (v) recommendWindows(v)
  else autoHint.value = ''
})

watch(() => selectedFactor.data.value, (f) => {
  if (f?.default_params) factorParams.value = { ...f.default_params }
}, { immediate: true })

const createWf = useCreateWalkForward()

async function handleSubmit() {
  if (!selectedFactorId.value) { message.warning('请选择因子'); return }
  if (!poolId.value) { message.warning('请选择股票池'); return }
  if (!dateRange.value) { message.warning('请选择日期区间'); return }

  const startDate = new Date(dateRange.value[0]).toISOString().slice(0, 10)
  const endDate = new Date(dateRange.value[1]).toISOString().slice(0, 10)

  const body: Record<string, any> = {
    factor_id: selectedFactorId.value,
    params: factorParams.value,
    pool_id: poolId.value,
    start_date: startDate,
    end_date: endDate,
    n_groups: nGroups.value,
    train_days: trainDays.value,
    test_days: testDays.value,
  }
  if (stepDays.value) body.step_days = stepDays.value

  const result = await createWf.mutateAsync(body)
  message.success('样本外验证任务已提交')
  router.push(`/backtests/walk-forward/${result.run_id}`)
}
</script>

<template>
  <div>
    <n-page-header title="创建样本外验证 (Walk-Forward)" @back="router.back()" style="margin-bottom: 16px" />

    <n-alert type="info" :show-icon="true" style="max-width: 700px; margin-bottom: 16px">
      滚动训练窗 + 测试窗：每个测试窗只用其之前的数据计算因子（彻底消除前视），
      拼接成连续的样本外（OOS）权益曲线，并对比训练段/测试段 IC 衰减，识别过拟合。
    </n-alert>

    <n-form label-placement="left" label-width="130px" style="max-width: 700px">
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

      <n-form-item v-if="selectedFactor.data.value?.params_schema" label="因子参数">
        <params-form-renderer
          :schema="selectedFactor.data.value.params_schema"
          v-model="factorParams"
        />
      </n-form-item>

      <n-form-item label="股票池" required>
        <pool-selector v-model:value="poolId" style="width: 100%" />
      </n-form-item>

      <n-form-item label="日期区间" required>
        <n-date-picker v-model:value="dateRange" type="daterange" clearable style="width: 100%" />
      </n-form-item>

      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 160px" />
      </n-form-item>

      <n-form-item v-if="autoHint" label=" ">
        <span style="color: #18a058; font-size: 12px">{{ autoHint }}</span>
      </n-form-item>

      <n-form-item label="训练窗(交易日)">
        <n-input-number v-model:value="trainDays" :min="60" :max="1008" style="width: 160px" />
      </n-form-item>

      <n-form-item label="测试窗(交易日)">
        <n-input-number v-model:value="testDays" :min="20" :max="252" style="width: 160px" />
      </n-form-item>

      <n-form-item label="滑窗步长(可选)">
        <n-input-number
          v-model:value="stepDays"
          :min="20" :max="252"
          placeholder="默认=测试窗"
          clearable
          style="width: 160px"
        />
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :loading="createWf.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交验证
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
