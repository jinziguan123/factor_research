<script setup lang="ts">
/**
 * 成本敏感性分析创建页。
 * 和 BacktestCreate 同结构，区别：cost_bps 换成 NDynamicTags 的 cost_bps_list
 * （默认 0 / 3 / 5 / 10 / 20，代表"无成本 / 券商典型 / 中等 / 偏高 / 极端"五档）。
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NDynamicTags, NButton, NSwitch, NTooltip, useMessage,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateCostSensitivity } from '@/api/cost_sensitivity'
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
const initCash = ref(1e7)
const filterPriceLimit = ref(false)

// cost_bps_list：NDynamicTags 操作字符串数组，提交时再转 number。
// 默认 5 个点覆盖"无 / 典型 / 中 / 偏高 / 极端"四档，用户可任意增删。
const costBpsList = ref<string[]>(['0', '3', '5', '10', '20'])

const positionOptions = [
  { label: '做多头部 (top)', value: 'top' },
  { label: '多空对冲 (long_short)', value: 'long_short' },
]

// 因子切换：默认参数回填
watch(() => selectedFactor.data.value, (f) => {
  if (f?.default_params) {
    factorParams.value = { ...f.default_params }
  }
}, { immediate: true })

const createRun = useCreateCostSensitivity()

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

  // 成本列表转换 + 前端自校验：长度 2..20、每个 >= 0 且 <= 200bp、至少 2 个不同值。
  // 后端 schema 也会再拒一次，但就近提示体验更好。
  const bpsNums = costBpsList.value
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n))
  const unique = Array.from(new Set(bpsNums)).sort((a, b) => a - b)
  if (unique.length < 2) {
    message.warning('请至少输入 2 个不同的成本点（单次成本回测请走回测页）')
    return
  }
  if (unique.length > 20) {
    message.warning('成本点数过多（>20），曲线已失去分辨意义')
    return
  }
  if (unique.some((v) => v < 0 || v > 200)) {
    message.warning('成本点必须在 [0, 200] 基点内')
    return
  }

  const startDate = new Date(dateRange.value[0]).toISOString().slice(0, 10)
  const endDate = new Date(dateRange.value[1]).toISOString().slice(0, 10)

  const body: Record<string, any> = {
    factor_id: selectedFactorId.value,
    params: factorParams.value,
    pool_id: poolId.value,
    start_date: startDate,
    end_date: endDate,
    n_groups: nGroups.value,
    rebalance_period: rebalancePeriod.value,
    position: position.value,
    init_cash: initCash.value,
    cost_bps_list: unique,
    filter_price_limit: filterPriceLimit.value,
  }

  const result = await createRun.mutateAsync(body)
  message.success('敏感性分析任务已提交')
  router.push(`/cost-sensitivity/${result.run_id}`)
}
</script>

<template>
  <div>
    <n-page-header title="创建成本敏感性分析" @back="router.back()" style="margin-bottom: 16px" />

    <n-form label-placement="left" label-width="140px" style="max-width: 720px">
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
        <n-date-picker
          v-model:value="dateRange"
          type="daterange"
          clearable
          style="width: 100%"
        />
      </n-form-item>

      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 160px" />
      </n-form-item>

      <n-form-item label="调仓周期(天)">
        <n-input-number v-model:value="rebalancePeriod" :min="1" :max="60" style="width: 160px" />
      </n-form-item>

      <n-form-item label="持仓方式">
        <n-select
          v-model:value="position"
          :options="positionOptions"
          style="width: 240px"
        />
      </n-form-item>

      <n-form-item label="初始资金">
        <n-input-number v-model:value="initCash" :min="10000" :step="1000000" style="width: 200px" />
      </n-form-item>

      <n-form-item label="成本点 (bps)">
        <n-dynamic-tags v-model:value="costBpsList" />
      </n-form-item>

      <n-form-item>
        <template #label>
          <n-tooltip>
            <template #trigger>
              <span style="cursor: help; border-bottom: 1px dashed #999">
                涨跌停过滤
              </span>
            </template>
            按 |pct_change| ≥ 0.097 剔除当日触板票（多空两侧都剔），
            更接近"明日不可成交"的真实约束。<br/>
            注意：未区分主板 / 创业板（20%）/ ST（5%），口径偏保守。
          </n-tooltip>
        </template>
        <n-switch v-model:value="filterPriceLimit" />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          {{ filterPriceLimit ? '已开启' : '已关闭' }}
        </span>
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :loading="createRun.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交分析
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
