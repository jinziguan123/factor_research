<script setup lang="ts">
/**
 * 多因子合成创建页。
 * - 多个因子项（NSelect + 嵌入式参数表单 ParamsFormRenderer）；
 * - method 下拉：equal / ic_weighted / orthogonal_equal；
 * - 其它通用字段对齐 EvalCreate（pool / 日期 / n_groups / forward_periods）。
 *
 * 因子项：每行可增 / 删，最少 2 最多 8（schema 层会再挡一次）。
 * params：选中因子时自动用其 default_params 初始化，用户可在表单上调整；
 * 切换因子时 params 重置为新因子的 default_params。
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber, NDatePicker,
  NButton, NSpace, NInput, NDynamicInput, useMessage,
} from 'naive-ui'
import { useFactors } from '@/api/factors'
import type { Factor } from '@/api/factors'
import { useCreateComposition } from '@/api/compositions'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import ParamsFormRenderer from '@/components/forms/ParamsFormRenderer.vue'

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

// 每一行：{factor_id, params}。params 在选择因子时自动用 default_params 初始化。
interface FactorRow {
  factor_id: string
  params: Record<string, any>
}
const factorItems = ref<FactorRow[]>([
  { factor_id: '', params: {} },
  { factor_id: '', params: {} },
])

// factor_id → Factor 查找表，用于读 params_schema / default_params。
const factorById = computed<Record<string, Factor>>(() => {
  const m: Record<string, Factor> = {}
  for (const f of factors.value ?? []) m[f.factor_id] = f
  return m
})

/** 切换因子时：用新因子的 default_params 重置 params（避免把旧因子的参数串到新因子上）。 */
function onFactorChange(item: FactorRow, newId: string) {
  const f = factorById.value[newId]
  item.factor_id = newId
  item.params = f?.default_params ? { ...f.default_params } : {}
}

/** 取该因子的 params_schema；空 schema → null（前端模板用此判断"该因子无参数"）。 */
function getFactorSchema(fid: string): Record<string, any> | null {
  if (!fid) return null
  const f = factorById.value[fid]
  if (!f?.params_schema) return null
  if (Object.keys(f.params_schema).length === 0) return null
  return f.params_schema
}

const method = ref<'equal' | 'ic_weighted' | 'orthogonal_equal' | 'ml_lgb'>('equal')
const methodOptions = [
  { label: '等权 (equal) — 每个因子 z-score 后算术平均', value: 'equal' },
  { label: 'IC 加权 (ic_weighted) — 按全窗口 IC 自动加权', value: 'ic_weighted' },
  { label: '正交等权 (orthogonal_equal) — Gram-Schmidt 去共线后等权', value: 'orthogonal_equal' },
  { label: 'LightGBM 合成 (ml_lgb) — walk-forward 学非线性权重，耗时 3-10 分钟', value: 'ml_lgb' },
]

const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const forwardPeriodsText = ref('1, 5, 10')
const icWeightPeriod = ref(1)

const createRun = useCreateComposition()

function parseForwardPeriods(text: string): number[] {
  return text
    .split(/[,\s]+/)
    .map((s) => Number(s))
    .filter((n) => Number.isInteger(n) && n > 0)
}

async function handleSubmit() {
  if (!poolId.value) {
    message.warning('请选择股票池')
    return
  }
  if (!dateRange.value) {
    message.warning('请选择日期区间')
    return
  }

  const validRows = factorItems.value.filter((r) => r.factor_id)
  if (validRows.length < 2) {
    message.warning('至少选择 2 个因子（只有 1 个请走"评估记录"）')
    return
  }
  if (validRows.length > 8) {
    message.warning('最多 8 个因子（超过后相关性矩阵已不易阅读）')
    return
  }
  // 因子 id 去重就近提示。
  const ids = validRows.map((r) => r.factor_id)
  if (new Set(ids).size !== ids.length) {
    message.warning('因子存在重复，请去重')
    return
  }

  // 空 params dict → null，让后端用 default_params 填（与原协议兼容）；
  // 非空则原样下发。
  const factor_items = validRows.map((r) => ({
    factor_id: r.factor_id,
    params: r.params && Object.keys(r.params).length > 0 ? r.params : null,
  }))

  const forwardPeriods = parseForwardPeriods(forwardPeriodsText.value)
  if (forwardPeriods.length === 0) {
    message.warning('forward_periods 至少需要一个正整数')
    return
  }

  const startDate = new Date(dateRange.value[0]).toISOString().slice(0, 10)
  const endDate = new Date(dateRange.value[1]).toISOString().slice(0, 10)

  const body: Record<string, any> = {
    pool_id: poolId.value,
    start_date: startDate,
    end_date: endDate,
    method: method.value,
    factor_items,
    n_groups: nGroups.value,
    forward_periods: forwardPeriods,
    ic_weight_period: icWeightPeriod.value,
  }

  const result = await createRun.mutateAsync(body)
  message.success('合成任务已提交')
  router.push(`/compositions/${result.run_id}`)
}
</script>

<template>
  <div>
    <n-page-header title="创建多因子合成" @back="router.back()" style="margin-bottom: 16px" />

    <n-form label-placement="left" label-width="160px" style="max-width: 820px">
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

      <n-form-item label="合成方法" required>
        <n-select v-model:value="method" :options="methodOptions" style="width: 100%" />
      </n-form-item>

      <n-form-item label="因子列表" required>
        <n-dynamic-input
          v-model:value="factorItems"
          :min="2"
          :max="8"
          :on-create="() => ({ factor_id: '', params: {} })"
        >
          <template #default="{ value }">
            <n-space vertical :size="8" style="width: 100%">
              <n-select
                :value="value.factor_id"
                :options="factorOptions"
                :loading="factorsLoading"
                placeholder="选择因子"
                filterable
                style="width: 100%; max-width: 420px"
                @update:value="(v: string) => onFactorChange(value, v)"
              />
              <params-form-renderer
                v-if="getFactorSchema(value.factor_id)"
                :schema="getFactorSchema(value.factor_id)!"
                :model-value="value.params"
                @update:model-value="(p: Record<string, any>) => (value.params = p)"
              />
              <span
                v-else-if="value.factor_id"
                style="color: #999; font-size: 12px"
              >
                该因子无可调参数
              </span>
            </n-space>
          </template>
        </n-dynamic-input>
      </n-form-item>

      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 160px" />
      </n-form-item>

      <n-form-item label="前瞻期 (天)">
        <n-input
          v-model:value="forwardPeriodsText"
          placeholder="逗号分隔，如 1, 5, 10"
          style="width: 240px"
        />
      </n-form-item>

      <n-form-item v-if="method === 'ic_weighted'" label="IC 权重前瞻期">
        <n-input-number v-model:value="icWeightPeriod" :min="1" :max="20" style="width: 160px" />
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :loading="createRun.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交合成
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
