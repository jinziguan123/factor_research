<script setup lang="ts">
/**
 * 图形检索页（需求1：截图找相似股票）。
 * 支持上传一张或多张走势截图 → 视觉 LLM 各自提取价格主曲线 →
 * 多张时综合成一个查询，在所选股票池里找「对每张都像」的股票（min 聚合）。
 * 顶部回显每张「系统识别出的查询曲线」作为人工纠偏闸门（提取精度依赖视觉模型）。
 */
import { computed, ref } from 'vue'
import {
  NPageHeader, NCard, NSelect, NInput, NButton, NUpload, NSpace,
  NAlert, NRadioGroup, NRadioButton, useMessage, type UploadFileInfo,
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

// 多文件：维护 {id, uri} 列表，n-upload change 时增量读成 base64 dataURI。
const files = ref<{ id: string; uri: string }[]>([])
const hint = ref('')
const poolId = ref<number | null>(null)
const agg = ref<'min' | 'mean'>('min')
const result = ref<PatternResult | null>(null)

const { data: pools } = usePools()
const search = useByImageSearch()

const poolOptions = () =>
  (pools.value ?? []).map(p => ({ label: `${p.pool_name} (#${p.pool_id})`, value: p.pool_id }))

const imageUris = computed(() => files.value.map(f => f.uri))

// n-upload 不真正上传：根据当前 fileList 增量读取新文件、剔除被移除的文件。
function onFileChange(opts: { fileList: UploadFileInfo[] }) {
  const list = opts.fileList
  files.value = files.value.filter(f => list.some(l => l.id === f.id))
  for (const l of list) {
    if (l.file && !files.value.some(f => f.id === l.id)) {
      const reader = new FileReader()
      reader.onload = () => { files.value = [...files.value, { id: l.id, uri: reader.result as string }] }
      reader.readAsDataURL(l.file)
    }
  }
}

async function run() {
  if (imageUris.value.length === 0) { message.warning('请先上传至少一张走势截图'); return }
  if (poolId.value == null) { message.warning('请选择股票池'); return }
  try {
    result.value = await search.mutateAsync({
      images: imageUris.value,
      pool_id: poolId.value,
      hint: hint.value || undefined,
      agg: agg.value,
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

const queryCurves = computed<number[][]>(() => {
  if (!result.value) return []
  return result.value.query_curves ?? [result.value.query_curve]
})

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
        上传一张或多张走势截图，在所选股票池里找形状最相似的股票（最近窗口）。多张时找「对每张都像」的股票。
      </template>
    </n-page-header>

    <n-card style="margin-bottom: 16px">
      <n-space vertical :size="12">
        <n-upload
          :default-upload="false"
          multiple
          accept="image/*"
          list-type="image"
          @change="onFileChange"
        >
          <n-button>选择走势截图（可多选）</n-button>
        </n-upload>

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
        <n-space v-if="files.length > 1" align="center" :size="8">
          <span style="font-size: 13px; opacity: 0.7">多图聚合：</span>
          <n-radio-group v-model:value="agg" size="small">
            <n-radio-button value="min">对每张都像（严格）</n-radio-button>
            <n-radio-button value="mean">平均（宽松）</n-radio-button>
          </n-radio-group>
        </n-space>
        <n-button type="primary" :loading="search.isPending.value" @click="run">
          查找相似股票
        </n-button>
      </n-space>
    </n-card>

    <n-card v-if="result">
      <div style="font-size: 12px; opacity: 0.6; margin-bottom: 4px">
        系统识别出的查询曲线（请核对，若偏差大可补充上方提示后重试）：
      </div>
      <n-space :size="12" wrap>
        <div v-for="(c, i) in queryCurves" :key="i" style="width: 200px">
          <div style="font-size: 12px; opacity: 0.5">图 {{ i + 1 }}</div>
          <v-chart style="height: 100px" :option="queryOption(c)" autoresize />
        </div>
      </n-space>
      <div style="margin-top: 12px">
        <match-result-list :matches="result.matches" @open="openMatch" />
      </div>
    </n-card>
    <n-alert v-else type="default">
      上传截图并选择股票池后点击「查找相似股票」。
    </n-alert>
  </div>
</template>
