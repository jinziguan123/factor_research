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
    <n-card title="导入复权因子 (qfq)">
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
  </div>
</template>
