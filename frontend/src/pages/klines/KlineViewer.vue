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
  NSpace, NAlert, NSpin, useMessage,
} from 'naive-ui'
import { useDailyKline, useMinuteKline } from '@/api/klines'
import CandlestickChart from '@/components/charts/CandlestickChart.vue'

const message = useMessage()

const symbol = ref('000001.SZ')
const freq = ref<'1d' | '1m'>('1d')
const adjust = ref<'qfq' | 'none'>('qfq')

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
        />
      </n-card>
      <n-alert v-else-if="!isLoading && !errorMsg" type="default">
        暂无数据，请检查股票代码 / 日期区间。
      </n-alert>
    </n-spin>
  </div>
</template>
