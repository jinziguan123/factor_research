<script setup lang="ts">
/**
 * K 线查看页。
 * - 输入股票代码（如 000001.SZ），选择频率（日 / 分钟）与复权方式（qfq / raw）；
 * - 右上角切换复权方式可以直接对比"前复权后"与"原始"的序列差异，方便验证 qfq 是否跑错。
 * - 分钟线自动把默认窗口限制到最近 5 个交易日（后端硬上限 10 个交易日）。
 */
import { computed, ref, watch } from 'vue'
import {
  NPageHeader, NCard, NInput, NButton, NSelect, NDatePicker,
  NSpace, NAlert, NSpin, NTag, NDrawer, NDrawerContent,
  NForm, NFormItem, NInputNumber, useMessage,
} from 'naive-ui'
import { useDailyKline, useMinuteKline, useFactorBars, type FactorBarQuery } from '@/api/klines'
import { useFactors } from '@/api/factors'
import CandlestickChart from '@/components/charts/CandlestickChart.vue'

const message = useMessage()

// --- refresh key to trigger factor bar refetch ---
const refreshKey = ref(0)

// --- factor selection ---
const FACTOR_COLORS = ['#5dade2', '#e67e22', '#27ae60', '#9b59b6', '#f1c40f']

interface FactorSlot {
  factor_id: string
  display_name: string
  category: string
  params_schema: Record<string, any>
  default_params: Record<string, any>
  params: Record<string, any>
  color: string
}

const selectedFactors = ref<FactorSlot[]>([])
const selectedFactorToAdd = ref<string | null>(null)
const { data: allFactors } = useFactors()

// Only show factors that support the current frequency and aren't already selected
const availableFactors = computed(() => {
  if (!allFactors.value) return []
  const alreadySelected = new Set(selectedFactors.value.map(s => s.factor_id))
  return allFactors.value.filter(
    f => f.supported_freqs.includes(freq.value) && !alreadySelected.has(f.factor_id),
  )
})

const canAddFactor = computed(() => selectedFactors.value.length < 5)

// --- parameter editing ---
const editingFactorIndex = ref<number | null>(null)
const editingParams = ref<Record<string, any>>({})

const showParamDrawer = computed({
  get: () => editingFactorIndex.value !== null,
  set: (v) => { if (!v) editingFactorIndex.value = null },
})

function openParamEditor(index: number) {
  editingFactorIndex.value = index
  editingParams.value = { ...selectedFactors.value[index].params }
}

function applyParams() {
  if (editingFactorIndex.value === null) return
  const idx = editingFactorIndex.value
  const next = [...selectedFactors.value]
  next[idx] = { ...next[idx], params: { ...editingParams.value } }
  selectedFactors.value = next
  editingFactorIndex.value = null
}

function resetParams() {
  if (editingFactorIndex.value === null) return
  const slot = selectedFactors.value[editingFactorIndex.value]
  editingParams.value = { ...slot.default_params }
}

// --- factor management ---
function addFactor(factorId: string) {
  if (!canAddFactor.value) return
  const factor = allFactors.value?.find(f => f.factor_id === factorId)
  if (!factor) return
  const slot: FactorSlot = {
    factor_id: factor.factor_id,
    display_name: factor.display_name,
    category: factor.category,
    params_schema: factor.params_schema,
    default_params: { ...factor.default_params },
    params: { ...factor.default_params },
    color: FACTOR_COLORS[selectedFactors.value.length],
  }
  selectedFactors.value = [...selectedFactors.value, slot]
}

function removeFactor(index: number) {
  const next = [...selectedFactors.value]
  next.splice(index, 1)
  next.forEach((s, i) => { s.color = FACTOR_COLORS[i] })
  selectedFactors.value = next
}

// --- factor data hooks (one per slot position) ---
function slotQuery(index: number) {
  return computed<FactorBarQuery | null>(() => {
    const slot = selectedFactors.value[index]
    if (!slot || !symbol.value.trim()) return null
    void refreshKey.value  // dependency tracking
    const range = freq.value === '1d' ? dailyRange.value : minuteRange.value
    return {
      symbol: symbol.value.trim().toUpperCase(),
      start: toIso(range[0]),
      end: toIso(range[1]),
      freq: freq.value,
    }
  })
}

const slotBars = [0, 1, 2, 3, 4].map(i =>
  useFactorBars(
    computed(() => selectedFactors.value[i]?.factor_id ?? ''),
    computed(() => selectedFactors.value[i]?.params ?? {}),
    slotQuery(i),
  )
)

