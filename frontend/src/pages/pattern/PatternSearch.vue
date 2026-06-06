<script setup lang="ts">
/**
 * 图形检索页（需求1：截图找相似股票）。
 * 上传走势截图 → 视觉 LLM 提取价格主曲线 → 在所选股票池每只股最近窗口里找相似。
 * 顶部回显「系统识别出的查询曲线」作为人工纠偏闸门（提取精度依赖视觉模型）。
 */
import { ref } from 'vue'
import {
  NPageHeader, NCard, NSelect, NInput, NButton, NUpload, NSpace,
  NAlert, useMessage, type UploadFileInfo,
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { useRouter } from 'vue-router'
import { usePools } from '@/api/pools'
import { useByImageSearch, type PatternResult, type PatternMatch } from '@/api/patternSearch'
import MatchResultList from '@/components/pattern/MatchResultList.vue'

use([CanvasRenderer, LineChart, GridComponent])

const message = useMessage()
const router = useRouter()

const imageUri = ref('')
const hint = ref('')
const poolId = ref<number | null>(null)
const result = ref<PatternResult | null>(null)

const { data: pools } = usePools()
const search = useByImageSearch()

const poolOptions = () =>
  (pools.value ?? []).map(p => ({ label: `${p.pool_name} (#${p.pool_id})`, value: p.pool_id }))

// n-upload 不真正上传，仅把文件读成 base64 dataURI。
function onFileChange(opts: { file: UploadFileInfo }) {
  const f = opts.file.file
  if (!f) return
  const reader = new FileReader()
  reader.onload = () => { imageUri.value = reader.result as string }
  reader.readAsDataURL(f)
}

async function run() {
  if (!imageUri.value) { message.warning('请先上传走势截图'); return }
  if (poolId.value == null) { message.warning('请选择股票池'); return }
  try {
    result.value = await search.mutateAsync({
      image: imageUri.value,
      pool_id: poolId.value,
      hint: hint.value || undefined,
    })
    if (result.value.matches.length === 0) message.info('未在该股票池找到相似股票')
  } catch (e: any) {
    message.error(e?.message || '检索失败')
  }
}

// 跨股跳转：带 symbol/start/end 进入 K 线页定位。
function openMatch(m: PatternMatch) {
  router.push({
    path: '/klines',
    query: {
      symbol: m.label,
      start: m.start_date ?? undefined,
      end: m.end_date ?? undefined,
    },
  })
}

function queryOption(curve: number[]) {
  return {
    animation: false,
    grid: { left: 4, right: 4, top: 4, bottom: 4 },
    xAxis: { type: 'category', show: false, data: curve.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{ type: 'line', data: curve, showSymbol: false, lineStyle: { width: 1.5 } }],
  }
}
</script>

<template>
  <div>
    <n-page-header title="图形检索" style="margin-bottom: 16px">
      <template #subtitle>
        上传一张走势截图，在所选股票池里找形状最相似的股票（最近窗口）。
      </template>
    </n-page-header>

    <n-card style="margin-bottom: 16px">
      <n-space vertical :size="12">
        <n-upload
          :default-upload="false"
          :max="1"
          accept="image/*"
          @change="onFileChange"
        >
          <n-button>选择走势截图</n-button>
        </n-upload>
        <img v-if="imageUri" :src="imageUri" style="max-height: 200px; border: 1px solid #eee; border-radius: 4px" />

        <n-input
          v-model:value="hint"
          type="textarea"
          placeholder="可选提示，用于纠偏，如：圆弧底后放量突破"
          :autosize="{ minRows: 1, maxRows: 3 }"
          style="max-width: 480px"
        />
        <n-select
          v-model:value="poolId"
          :options="poolOptions()"
          placeholder="选择股票池"
          filterable
          style="max-width: 320px"
        />
        <n-button type="primary" :loading="search.isPending.value" @click="run">
          查找相似股票
        </n-button>
      </n-space>
    </n-card>

    <n-card v-if="result">
      <div style="font-size: 12px; opacity: 0.6; margin-bottom: 4px">
        系统识别出的查询曲线（请核对，若偏差大可补充上方提示后重试）：
      </div>
      <v-chart style="height: 120px" :option="queryOption(result.query_curve)" autoresize />
      <div style="margin-top: 12px">
        <match-result-list :matches="result.matches" @open="openMatch" />
      </div>
    </n-card>
    <n-alert v-else type="default">
      上传截图并选择股票池后点击「查找相似股票」。
    </n-alert>
  </div>
</template>
