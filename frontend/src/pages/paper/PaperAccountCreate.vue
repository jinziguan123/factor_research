<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber, NInput,
  NButton, NSpace, NDynamicInput, useMessage,
} from 'naive-ui'
import { useFactors } from '@/api/factors'
import type { Factor } from '@/api/factors'
import { useCreatePaperAccount } from '@/api/paper_trading'
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

interface FactorRow {
  factor_id: string
  params: Record<string, any>
}
const factorItems = ref<FactorRow[]>([{ factor_id: '', params: {} }])

const factorById = computed<Record<string, Factor>>(() => {
  const m: Record<string, Factor> = {}
  for (const f of factors.value ?? []) m[f.factor_id] = f
  return m
})

function onFactorChange(item: FactorRow, newId: string) {
  const f = factorById.value[newId]
  item.factor_id = newId
  item.params = f?.default_params ? { ...f.default_params } : {}
}

function getFactorSchema(fid: string): Record<string, any> | null {
  if (!fid) return null
  const f = factorById.value[fid]
  if (!f?.params_schema) return null
  if (Object.keys(f.params_schema).length === 0) return null
  return f.params_schema
}

const name = ref('')
const method = ref('equal')
const methodOptions = [
  { label: '等权 (equal)', value: 'equal' },
]
const poolId = ref<number | null>(null)
const nGroups = ref(5)
const topN = ref<number | null>(null)
const initCash = ref(1_000_000)

const createMut = useCreatePaperAccount()

async function handleSubmit() {
  if (!name.value.trim()) {
    message.warning('请输入账户名称')
    return
  }
  if (!poolId.value) {
    message.warning('请选择股票池')
    return
  }
  const validRows = factorItems.value.filter(r => r.factor_id)
  if (validRows.length === 0) {
    message.warning('至少选择 1 个因子')
    return
  }
  const ids = validRows.map(r => r.factor_id)
  if (new Set(ids).size !== ids.length) {
    message.warning('因子存在重复，请去重')
    return
  }

  const items = validRows.map(r => ({
    factor_id: r.factor_id,
    params: r.params && Object.keys(r.params).length > 0 ? r.params : null,
  }))

  try {
    const result = await createMut.mutateAsync({
      name: name.value.trim(),
      factor_items: items,
      method: method.value,
      pool_id: poolId.value,
      n_groups: nGroups.value,
      top_n: topN.value,
      init_cash: initCash.value,
    })
    message.success('模拟盘已创建')
    router.push(`/paper-accounts/${result.account_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '创建失败')
  }
}
</script>

<template>
  <div>
    <n-page-header title="新建模拟盘" @back="router.back()" style="margin-bottom: 16px" />

    <n-form label-placement="left" label-width="160px" style="max-width: 820px">
      <n-form-item label="账户名称" required>
        <n-input v-model:value="name" placeholder="如：动量等权策略" style="width: 100%" />
      </n-form-item>

      <n-form-item label="股票池" required>
        <pool-selector v-model:value="poolId" style="width: 100%" />
      </n-form-item>

      <n-form-item label="合成方法" required>
        <n-select v-model:value="method" :options="methodOptions" style="width: 100%" />
      </n-form-item>

      <n-form-item label="因子列表" required>
        <n-dynamic-input
          v-model:value="factorItems"
          :min="1"
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
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          qcut 分位数，默认 5 即五分位
        </span>
      </n-form-item>

      <n-form-item label="Top N（可选）">
        <n-input-number v-model:value="topN" :min="1" clearable style="width: 160px" />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          留空则取顶组全部
        </span>
      </n-form-item>

      <n-form-item label="初始资金">
        <n-input-number
          v-model:value="initCash"
          :min="10000"
          :step="100000"
          style="width: 200px"
        />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          默认 100 万
        </span>
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :loading="createMut.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          创建模拟盘
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
