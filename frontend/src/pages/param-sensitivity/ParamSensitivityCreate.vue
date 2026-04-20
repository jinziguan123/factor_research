<script setup lang="ts">
/**
 * 参数敏感性扫描创建页。
 *
 * 表单部分从旧 Preview 页搬过来：参数下拉自动来自 factor.params_schema；values 默认
 * 由 schema.min/max 生成"等距 7 点"，用户可增删（NDynamicTags）。提交成功后跳转到
 * Detail 页轮询（结果异步写入 points_json）。
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NDynamicTags, NButton, NAlert, useMessage,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateParamSensitivity } from '@/api/param_sensitivity'
import PoolSelector from '@/components/forms/PoolSelector.vue'

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

const paramOptions = computed(() => {
  const schema = selectedFactor.data.value?.params_schema ?? {}
  return Object.entries(schema).map(([key, entry]: [string, any]) => ({
    label: `${key}${entry?.desc ? `（${entry.desc}）` : ''}`,
    value: key,
  }))
})
const selectedParam = ref<string | null>(null)

// 默认值列表：从 schema 的 min/max/default 推 7 个等距扫描点，覆盖 default 的两侧。
// 没有 min/max 时退化成 [default*0.5, default*0.75, default, default*1.25, ...]。
function suggestValues(schemaEntry: any, defaultValue: any): string[] {
  if (schemaEntry && typeof schemaEntry.min === 'number' && typeof schemaEntry.max === 'number') {
    const { min, max } = schemaEntry
    const n = 7
    const step = (max - min) / (n - 1)
    return Array.from({ length: n }, (_, i) => {
      const v = min + step * i
      return schemaEntry.type === 'int' ? String(Math.round(v)) : v.toFixed(3)
    })
  }
  if (typeof defaultValue === 'number') {
    const d = defaultValue
    const mul = [0.5, 0.75, 1, 1.25, 1.5]
    return mul.map((m) =>
      (schemaEntry?.type === 'int') ? String(Math.max(1, Math.round(d * m))) : (d * m).toFixed(3),
    )
  }
  return ['10', '20', '30', '40', '50']
}

const valuesInput = ref<string[]>([])
// 切因子 / 切参数时自动填充扫描点，用户后续手改不会被覆盖（仅在 param 切换那一刻重置）。
watch([() => selectedParam.value, () => selectedFactor.data.value], ([paramKey, factor]) => {
  if (!paramKey || !factor) return
  const schema = factor.params_schema ?? {}
  const entry = schema[paramKey]
  const def = factor.default_params?.[paramKey]
  valuesInput.value = suggestValues(entry, def)
})

const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const forwardPeriodsInput = ref<string[]>(['1', '5', '10'])

const createRun = useCreateParamSensitivity()

async function handleSubmit() {
  if (!selectedFactorId.value) return message.warning('请选择因子')
  if (!selectedParam.value) return message.warning('请选择要扫的参数')
  if (!poolId.value) return message.warning('请选择股票池')
  if (!dateRange.value) return message.warning('请选择日期区间')

  // 去重 + 升序 + 有限性过滤；后端也会挡，就近提示体验更好。
  const vals = Array.from(new Set(
    valuesInput.value.map((s) => Number(s)).filter((x) => Number.isFinite(x)),
  )).sort((a, b) => a - b)
  if (vals.length < 2) return message.warning('至少需要 2 个不同的扫描点')
  if (vals.length > 15) return message.warning('扫描点过多（>15），建议控制在 5-10 个')

  const fwdPeriods = forwardPeriodsInput.value
    .map((s) => Number(s)).filter((x) => Number.isFinite(x) && x > 0)
  if (fwdPeriods.length === 0) return message.warning('请填写至少 1 个前瞻期')

  const body = {
    factor_id: selectedFactorId.value,
    param_name: selectedParam.value,
    values: vals,
    pool_id: poolId.value,
    start_date: new Date(dateRange.value[0]).toISOString().slice(0, 10),
    end_date: new Date(dateRange.value[1]).toISOString().slice(0, 10),
    n_groups: nGroups.value,
    forward_periods: fwdPeriods,
  }

  try {
    const res = await createRun.mutateAsync(body)
    message.success('扫描任务已提交')
    router.push(`/param-sensitivity/${res.run_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '提交失败')
  }
}
</script>

<template>
  <div>
    <n-page-header title="创建参数敏感性扫描" @back="router.back()" style="margin-bottom: 16px" />

    <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
      扫描异步执行：每点约 20-60 秒，5 个点的典型扫描约 2-3 分钟。提交后跳转到详情页看进度，
      可随时中断。结果会落库可以反复查阅。
    </n-alert>

    <n-form label-placement="left" label-width="140px" style="max-width: 760px">
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

      <n-form-item label="扫描参数" required>
        <n-select
          v-model:value="selectedParam"
          :options="paramOptions"
          :disabled="!selectedFactorId || paramOptions.length === 0"
          :placeholder="selectedFactorId ? (paramOptions.length ? '选择要扫的参数' : '该因子无 params_schema') : '先选因子'"
          style="width: 100%"
        />
      </n-form-item>

      <n-form-item label="扫描点">
        <n-dynamic-tags v-model:value="valuesInput" />
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

      <n-form-item label="前瞻期（日）">
        <n-dynamic-tags v-model:value="forwardPeriodsInput" />
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :loading="createRun.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          提交扫描
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
