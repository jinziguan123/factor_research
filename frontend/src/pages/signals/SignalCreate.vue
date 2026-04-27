<script setup lang="ts">
/**
 * 实盘信号创建页。
 *
 * 与 CompositionCreate 同结构，差异：
 * - 没有 start_date / end_date：信号只看当下，历史窗口由后端定；
 * - 没有 forward_periods：不评估 IC，只看末行 qcut；
 * - method 多 'single' 选项（单因子，factor_items 仅 1 个）；
 * - 多 use_realtime / filter_price_limit NSwitch（默认 ON）；
 * - 多 ic_lookback_days（仅 ic_weighted 显示）。
 */
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NButton, NSpace, NDynamicInput, NSwitch, NTooltip, NAlert,
  useMessage,
} from 'naive-ui'
import { useFactors } from '@/api/factors'
import type { Factor } from '@/api/factors'
import { useCreateSignal } from '@/api/signals'
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

// factor_id → Factor 查找表，用于读 params_schema / default_params。
const factorById = computed<Record<string, Factor>>(() => {
  const m: Record<string, Factor> = {}
  for (const f of factors.value ?? []) m[f.factor_id] = f
  return m
})

/** 切换因子时：用新因子的 default_params 重置 params。 */
function onFactorChange(item: FactorRow, newId: string) {
  const f = factorById.value[newId]
  item.factor_id = newId
  item.params = f?.default_params ? { ...f.default_params } : {}
}

/** 取该因子的 params_schema；空 schema → null。 */
function getFactorSchema(fid: string): Record<string, any> | null {
  if (!fid) return null
  const f = factorById.value[fid]
  if (!f?.params_schema) return null
  if (Object.keys(f.params_schema).length === 0) return null
  return f.params_schema
}

type Method = 'single' | 'equal' | 'ic_weighted' | 'orthogonal_equal'
const method = ref<Method>('single')
const methodOptions = [
  { label: '单因子 (single) — 仅 1 个因子的当下排名', value: 'single' },
  { label: '等权 (equal) — 多个因子 z-score 后算术平均', value: 'equal' },
  { label: 'IC 加权 (ic_weighted) — 按全窗口 IC 自动加权', value: 'ic_weighted' },
  { label: '正交等权 (orthogonal_equal) — Gram-Schmidt 去共线', value: 'orthogonal_equal' },
]

const poolId = ref<number | null>(null)
const nGroups = ref(5)
const icLookbackDays = ref(60)
const useRealtime = ref(true)
const filterPriceLimit = ref(true)

// method 切换时调整 factorItems 最小行数
watch(method, (m) => {
  if (m === 'single') {
    if (factorItems.value.length === 0) {
      factorItems.value = [{ factor_id: '', params: {} }]
    } else {
      factorItems.value = factorItems.value.slice(0, 1)
    }
  } else if (factorItems.value.length < 2) {
    factorItems.value = [
      ...factorItems.value,
      { factor_id: '', params: {} },
    ].slice(0, 2)
  }
})

const minFactorRows = computed(() => (method.value === 'single' ? 1 : 2))
const maxFactorRows = computed(() => (method.value === 'single' ? 1 : 8))

const createRun = useCreateSignal()

async function handleSubmit() {
  if (!poolId.value) {
    message.warning('请选择股票池')
    return
  }

  const validRows = factorItems.value.filter((r) => r.factor_id)
  if (method.value === 'single') {
    if (validRows.length !== 1) {
      message.warning('单因子模式需要正好 1 个因子')
      return
    }
  } else {
    if (validRows.length < 2) {
      message.warning('多因子合成至少需要 2 个因子（或切到"single"模式）')
      return
    }
    const ids = validRows.map((r) => r.factor_id)
    if (new Set(ids).size !== ids.length) {
      message.warning('因子存在重复，请去重')
      return
    }
  }

  // 空 params dict → null（后端会用 default_params）；非空原样下发
  const items = validRows.map((r) => ({
    factor_id: r.factor_id,
    params:
      r.params && Object.keys(r.params).length > 0 ? r.params : null,
  }))

  const body: Record<string, any> = {
    factor_items: items,
    method: method.value,
    pool_id: poolId.value,
    n_groups: nGroups.value,
    ic_lookback_days: icLookbackDays.value,
    use_realtime: useRealtime.value,
    filter_price_limit: filterPriceLimit.value,
    // as_of_time 留空：后端用 NOW()
  }

  try {
    const result = await createRun.mutateAsync(body)
    message.success('信号任务已提交')
    router.push(`/signals/${result.run_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '提交失败')
  }
}
</script>

<template>
  <div>
    <n-page-header title="新建实盘信号" @back="router.back()" style="margin-bottom: 16px" />

    <n-alert type="info" size="small" style="margin-bottom: 16px">
      <span style="font-size: 12px">
        实盘信号会拉取最新 spot 快照作为"今日 close 估计"，结合历史日 K 计算因子排名。
        盘后 16:00 后或 spot 数据陈旧（&gt;10min）时自动降级到"昨日 close"模式。
      </span>
    </n-alert>

    <n-form label-placement="left" label-width="160px" style="max-width: 820px">
      <n-form-item label="股票池" required>
        <pool-selector v-model:value="poolId" style="width: 100%" />
      </n-form-item>

      <n-form-item label="合成方法" required>
        <n-select v-model:value="method" :options="methodOptions" style="width: 100%" />
      </n-form-item>

      <n-form-item label="因子列表" required>
        <n-dynamic-input
          v-model:value="factorItems"
          :min="minFactorRows"
          :max="maxFactorRows"
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

      <n-form-item v-if="method === 'ic_weighted'" label="IC 加权回看期 (天)">
        <n-input-number
          v-model:value="icLookbackDays"
          :min="10" :max="500"
          style="width: 160px"
        />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          用过去 N 天的 IC 计算因子权重；60 是常用经验值
        </span>
      </n-form-item>

      <n-form-item>
        <template #label>
          <n-tooltip>
            <template #trigger>
              <span style="cursor: help; border-bottom: 1px dashed #999">
                使用实时数据
              </span>
            </template>
            开启时取最新 spot 快照作为"今日 close"输入因子计算；<br/>
            关闭或 spot 陈旧 / 缺失时，用昨日 close 当今日 close（保守降级）。
          </n-tooltip>
        </template>
        <n-switch v-model:value="useRealtime" />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          {{ useRealtime ? '已开启（盘中推荐）' : '已关闭（用昨日 close）' }}
        </span>
      </n-form-item>

      <n-form-item>
        <template #label>
          <n-tooltip>
            <template #trigger>
              <span style="cursor: help; border-bottom: 1px dashed #999">
                涨跌停过滤
              </span>
            </template>
            按 |pct_chg| ≥ 0.097 剔除当日涨停 / 跌停 / 停牌票（明天买不到 / 卖不掉）。<br/>
            盘中比回测更有必要：实盘信号一般默认开启。
          </n-tooltip>
        </template>
        <n-switch v-model:value="filterPriceLimit" />
        <span style="margin-left: 12px; color: #999; font-size: 12px">
          {{ filterPriceLimit ? '已开启（推荐）' : '已关闭' }}
        </span>
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :loading="createRun.isPending.value"
          @click="handleSubmit"
          style="border-radius: 20px; padding: 0 32px"
        >
          触发信号
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
