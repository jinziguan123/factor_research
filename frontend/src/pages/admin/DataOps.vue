<script setup lang="ts">
/**
 * 数据维护面板
 * 导入分钟线 (QMT .DAT → stock_bar_1m) + 聚合日线 + 导入复权因子
 *
 * 推荐执行顺序：分钟线 → 聚合日线 → 复权因子。
 * 所有任务走后端 BackgroundTasks，提交后在"最近任务"（/api/admin/jobs）里查看进度。
 */
import { ref } from 'vue'
import {
  NPageHeader, NCard, NDatePicker, NButton, NSpace, NAlert,
  NInputNumber, NInput, NSelect, useMessage,
} from 'naive-ui'
import { client } from '@/api/client'

const message = useMessage()

// ---- 导入分钟线 (QMT .DAT → stock_bar_1m) ----
// 留空约定：symbol / base_dir 留空 → 后端使用环境变量 IQUANT_LOCAL_DATA_DIR + 全量扫目录。
const barMode = ref<'full' | 'incremental'>('incremental')
const barSymbol = ref('')
const barBaseDir = ref('')
const barRewindDays = ref(3)
const barLoading = ref(false)
const barSuccess = ref(false)

const barModeOptions = [
  { label: '增量 (incremental)', value: 'incremental' },
  { label: '全量 (full)', value: 'full' },
]

