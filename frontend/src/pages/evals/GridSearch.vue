<script setup lang="ts">
/**
 * 栅格搜索（Grid Search）创建页
 * 选择因子 → 设置参数扫描范围 → 股票池 → 日期 → 提交
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NSelect, NInputNumber,
  NDatePicker, NButton, NTag, NSpace, NAlert, NCard,
  NGrid, NGridItem, useMessage,
} from 'naive-ui'
import { useFactors, useFactor } from '@/api/factors'
import { useCreateGridSearch } from '@/api/evals'
import PoolSelector from '@/components/forms/PoolSelector.vue'
import type { Factor } from '@/api/factors'

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

// 栅格值：{ param_name: [val1, val2, ...] }
const gridValues = ref<Record<string, number[]>>({})
const baseParams = ref<Record<string, any>>({})

// 当选因子变化时，初始化栅格
watch(() => selectedFactor.data.value, (f) => {
  if (!f?.params_schema) return
  gridValues.value = {}
  baseParams.value = { ...(f.default_params || {}) }
  for (const [key, meta] of Object.entries(f.params_schema)) {
    const schema = meta as any
    const dft = schema.default ?? 0
    // 生成 5 个默认扫描点（默认值 ± 一步）
    const step = Math.max(1, Math.round(dft * 0.5))
    const vals: number[] = []
    for (let i = -2; i <= 2; i++) {
      const v = dft + i * step
      if (v >= (schema.min ?? 0) && v <= (schema.max ?? 1e9)) {
        vals.push(v)
      }
    }
    if (vals.length > 0) gridValues.value[key] = vals
  }
}, { immediate: true })

const poolId = ref<number | null>(null)
const dateRange = ref<[number, number] | null>(null)
const nGroups = ref(5)
const optimizeBy = ref<'ic_mean' | 'rank_ic_mean' | 'long_short_sharpe'>('ic_mean')

const totalCombos = computed(() => {
  let n = 1
  for (const vals of Object.values(gridValues.value)) n *= vals.length
  return n
})
const tooMany = computed(() => totalCombos.value > 200)
const tooFew = computed(() => totalCombos.value < 2)

const createGS = useCreateGridSearch()

function addValue(paramName: string) {
  const vals = gridValues.value[paramName]
  if (!vals || vals.length === 0) return
  const last = vals[vals.length - 1]
  const step = vals.length > 1 ? vals[1] - vals[0] : Math.max(1, Math.round(last * 0.3))
  gridValues.value[paramName] = [...vals, last + step]
}
function removeValue(paramName: string, idx: number) {
  const vals = gridValues.value[paramName]
  if (!vals || vals.length <= 2) return
  gridValues.value[paramName] = vals.filter((_, i) => i !== idx)
}
function updateValue(paramName: string, idx: number, value: number) {
  gridValues.value[paramName][idx] = value
}

async function handleSubmit() {
  if (!selectedFactorId.value) { message.warning('请选择因子'); return }
  if (!poolId.value) { message.warning('请选择股票池'); return }
  if (!dateRange.value) { message.warning('请选择日期区间'); return }
  if (tooFew.value) { message.warning('至少需要 2 个参数组合'); return }
  if (tooMany.value) { message.warning(`组合数 ${totalCombos.value} 超过上限 200`); return }

  // 过滤掉未修改的栅格值
  const grid: Record<string, number[]> = {}
  for (const [key, vals] of Object.entries(gridValues.value)) {
    if (vals && vals.length >= 2) grid[key] = vals
  }
  if (Object.keys(grid).length === 0) {
    message.warning('至少需要一个参数有 ≥ 2 个扫描值')
    return
  }

  const [start, end] = dateRange.value
  try {
    const { run_id } = await createGS.mutateAsync({
      factor_id: selectedFactorId.value,
      grid: grid as any,
      pool_id: poolId.value,
      start_date: new Date(start).toISOString().slice(0, 10),
      end_date: new Date(end).toISOString().slice(0, 10),
      n_groups: nGroups.value,
      forward_periods: [1],
      base_params: Object.keys(baseParams.value).length ? baseParams.value : undefined,
      optimize_by: optimizeBy.value,
    })
    message.success('栅格搜索已派发')
    router.push(`/param-sensitivity/${run_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.message ?? e?.message ?? '提交失败')
  }
}

const optimizeOptions = [
  { label: 'IC 均值 (ic_mean)', value: 'ic_mean' },
  { label: 'Rank IC 均值 (rank_ic_mean)', value: 'rank_ic_mean' },
  { label: '多空夏普 (long_short_sharpe)', value: 'long_short_sharpe' },
]

const categoryLabels: Record<string, string> = {
  reversal: '反转', momentum: '动量', volatility: '波动率',
  volume: '成交量', fundamental: '基本面', alpha101: 'Alpha101',
  oscillator: '振荡器', custom: '自定义',
}
</script>

<template>
  <div>
    <n-page-header title="栅格搜索（参数寻优）" style="margin-bottom: 16px">
      <template #subtitle>
        枚举参数组合找到最优配置 —— 从因子详情页可跳转过来
      </template>
    </n-page-header>

    <n-alert type="info" :show-icon="false" style="margin-bottom: 16px">
      栅格搜索会枚举所有参数组合并逐一评估 IC，结果按你选择的优化目标排序。
      组合数上限 200（如 3 参数各 6 值 = 216 > 200 会被拒绝）。
      完成后可以在评估详情页查看结果表格。
    </n-alert>

    <n-form label-placement="left" label-width="120" style="max-width: 720px">
      <!-- 因子选择 -->
      <n-form-item label="因子" required>
        <n-select
          v-model:value="selectedFactorId"
          :options="factorOptions"
          :loading="factorsLoading"
          placeholder="选择要优化的因子"
          filterable
          style="width: 360px"
        />
      </n-form-item>

      <!-- 因子信息提示 -->
      <n-form-item v-if="selectedFactor.data.value" label=" ">
        <n-space>
          <n-tag size="small" :bordered="false">
            {{ categoryLabels[selectedFactor.data.value.category] ?? selectedFactor.data.value.category }}
          </n-tag>
          <span style="color: #848E9C; font-size: 12px">
            {{ selectedFactor.data.value.description }}
          </span>
        </n-space>
      </n-form-item>

      <!-- 参数栅格 -->
      <n-form-item v-if="selectedFactor.data.value && Object.keys(gridValues).length > 0" label="扫描参数">
        <n-card size="small" style="width: 100%">
          <div v-for="(vals, pname) in gridValues" :key="pname" style="margin-bottom: 14px">
            <div style="font-weight: 600; margin-bottom: 6px; font-size: 13px">
              {{ pname }}
              <span style="font-weight: 400; color: #848E9C; margin-left: 6px">
                （{{ (selectedFactor.data.value?.params_schema as any)?.[pname]?.desc ?? '' }}）
              </span>
            </div>
            <n-space align="center" :wrap="true">
              <template v-for="(v, idx) in vals" :key="idx">
                <n-input-number
                  :value="v"
                  :min="(selectedFactor.data.value?.params_schema as any)?.[pname]?.min"
                  :max="(selectedFactor.data.value?.params_schema as any)?.[pname]?.max"
                  size="small"
                  style="width: 90px"
                  @update:value="(nv: number | null) => nv != null && updateValue(pname, idx, nv)"
                />
                <n-button
                  v-if="vals.length > 2"
                  size="tiny"
                  quaternary
                  type="error"
                  @click="removeValue(pname, idx)"
                >×</n-button>
              </template>
              <n-button size="tiny" secondary @click="addValue(pname)">+</n-button>
            </n-space>
          </div>
        </n-card>
      </n-form-item>

      <!-- 未修改的固定参数 -->
      <n-form-item v-if="selectedFactor.data.value && Object.keys(baseParams).some(k => !(k in gridValues))" label="固定参数">
        <n-space :wrap="true">
          <n-tag
            v-for="k in Object.keys(baseParams).filter(k => !(k in gridValues))"
            :key="k"
            size="small"
          >{{ k }}={{ baseParams[k] }}</n-tag>
        </n-space>
        <span style="color: #848E9C; font-size: 11px; margin-left: 8px">
          这些参数使用默认值，不会扫描。如需扫描请在上方添加。
        </span>
      </n-form-item>

      <!-- 股票池 -->
      <n-form-item label="股票池" required>
        <pool-selector v-model:value="poolId" style="width: 360px" />
      </n-form-item>

      <!-- 日期 -->
      <n-form-item label="日期区间" required>
        <n-date-picker
          v-model:value="dateRange"
          type="daterange"
          clearable
          style="width: 360px"
        />
      </n-form-item>

      <!-- 分组数 -->
      <n-form-item label="分组数">
        <n-input-number v-model:value="nGroups" :min="2" :max="20" style="width: 100px" />
      </n-form-item>

      <!-- 优化目标 -->
      <n-form-item label="优化目标">
        <n-select
          v-model:value="optimizeBy"
          :options="optimizeOptions"
          style="width: 280px"
        />
      </n-form-item>

      <!-- 组合数 -->
      <n-form-item label="预计组合数">
        <span :style="{ color: tooMany ? '#D03050' : '#18A058', fontWeight: 600, fontSize: 16 }">
          {{ totalCombos }}
        </span>
        <n-tag v-if="tooMany" type="error" size="small" style="margin-left: 8px">
          超过上限 200，请缩小范围
        </n-tag>
        <n-tag v-if="tooFew" type="warning" size="small" style="margin-left: 8px">
          至少需要 2 个组合
        </n-tag>
      </n-form-item>

      <n-form-item>
        <n-button
          type="primary"
          :disabled="tooMany || tooFew || createGS.isPending.value"
          :loading="createGS.isPending.value"
          @click="handleSubmit"
        >
          开始栅格搜索
        </n-button>
      </n-form-item>
    </n-form>
  </div>
</template>
