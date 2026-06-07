<script setup lang="ts">
/**
 * 新建图形检索（需求1：截图找相似股票）。
 * 上传一张或多张走势截图 + 选股票池 → 创建异步任务 → 跳到详情页轮询结果。
 * 多张时综合成一个查询，找「对每张都像」的股票（min 聚合）。
 */
import { computed, ref } from 'vue'
import {
  NPageHeader, NCard, NSelect, NInput, NButton, NUpload, NSpace,
  NRadioGroup, NRadioButton, useMessage, type UploadFileInfo,
} from 'naive-ui'
import { useRouter } from 'vue-router'
import { usePools } from '@/api/pools'
import { useCreateImageSearch } from '@/api/patternSearch'

const message = useMessage()
const router = useRouter()

// 多文件：维护 {id, uri, name} 列表，n-upload change 时增量读成 base64 dataURI。
const files = ref<{ id: string; uri: string; name: string }[]>([])
const hint = ref('')
const poolId = ref<number | null>(null)
const agg = ref<'min' | 'mean'>('min')

const { data: pools } = usePools()
const create = useCreateImageSearch()

const poolOptions = () =>
  (pools.value ?? []).map(p => ({ label: `${p.pool_name} (#${p.pool_id})`, value: p.pool_id }))

// 只取已读完（uri 非空）的文件，避免把占位项也算进去。
const imageUris = computed(() => files.value.filter(f => f.uri).map(f => f.uri))
const imageNames = computed(() => files.value.filter(f => f.uri).map(f => f.name))

function onFileChange(opts: { fileList: UploadFileInfo[] }) {
  const list = opts.fileList
  // 以 n-upload 的 fileList 为准移除已删除项。
  files.value = files.value.filter(f => list.some(l => l.id === f.id))
  for (const l of list) {
    if (l.file && !files.value.some(f => f.id === l.id)) {
      // 关键：先同步占位入列（uri 为空），再异步读 base64 回填。
      // n-upload 多选会多次触发 change，若等 FileReader.onload 完成后才入列，
      // 判重在读取期间一直失败 → 同一文件被重复读入 → num_images 虚高。
      files.value = [...files.value, { id: l.id, uri: '', name: l.name }]
      const reader = new FileReader()
      reader.onload = () => {
        files.value = files.value.map(f =>
          f.id === l.id ? { ...f, uri: reader.result as string } : f,
        )
      }
      reader.readAsDataURL(l.file)
    }
  }
}

async function run() {
  if (imageUris.value.length === 0) { message.warning('请先上传至少一张走势截图'); return }
  if (imageUris.value.length !== files.value.length) {
    message.warning('图片还在读取中，请稍候重试'); return
  }
  if (poolId.value == null) { message.warning('请选择股票池'); return }
  try {
    const res = await create.mutateAsync({
      images: imageUris.value,
      image_names: imageNames.value,
      pool_id: poolId.value,
      hint: hint.value || undefined,
      agg: agg.value,
    })
    message.success('任务已创建，正在后台检索…')
    router.push({ path: `/pattern/runs/${res.run_id}` })
  } catch (e: any) {
    message.error(e?.message || '创建任务失败')
  }
}
</script>

<template>
  <div>
    <n-page-header title="新建图形检索" style="margin-bottom: 16px" @back="router.push('/pattern')">
      <template #subtitle>
        上传一张或多张走势截图，在所选股票池里找形状最相似的股票（最近窗口）。多张时找「对每张都像」的股票。
      </template>
    </n-page-header>

    <n-card>
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
        <n-button type="primary" :loading="create.isPending.value" @click="run">
          创建检索任务
        </n-button>
      </n-space>
    </n-card>
  </div>
</template>
