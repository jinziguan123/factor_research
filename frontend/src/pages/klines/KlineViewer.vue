<script setup lang="ts">
/**
 * K 线查看页。
 * - 输入股票代码（如 000001.SZ），选择频率（日 / 分钟）与复权方式（qfq / raw）；
 * - 右上角切换复权方式可以直接对比"前复权后"与"原始"的序列差异，方便验证 qfq 是否跑错。
 * - 分钟线自动把默认窗口限制到最近 5 个交易日（后端硬上限 10 个交易日）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NInput, NButton, NSelect, NDatePicker,
  NSpace, NAlert, NSpin, NTag, NDrawer, NDrawerContent,
  NForm, NFormItem, NInputNumber, NTabs, NTabPane, useMessage,
} from 'naive-ui'
import { useDailyKline, useMinuteKline, useFactorBars, type FactorBarQuery } from '@/api/klines'
import { useFactors } from '@/api/factors'
import { usePools } from '@/api/pools'
import {
  useByStockSearch, useByWindowSearch, type PatternResult, type PatternMatch,
} from '@/api/patternSearch'
import CandlestickChart from '@/components/charts/CandlestickChart.vue'
import MatchResultList from '@/components/pattern/MatchResultList.vue'

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

// 成交量剖面开关（跨页面保持状态）
const showVolumeProfile = ref(localStorage.getItem('kline_vp_on') === 'true')
watch(showVolumeProfile, (v) => localStorage.setItem('kline_vp_on', String(v)))

// 框选找相似模式：开启后可在 K 线上拖拽框选一段走势，图右上角冒出「找相似」按钮。
const selectMode = ref(false)

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

// --- 图形相似度检索 ---
// 框选一段走势后，提供两种检索：
//   本股历史（by_stock）：在这只股票自己的历史里找相似形态；
//   股票池选股（by_window）：用这段真实走势，在所选股票池里找走势最像的「别的」股票。
const router = useRouter()
const similarSearch = useByStockSearch()
const windowSearch = useByWindowSearch()
const showSimilarDrawer = ref(false)
const similarResult = ref<PatternResult | null>(null)        // 本股历史
const windowResult = ref<PatternResult | null>(null)         // 股票池选股
const brushedWindow = ref<{ start: string; end: string } | null>(null)
const poolId = ref<number | null>(null)
const { data: pools } = usePools()
const poolOptions = () =>
  (pools.value ?? []).map(p => ({ label: `${p.pool_name} (#${p.pool_id})`, value: p.pool_id }))

async function onFindSimilar(payload: { start: string; end: string }) {
  if (!symbol.value.trim()) {
    message.warning('请输入股票代码')
    return
  }
  brushedWindow.value = payload
  windowResult.value = null
  try {
    similarResult.value = await similarSearch.mutateAsync({
      symbol: symbol.value.trim().toUpperCase(),
      window_start: payload.start,
      window_end: payload.end,
    })
    showSimilarDrawer.value = true
    if (similarResult.value.matches.length === 0) {
      message.info('本股历史未找到相似图形')
    }
  } catch (e: any) {
    message.error(e?.message || '检索失败')
  }
}

// 股票池选股：用框选的这段走势，在所选池里找别的相似股票。
async function runPoolSearch() {
  if (!brushedWindow.value) { message.warning('请先在 K 线上框选一段走势'); return }
  if (poolId.value == null) { message.warning('请选择股票池'); return }
  try {
    windowResult.value = await windowSearch.mutateAsync({
      symbol: symbol.value.trim().toUpperCase(),
      pool_id: poolId.value,
      window_start: brushedWindow.value.start,
      window_end: brushedWindow.value.end,
    })
    if (windowResult.value.matches.length === 0) message.info('该股票池里没找到相似股票')
  } catch (e: any) {
    message.error(e?.message || '检索失败')
  }
}

// 跨股跳转：点击池内某只相似股票 → 切到那只股票的对应区间。
function openPoolMatch(m: PatternMatch) {
  showSimilarDrawer.value = false
  router.push({
    path: '/klines',
    query: { symbol: m.label, start: m.start_date ?? undefined, end: m.end_date ?? undefined },
  })
}

function dateToTs(d: string): number {
  const [y, m, day] = d.split('-').map(Number)
  return new Date(y, (m ?? 1) - 1, day ?? 1).getTime()
}

// 跨股跳转入口：图形检索页带 symbol/start/end query 进来时定位到对应股票与区间。
const route = useRoute()
function applyRouteQuery() {
  const q = route.query
  const sym = typeof q.symbol === 'string' ? q.symbol : ''
  if (!sym) return
  symbol.value = sym.toUpperCase()
  freq.value = '1d'
  const start = typeof q.start === 'string' ? q.start : ''
  const end = typeof q.end === 'string' ? q.end : ''
  if (start && end) {
    const DAY = 86_400_000
    dailyRange.value = [dateToTs(start) - 20 * DAY, dateToTs(end) + 20 * DAY]
  }
}
onMounted(applyRouteQuery)
// 同一页内 query 变化（已在 /klines 时再次跳转）也要响应。
watch(() => route.query, applyRouteQuery)

// 点击某条匹配：把日线窗口跳到该历史段（前后各留 20 天上下文）并刷新。
function jumpToMatch(m: PatternMatch) {
  if (!m.start_date || !m.end_date) return
  const DAY = 86_400_000
  freq.value = '1d'
  dailyRange.value = [dateToTs(m.start_date) - 20 * DAY, dateToTs(m.end_date) + 20 * DAY]
  showSimilarDrawer.value = false
  handleRefresh()
}

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
        <n-button :type="showVolumeProfile ? 'primary' : 'default'" @click="showVolumeProfile = !showVolumeProfile">
          VP {{ showVolumeProfile ? 'ON' : 'OFF' }}
        </n-button>
        <n-button :type="selectMode ? 'primary' : 'default'" @click="selectMode = !selectMode">
          🔍 框选找相似 {{ selectMode ? 'ON' : 'OFF' }}
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
          :show-volume-profile="showVolumeProfile"
          :select-mode="selectMode"
          @find-similar="onFindSimilar"
        />
      </n-card>
      <n-alert v-else-if="!isLoading && !errorMsg" type="default">
        暂无数据，请检查股票代码 / 日期区间。
      </n-alert>
    </n-spin>

    <!-- 图形相似度检索结果抽屉 -->
    <n-drawer v-model:show="showSimilarDrawer" :width="440" placement="right">
      <n-drawer-content title="走势相似检索" closable>
        <n-tabs type="line" animated>
          <n-tab-pane name="self" tab="本股历史">
            <div style="font-size:12px;opacity:.6;margin-bottom:8px">
              框选段在 {{ symbol }} 自身历史中的相似走势，按相似度降序。点击跳转到对应区间。
            </div>
            <match-result-list v-if="similarResult" :matches="similarResult.matches" @open="jumpToMatch" />
          </n-tab-pane>
          <n-tab-pane name="pool" tab="股票池选股">
            <div style="font-size:12px;opacity:.6;margin-bottom:8px">
              用框选的这段走势，在所选股票池里找走势最像的「其他」股票（前复权、无截图噪声）。
            </div>
            <n-space align="center" :size="8" style="margin-bottom:12px">
              <n-select
                v-model:value="poolId"
                :options="poolOptions()"
                placeholder="选择股票池"
                filterable
                style="width: 220px"
              />
              <n-button type="primary" size="small" :loading="windowSearch.isPending.value" @click="runPoolSearch">
                选股
              </n-button>
            </n-space>
            <match-result-list v-if="windowResult" :matches="windowResult.matches" @open="openPoolMatch" />
          </n-tab-pane>
        </n-tabs>
      </n-drawer-content>
    </n-drawer>

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
