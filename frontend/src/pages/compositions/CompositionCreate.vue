<script setup lang="ts">
/**
 * 多因子合成创建页。
 * - 多个因子项（NSelect + 可选参数 JSON 字符串编辑）；
 * - method 下拉：equal / ic_weighted / orthogonal_equal；
 * - 其它通用字段对齐 EvalCreate（pool / 日期 / n_groups / forward_periods）。
 *
 * 因子项：每行可增 / 删，最少 2 最多 8（schema 层会再挡一次）。
 * params：以字符串 JSON 表单存储，默认空 → 用因子 default_params。
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber, NDatePicker,
  NButton, NSpace, NInput, NDynamicInput, useMessage,
} from 'naive-ui'
import { useFactors } from '@/api/factors'
import { useCreateComposition } from '@/api/compositions'
import PoolSelector from '@/components/forms/PoolSelector.vue'

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

// 每一行：{factor_id, paramsText}（paramsText 是 JSON 字符串，提交时解析）
interface FactorRow {
  factor_id: string
  paramsText: string
}
const factorItems = ref<FactorRow[]>([
  { factor_id: '', paramsText: '' },
  { factor_id: '', paramsText: '' },
])

const method = ref<'equal' | 'ic_weighted' | 'orthogonal_equal'>('equal')
const methodOptions = [
  { label: '等权 (equal) — 每个因子 z-score 后算术平均', value: 'equal' },
  { label: 'IC 加权 (ic_weighted) — 按全窗口 IC 自动加权', value: 'ic_weighted' },
  { label: '正交等权 (orthogonal_equal) — Gram-Schmidt 去共线后等权', value: 'orthogonal_equal' },
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

  // params 字符串 → dict；空字符串 → null，由后端用 default_params 填。
  const factor_items: any[] = []
  for (const r of validRows) {
    let params: Record<string, any> | null = null
    if (r.paramsText.trim()) {
      try {
        params = JSON.parse(r.paramsText)
      } catch (e) {
        message.error(`因子 ${r.factor_id} 的参数不是合法 JSON`)
        return
      }
    }
    factor_items.push({ factor_id: r.factor_id, params })
  }

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
          :on-create="() => ({ factor_id: '', paramsText: '' })"
        >
          <template #default="{ value }">
            <n-space style="width: 100%" :wrap="false">
              <n-select
                v-model:value="value.factor_id"
                :options="factorOptions"
                :loading="factorsLoading"
                placeholder="选择因子"
                filterable
                style="width: 260px"
              />
              <n-input
                v-model:value="value.paramsText"
                placeholder='可选：因子参数 JSON（如 {"n": 20}），空则用默认参数'
                style="width: 400px"
              />
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