// --- factor rows for CandlestickChart ---
const factorRows = computed(() =>
  slotBars
    .map((q, i) => ({ slot: selectedFactors.value[i], data: q.data.value }))
    .filter(x => x.slot && x.data && x.data.dates.length > 0)
    .map(x => ({
      name: x.slot!.display_name,
      color: x.slot!.color,
      dates: x.data!.dates,
      values: x.data!.values,
    }))
)

const symbol = ref('000001.SZ')
const freq = ref<'1d' | '1m'>('1d')
const adjust = ref<'qfq' | 'none'>('qfq')

// --- frequency switch: remove incompatible factors ---
watch(freq, (newFreq) => {
  const removed: string[] = []
  const kept = selectedFactors.value.filter(s => {
    const factor = allFactors.value?.find(f => f.factor_id === s.factor_id)
    const ok = factor?.supported_freqs.includes(newFreq)
    if (!ok) removed.push(s.display_name)
    return ok
  })
  if (removed.length > 0) {
    selectedFactors.value = kept.map((s, i) => ({ ...s, color: FACTOR_COLORS[i] }))
    message.warning(`${removed.join('、')} doesn't support this frequency, auto-removed`)
  }
})
// 涨跌配色：默认 A 股（红涨绿跌），一键切成币圈 / 美股风格（绿涨红跌）。
const colorMode = ref<'a-share' | 'binance'>('a-share')
function toggleColorMode() {
  colorMode.value = colorMode.value === 'a-share' ? 'binance' : 'a-share'
}

// 默认窗口：日线 180 天，分钟线 5 天。切换 freq 时自动换档，避免用户忘了缩窗口触发 400。
const today = new Date()
const dailyRange = ref<[number, number]>([
  new Date(today.getFullYear(), today.getMonth() - 6, today.getDate()).getTime(),
  today.getTime(),
])
const minuteRange = ref<[number, number]>([
  new Date(today.getFullYear(), today.getMonth(), today.getDate() - 5).getTime(),
  today.getTime(),
])