async function handleBar1mImport() {
  barLoading.value = true
  barSuccess.value = false
  try {
    await client.post('/admin/bar_1m:import', {
      mode: barMode.value,
      // 空字符串 → null，让后端走默认分支
      symbol: barSymbol.value.trim() || null,
      base_dir: barBaseDir.value.trim() || null,
      rewind_days: barRewindDays.value,
    })
    barSuccess.value = true
    message.success('分钟线导入任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    barLoading.value = false
  }
}

// ---- 聚合日线 ----
const aggDateRange = ref<[number, number] | null>(null)
const aggLoading = ref(false)
const aggSuccess = ref(false)

async function handleAggregate() {
  if (!aggDateRange.value) {
    message.warning('请选择日期区间')
    return
  }
  aggLoading.value = true
  aggSuccess.value = false
  try {
    const start = new Date(aggDateRange.value[0]).toISOString().slice(0, 10)
    const end = new Date(aggDateRange.value[1]).toISOString().slice(0, 10)
    await client.post('/admin/bar_1d:aggregate', { start, end })
    aggSuccess.value = true
    message.success('聚合任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    aggLoading.value = false
  }
}

// ---- 导入复权因子 ----
const qfqChunkSize = ref(500)
const qfqLoading = ref(false)
const qfqSuccess = ref(false)

async function handleQfqImport() {
  qfqLoading.value = true
  qfqSuccess.value = false
  try {
    await client.post('/admin/qfq:import', { chunk_size: qfqChunkSize.value })
    qfqSuccess.value = true
    message.success('复权因子导入任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    qfqLoading.value = false
  }
}

// ---- 同步标的全集（Baostock → fr_instrument） ----
// 无参数；幂等 upsert。该任务用于解决幸存者偏差，灌入全量标的（含历史退市）。
const instrumentsLoading = ref(false)
const instrumentsSuccess = ref(false)

async function handleSyncInstruments() {
  instrumentsLoading.value = true
  instrumentsSuccess.value = false
  try {
    await client.post('/admin/instruments:sync_baostock', {})
    instrumentsSuccess.value = true
    message.success('标的同步任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    instrumentsLoading.value = false
  }
}

// ---- 同步当前行业归属（Baostock → fr_industry_current） ----
// 警告：Baostock 行业接口只返回当前快照，没有历史回溯能力。
const industryLoading = ref(false)
const industrySuccess = ref(false)

async function handleSyncIndustry() {
  industryLoading.value = true
  industrySuccess.value = false
  try {
    await client.post('/admin/industry:sync_baostock', {})
    industrySuccess.value = true
    message.success('行业归属同步任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    industryLoading.value = false
  }
}

// ---- 同步指数成分历史（Baostock → fr_index_constituent） ----
// 按 updateDate 翻篇，可重跑；窗口 / 指数子集都可选。
const idxConstDateRange = ref<[number, number] | null>(null)
const idxConstCodes = ref<string[]>([])
const idxConstLoading = ref(false)
const idxConstSuccess = ref(false)

const idxConstCodeOptions = [
  { label: '沪深300 (000300.SH)', value: '000300.SH' },
  { label: '中证500 (000905.SH)', value: '000905.SH' },
  { label: '中证1000 (000852.SH)', value: '000852.SH' },
]

async function handleSyncIndexConstituent() {
  idxConstLoading.value = true
  idxConstSuccess.value = false
  try {
    const payload: { start?: string; end?: string; index_codes?: string[] } = {}
    if (idxConstDateRange.value) {
      payload.start = new Date(idxConstDateRange.value[0]).toISOString().slice(0, 10)
      payload.end = new Date(idxConstDateRange.value[1]).toISOString().slice(0, 10)
    }
    if (idxConstCodes.value.length > 0) {
      payload.index_codes = idxConstCodes.value
    }
    await client.post('/admin/index_constituent:sync_baostock', payload)
    idxConstSuccess.value = true
    message.success('指数成分同步任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    idxConstLoading.value = false
  }
}

// ---- 同步财报数据（Baostock query_profit_data → fr_fundamental_profit） ----
// 长跑任务：HS300 历史成员 × 28 季度 ≈ 1-3 小时；建议先用小窗口跑一次验证。
const profitUniverse = ref<'hs300_history' | 'all_in_db'>('hs300_history')
const profitStartYear = ref(2018)
const profitStartQuarter = ref(1)
const profitLoading = ref(false)
const profitSuccess = ref(false)

const profitUniverseOptions = [
  { label: 'HS300 历史成员（推荐首跑）', value: 'hs300_history' },
  { label: '全市场（fr_instrument，含退市）', value: 'all_in_db' },
]
const profitQuarterOptions = [
  { label: 'Q1', value: 1 },
  { label: 'Q2', value: 2 },
  { label: 'Q3', value: 3 },
  { label: 'Q4', value: 4 },
]

async function handleSyncProfit() {
  profitLoading.value = true
  profitSuccess.value = false
  try {
    await client.post('/admin/profit:sync_baostock', {
      universe: profitUniverse.value,
      start_year: profitStartYear.value,
      start_quarter: profitStartQuarter.value,
    })
    profitSuccess.value = true
    message.success('财报同步任务已提交到后台（长跑，请耐心等待）')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    profitLoading.value = false
  }
}

// ---- 同步交易日历（Baostock → fr_trade_calendar） ----
// 可选日期区间；不传 → 后端默认 2015-01-01 至当日。
const calendarDateRange = ref<[number, number] | null>(null)
const calendarLoading = ref(false)
const calendarSuccess = ref(false)

async function handleSyncCalendar() {
  calendarLoading.value = true
  calendarSuccess.value = false
  try {
    const payload: { start?: string; end?: string } = {}
    if (calendarDateRange.value) {
      payload.start = new Date(calendarDateRange.value[0]).toISOString().slice(0, 10)
      payload.end = new Date(calendarDateRange.value[1]).toISOString().slice(0, 10)
    }
    await client.post('/admin/calendar:sync_baostock', payload)
    calendarSuccess.value = true
    message.success('交易日历同步任务已提交到后台')
  } catch (e: any) {
    message.error(e?.message || '提交失败')
  } finally {
    calendarLoading.value = false
  }
}
</script>

<template>
  <div>
    <n-page-header title="数据维护" style="margin-bottom: 16px" />

    <!-- 导入分钟线 -->
    <n-card title="导入分钟线 (QMT .DAT → stock_bar_1m)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-space align="center" :wrap-item="false" style="row-gap: 8px" wrap>
          <span style="color: #666">模式：</span>
          <n-select
            v-model:value="barMode"
            :options="barModeOptions"
            style="width: 180px"
          />
          <span style="color: #666">回退天数：</span>
          <n-input-number
            v-model:value="barRewindDays"
            :min="0"
            :max="30"
            :step="1"
            style="width: 120px"
          />
        </n-space>
        <n-space align="center" :wrap-item="false" style="row-gap: 8px" wrap>
          <span style="color: #666">单股 (可选)：</span>
          <n-input
            v-model:value="barSymbol"
            placeholder="留空扫全目录，如 000001.SZ"
            style="width: 220px"
            clearable
          />
          <span style="color: #666">数据目录 (可选)：</span>
          <n-input
            v-model:value="barBaseDir"
            placeholder="留空用 IQUANT_LOCAL_DATA_DIR"
            style="width: 360px"
            clearable
          />
          <n-button
            type="primary"
            :loading="barLoading"
            @click="handleBar1mImport"
          >
            执行导入
          </n-button>
        </n-space>
        <n-alert v-if="barSuccess" type="success" closable>
          分钟线导入任务已提交到后台，请到下方"最近任务"查看进度
        </n-alert>
      </n-space>
    </n-card>

    <!-- 聚合日线 -->
    <n-card title="聚合日线数据 (bar_1d)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-space align="center">
          <n-date-picker
            v-model:value="aggDateRange"
            type="daterange"
            clearable
            style="width: 320px"
          />
          <n-button
            type="primary"
            :loading="aggLoading"
            @click="handleAggregate"
          >
            执行聚合
          </n-button>
        </n-space>
        <n-alert v-if="aggSuccess" type="success" closable>
          聚合任务已提交到后台，请查看服务器日志了解进度
        </n-alert>
      </n-space>
    </n-card>

    <!-- 导入复权因子 -->
    <n-card title="导入复权因子 (qfq)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-space align="center">
          <span style="color: #666">批次大小：</span>
          <n-input-number
            v-model:value="qfqChunkSize"
            :min="50"
            :max="5000"
            :step="100"
            style="width: 140px"
          />
          <n-button
            type="primary"
            :loading="qfqLoading"
            @click="handleQfqImport"
          >
            执行导入
          </n-button>
        </n-space>
        <n-alert v-if="qfqSuccess" type="success" closable>
          复权因子导入任务已提交到后台，请查看服务器日志了解进度
        </n-alert>
      </n-space>
    </n-card>

    <!-- 同步标的全集（含退市） -->
    <n-card title="同步标的全集 (Baostock → fr_instrument)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 4px">
          全量拉取（含历史退市）；幂等 upsert，重复执行安全。用于解决幸存者偏差。
        </n-alert>
        <n-space align="center">
          <n-button
            type="primary"
            :loading="instrumentsLoading"
            @click="handleSyncInstruments"
          >
            执行同步
          </n-button>
        </n-space>
        <n-alert v-if="instrumentsSuccess" type="success" closable>
          标的同步任务已提交到后台，请查看服务器日志了解进度
        </n-alert>
      </n-space>
    </n-card>

    <!-- 同步当前行业归属 -->
    <n-card title="同步当前行业归属 (Baostock → fr_industry_current)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-alert type="warning" :show-icon="false" style="margin-bottom: 4px">
          ⚠️ Baostock 行业接口只返回**当前快照**，updateDate 是数据刷新日而非归属变更日。
          本表仅可用于当前行业暴露，**不能用于历史回测的行业中性化**。历史归属下个 phase 接 Akshare 申万。
        </n-alert>
        <n-space align="center">
          <n-button
            type="primary"
            :loading="industryLoading"
            @click="handleSyncIndustry"
          >
            执行同步
          </n-button>
        </n-space>
        <n-alert v-if="industrySuccess" type="success" closable>
          行业归属同步任务已提交到后台，请查看服务器日志了解进度
        </n-alert>
      </n-space>
    </n-card>

    <!-- 同步指数成分历史 -->
    <n-card title="同步指数成分历史 (Baostock → fr_index_constituent)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 4px">
          按 updateDate 翻篇建真历史；不选窗口默认 2015-01-01 至当日；不选指数默认 HS300+ZZ500+ZZ1000。幂等可重跑。
        </n-alert>
        <n-space align="center" :wrap-item="false" style="row-gap: 8px" wrap>
          <span style="color: #666">日期窗口：</span>
          <n-date-picker
            v-model:value="idxConstDateRange"
            type="daterange"
            clearable
            style="width: 320px"
          />
          <span style="color: #666">指数 (可选)：</span>
          <n-select
            v-model:value="idxConstCodes"
            :options="idxConstCodeOptions"
            multiple
            placeholder="留空 = 全部"
            style="width: 320px"
            clearable
          />
          <n-button
            type="primary"
            :loading="idxConstLoading"
            @click="handleSyncIndexConstituent"
          >
            执行同步
          </n-button>
        </n-space>
        <n-alert v-if="idxConstSuccess" type="success" closable>
          指数成分同步任务已提交到后台，请查看服务器日志了解进度
        </n-alert>
      </n-space>
    </n-card>

    <!-- 同步财报数据 (profit) -->
    <n-card title="同步财报数据 (Baostock query_profit_data → fr_fundamental_profit)" style="margin-bottom: 16px">
      <n-space vertical>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 4px">
          每行带 pubDate（公告日）+ statDate（报告期）双时间戳，PIT 防前视必备。
          <strong>长跑任务</strong>：HS300 历史成员 × 28 季度 ≈ 1-3 小时；幂等可重跑、续跑。
        </n-alert>
        <n-space align="center" :wrap-item="false" style="row-gap: 8px" wrap>
          <span style="color: #666">股票范围：</span>
          <n-select
            v-model:value="profitUniverse"
            :options="profitUniverseOptions"
            style="width: 280px"
          />
          <span style="color: #666">起始年：</span>
          <n-input-number
            v-model:value="profitStartYear"
            :min="2010"
            :max="2030"
            :step="1"
            style="width: 120px"
          />
          <span style="color: #666">起始季度：</span>
          <n-select
            v-model:value="profitStartQuarter"
            :options="profitQuarterOptions"
            style="width: 100px"
          />
          <n-button
            type="primary"
            :loading="profitLoading"
            @click="handleSyncProfit"
          >
            执行同步
          </n-button>
        </n-space>
        <n-alert v-if="profitSuccess" type="success" closable>
          财报同步任务已提交到后台，请通过日志观察进度（每 50 个 symbol 一条）
        </n-alert>
      </n-space>
    </n-card>

    <!-- 同步交易日历 -->
    <n-card title="同步交易日历 (Baostock → fr_trade_calendar)">
      <n-space vertical>
        <n-alert type="info" :show-icon="false" style="margin-bottom: 4px">
          不选区间则默认 2015-01-01 至当日；幂等 upsert。
        </n-alert>
        <n-space align="center">
          <n-date-picker
            v-model:value="calendarDateRange"
            type="daterange"
            clearable
            style="width: 320px"
          />
          <n-button
            type="primary"
            :loading="calendarLoading"
            @click="handleSyncCalendar"
          >
            执行同步
          </n-button>
        </n-space>
        <n-alert v-if="calendarSuccess" type="success" closable>
          日历同步任务已提交到后台，请查看服务器日志了解进度
        </n-alert>
      </n-space>
    </n-card>
  </div>
</template>