function toIso(ts: number): string {
  // 本地日期转 ISO 的 YYYY-MM-DD；避免 toISOString() 的 UTC 偏移问题。
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// 查询参数：用 getter 让 enabled 能 lazy 读最新的 symbol。
const dailyParams = computed(() =>
  freq.value === '1d' && symbol.value
    ? {
        symbol: symbol.value.trim().toUpperCase(),
        start: toIso(dailyRange.value[0]),
        end: toIso(dailyRange.value[1]),
        adjust: adjust.value,
      }
    : null,
)

const minuteParams = computed(() =>
  freq.value === '1m' && symbol.value
    ? {
        symbol: symbol.value.trim().toUpperCase(),
        start: toIso(minuteRange.value[0]),
        end: toIso(minuteRange.value[1]),
        adjust: adjust.value,
      }
    : null,
)

const dailyQ = useDailyKline(dailyParams)
const minuteQ = useMinuteKline(minuteParams)

// 给 Candlestick 组件喂数据；两种 freq 合并到同一套 props。
const chartData = computed(() => {
  if (freq.value === '1d') {
    const rows = dailyQ.data.value?.rows ?? []
    return {
      categories: rows.map((r) => r.trade_date),
      ohlc: rows.map((r) => [r.open, r.high, r.low, r.close] as [number, number, number, number]),
      volumes: rows.map((r) => r.volume),
    }
  }
  const rows = minuteQ.data.value?.rows ?? []
  return {
    categories: rows.map((r) => r.ts),
    ohlc: rows.map((r) => [r.open, r.high, r.low, r.close] as [number, number, number, number]),
    volumes: rows.map((r) => r.volume),
  }
})

const isLoading = computed(() =>
  freq.value === '1d' ? dailyQ.isFetching.value : minuteQ.isFetching.value,
)
const errorMsg = computed(() => {
  const e = freq.value === '1d' ? dailyQ.error.value : minuteQ.error.value
  return e ? (e as any).message || String(e) : null
})

// 工具条：用户改完 symbol 后按回车 / 点"刷新"触发实际请求。
// useQuery 的 reactive key 已经会自动跟随变更，refetch 只是显式 UX 触发。
function handleRefresh() {
  if (!symbol.value.trim()) {
    message.warning('请输入股票代码')
    return
  }
  if (freq.value === '1d') dailyQ.refetch()
  else minuteQ.refetch()
  refreshKey.value++
}

watch(freq, () => {
  // 切 freq 时若数据过旧，立刻触发一次查询，避免"图还停在上个频率"。
  handleRefresh()
})
</script>

<template>
  <div>
    <n-page-header title="K 线查看" style="margin-bottom: 16px">
      <template #subtitle>
        用于肉眼验证前复权 / 原始行情是否正常。分钟线最多 10 个交易日窗口。
      </template>
    </n-page-header>

    <n-card style="margin-bottom: 16px">
      <n-space :size="16" align="center" wrap>
        <n-input
          v-model:value="symbol"
          placeholder="股票代码，如 000001.SZ"
          style="width: 200px"
          @keyup.enter="handleRefresh"
        />
        <n-select
          v-model:value="freq"
          :options="[
            { label: '日线 (1d)', value: '1d' },
            { label: '分钟线 (1m)', value: '1m' },
          ]"
          style="width: 140px"
        />
        <n-select
          v-model:value="adjust"
          :options="[
            { label: '前复权 (qfq)', value: 'qfq' },
            { label: '原始 (raw)', value: 'none' },
          ]"
          style="width: 160px"
        />
        <!-- Factor selector -->
        <n-select
          v-model:value="selectedFactorToAdd"
          :options="availableFactors.map(f => ({ label: `${f.display_name} (${f.factor_id})`, value: f.factor_id }))"
          placeholder="+ Add Factor"
          :disabled="!canAddFactor"
          filterable
          clearable
          style="width: 180px"
          @update:value="(val: string) => { if (val) { addFactor(val); selectedFactorToAdd = null } }"
        />

        <!-- Factor chips -->
        <n-space v-if="selectedFactors.length > 0" :size="4" align="center">
          <n-tag
            v-for="(slot, idx) in selectedFactors"
            :key="slot.factor_id"
            :bordered="true"
            :color="{ color: slot.color, borderColor: slot.color }"
            closable
            @close="removeFactor(idx)"
          >
            {{ slot.display_name }}
            <template #avatar>
              <span @click.stop="openParamEditor(idx)" style="cursor: pointer; opacity: 0.7;">&#9881;</span>
            </template>
          </n-tag>
        </n-space>

        <n-date-picker
          v-if="freq === '1d'"
          v-model:value="dailyRange"
          type="daterange"
          clearable
          style="width: 280px"
        />
        <n-date-picker
          v-else
          v-model:value="minuteRange"
          type="daterange"
          clearable
          style="width: 280px"
        />
        <n-button type="primary" :loading="isLoading" @click="handleRefresh">
          刷新
        </n-button>
        <n-button quaternary @click="toggleColorMode">
          {{ colorMode === 'a-share' ? '红涨绿跌 (A 股)' : '绿涨红跌 (币圈)' }}
        </n-button>
      </n-space>
    </n-card>

    <n-alert v-if="errorMsg" type="error" style="margin-bottom: 16px">
      {{ errorMsg }}
    </n-alert>

    <n-spin :show="isLoading">
      <n-card v-if="chartData.categories.length > 0">
        <candlestick-chart
          :categories="chartData.categories"
          :ohlc="chartData.ohlc"
          :volumes="chartData.volumes"
          :color-mode="colorMode"
          :factor-rows="factorRows"
        />
      </n-card>
      <n-alert v-else-if="!isLoading && !errorMsg" type="default">
        暂无数据，请检查股票代码 / 日期区间。
      </n-alert>
    </n-spin>

    <!-- Parameter editing drawer -->
    <n-drawer v-model:show="showParamDrawer" :width="360" placement="right">
      <n-drawer-content title="Factor Parameters" closable>
        <template v-if="editingFactorIndex !== null && selectedFactors[editingFactorIndex]">
          <n-form label-placement="top" :model="editingParams">
            <n-form-item
              v-for="(meta, key) in selectedFactors[editingFactorIndex].params_schema"
              :key="key"
              :label="`${key} (${meta.type ?? 'int'}${meta.min != null ? ', ' + meta.min + '~' + meta.max : ''})`"
            >
              <n-select
                v-if="meta.options"
                v-model:value="editingParams[key]"
                :options="meta.options.map((o: any) => ({ label: String(o), value: o }))"
              />
              <n-input-number
                v-else
                v-model:value="editingParams[key]"
                :min="meta.min"
                :max="meta.max"
                :step="meta.type === 'float' ? 0.01 : 1"
              />
            </n-form-item>
          </n-form>
          <n-space justify="end" style="margin-top: 16px">
            <n-button quaternary @click="resetParams">Reset Default</n-button>
            <n-button type="primary" @click="applyParams">Apply</n-button>
          </n-space>
        </template>
        <template v-else>
          <n-alert type="default">No factor selected for editing.</n-alert>
        </template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>
