<script setup lang="ts">
/**
 * 因子库列表页
 * 按 category 分组展示，NCard grid 布局
 *
 * "+ AI 生成" 按钮弹出对话框：用户用中文描述选股因子，后端走
 * LLM → AST 安全校验 → 落盘流水线。成功后展示元信息与源码片段，
 * 热加载 watchdog 扫到新文件后列表自动刷新（onSuccess 里也 invalidate 兜底）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NGrid, NGridItem, NTag, NSpin, NEmpty,
  NButton, NModal, NInput, NForm, NFormItem, NSpace, NAlert,
  NUpload, NSelect, NCheckbox,
  useMessage,
  type UploadFileInfo,
} from 'naive-ui'
import { useFactors, useFactorCategories, useCreateFactor, type FactorQuery } from '@/api/factors'
import type { Factor } from '@/api/factors'
import { useGenerateFactor, type GenerateFactorOut } from '@/api/factor_assistant'
import { usePools } from '@/api/pools'
import PyCodeEditor from '@/components/forms/PyCodeEditor.vue'

// 单张截图允许的原始字节上限（压缩后 / 选择后）。base64 膨胀 ~1.37x 后
// data URI 长度上限约 2.7MB，与后端 2.5M 字符上限配对，给留一点边界余量。
const IMAGE_MAX_BYTES = 2 * 1024 * 1024
const IMAGE_MAX_COUNT = 4

const router = useRouter()
const message = useMessage()

// 筛选状态
const factorQuery = ref<FactorQuery>({})
const { data: factors, isLoading } = useFactors(factorQuery)
const { data: availableCategories } = useFactorCategories()
const searchKeyword = ref('')
const selectedCategory = ref<string | null>(null)
const sotaOnly = ref(false)

function applyFilters() {
  factorQuery.value = {
    keyword: searchKeyword.value.trim() || undefined,
    category: selectedCategory.value ?? undefined,
    is_sota: sotaOnly.value ? true : undefined,
  }
}
function clearFilters() {
  searchKeyword.value = ''
  selectedCategory.value = null
  sotaOnly.value = false
  factorQuery.value = {}
}

// 分类中文映射
const categoryLabels: Record<string, string> = {
  reversal: '反转',
  momentum: '动量',
  volatility: '波动率',
  volume: '成交量',
  fundamental: '基本面',
  alpha101: 'Alpha101',
  oscillator: '振荡器',
  custom: '自定义',
}

const categoryOptions = computed(() =>
  (availableCategories.value ?? []).map(c => ({
    label: categoryLabels[c] ?? c,
    value: c,
  })),
)

// 按 category 分组
const grouped = computed(() => {
  const groups: Record<string, Factor[]> = {}
  for (const f of factors.value ?? []) {
    const cat = f.category || 'custom'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(f)
  }
  return groups
})

// --- AI 生成因子对话框状态 ---
const aiModalOpen = ref(false)
const aiDescription = ref('')
const aiHints = ref('')
const aiError = ref('')
const aiResult = ref<GenerateFactorOut | null>(null)
// 上传的参考截图。NUpload 维护完整 UploadFileInfo 列表（渲染用），
// 提交时再把每个 file 读成 base64 data URI；不预先读，避免用户频繁增删重复读。
const aiFileList = ref<UploadFileInfo[]>([])
// L1.1：可选自动评估池——给定时生成因子后立即派发 60 天 IC 评估。
const aiAutoEvalPoolId = ref<number | null>(null)

const { mutateAsync: generateFactor, isPending: aiPending } = useGenerateFactor()
const { data: pools } = usePools()
const aiPoolOptions = computed(() =>
  (pools.value ?? []).map((p: any) => ({ label: p.pool_name, value: p.pool_id })),
)

function openAIModal() {
  aiDescription.value = ''
  aiHints.value = ''
  aiError.value = ''
  aiResult.value = null
  aiFileList.value = []
  aiModalOpen.value = true
}

/** 把 File 读成 `data:image/...;base64,...` 字符串。 */
function readFileAsDataUri(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}

/**
 * NUpload 的 beforeUpload 钩子：只做本地校验（类型 / 大小 / 张数），返回 false 阻断
 * 组件的自动上传——我们走"提交时才一起读 base64"的路径，所以压根不需要 HTTP 上传。
 */
function beforeAddImage(options: { file: UploadFileInfo; fileList: UploadFileInfo[] }) {
  const f = options.file.file
  if (!f) return false
  if (!f.type.startsWith('image/')) {
    message.warning(`${f.name} 不是图片格式，已忽略`)
    return false
  }
  if (f.size > IMAGE_MAX_BYTES) {
    message.warning(`${f.name} 超过 2MB，已忽略；可先截图压缩后再上传`)
    return false
  }
  if (options.fileList.length > IMAGE_MAX_COUNT) {
    message.warning(`最多只能传 ${IMAGE_MAX_COUNT} 张截图`)
    return false
  }
  return true
}

/**
 * AI 对话框全局 paste 监听：从剪贴板里挑出 image/* 追加到 aiFileList。
 * - 只在对话框打开、未提交中、未出结果时生效；
 * - 剪贴板里没有图片就不拦截，让 textarea 的普通文本粘贴照常走；
 * - 同 beforeAddImage 的校验（尺寸、张数上限）。
 */
function handleModalPaste(e: ClipboardEvent) {
  if (!aiModalOpen.value || aiPending.value || aiResult.value) return
  const items = e.clipboardData?.items
  if (!items || items.length === 0) return

  const imageFiles: File[] = []
  for (const item of items) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const f = item.getAsFile()
      if (f) imageFiles.push(f)
    }
  }
  if (imageFiles.length === 0) return
  e.preventDefault()

  let accepted = 0
  let rejectedSize = 0
  for (const raw of imageFiles) {
    if (aiFileList.value.length >= IMAGE_MAX_COUNT) {
      message.warning(`最多只能传 ${IMAGE_MAX_COUNT} 张截图，剩余已忽略`)
      break
    }
    if (raw.size > IMAGE_MAX_BYTES) {
      rejectedSize++
      continue
    }
    // 浏览器剪贴板给的 file.name 常常是固定的 "image.png"，多张会重名；重新命名一下
    const ext = (raw.type.split('/')[1] || 'png').split('+')[0]
    const synthName = `clipboard-${Date.now()}-${accepted}.${ext}`
    const renamed = new File([raw], synthName, { type: raw.type })
    aiFileList.value.push({
      id: `paste-${Date.now()}-${accepted}-${Math.random().toString(36).slice(2, 6)}`,
      name: synthName,
      status: 'finished',
      file: renamed,
      type: raw.type,
      percentage: 100,
      // image-card 预览用；对象 URL 随页面卸载自动释放，单次对话框最多 4 张，不单独 revoke
      url: URL.createObjectURL(renamed),
    } as UploadFileInfo)
    accepted++
  }
  if (accepted > 0) message.success(`已粘贴 ${accepted} 张截图`)
  if (rejectedSize > 0) message.warning(`${rejectedSize} 张截图超过 2MB，已忽略`)
}

onMounted(() => document.addEventListener('paste', handleModalPaste))
onBeforeUnmount(() => document.removeEventListener('paste', handleModalPaste))

async function submitAI() {
  aiError.value = ''
  aiResult.value = null
  const desc = aiDescription.value.trim()
  // 后端 min_length=4，这里提前挡避免 422
  if (desc.length < 4) {
    aiError.value = '描述太短了，至少写 4 个字说清楚因子逻辑'
    return
  }

  // 把已选的图片全部转 base64。列表里 `file` 可能因为历史拖拽为空（极少见），过滤掉。
  let images: string[] | null = null
  const files = aiFileList.value.map(f => f.file).filter((f): f is File => !!f)
  if (files.length > 0) {
    try {
      images = await Promise.all(files.map(readFileAsDataUri))
    } catch (e: any) {
      aiError.value = `读取图片失败：${e?.message ?? e}`
      return
    }
  }

  try {
    const out = await generateFactor({
      description: desc,
      hints: aiHints.value.trim() || null,
      images,
      auto_eval_pool_id: aiAutoEvalPoolId.value,
    })
    aiResult.value = out
    message.success(`生成成功：${out.display_name}`)
  } catch (e: any) {
    // 后端统一 envelope：HTTPException → {code, message}；axios 错误里也保留 e.message
    const msg =
      e?.response?.data?.message ??
      e?.response?.data?.detail ??
      e?.message ??
      '生成失败'
    aiError.value = String(msg)
  }
}

function goToGenerated() {
  if (aiResult.value) {
    router.push(`/factors/${aiResult.value.factor_id}`)
  }
}

// --- 从模板新建因子对话框（纯代码，不走 LLM）---
// 给新手一个最小合法骨架，省得每次从 ai_generated 拷贝；
// BaseFactor 接口要求的四个必填字段（factor_id / required_warmup / compute / category）
// 都保留了占位注释，提示用户"这里要改什么 / 不改会怎样"。
const TEMPLATE_CODE = `from __future__ import annotations

import pandas as pd

from backend.factors.base import BaseFactor, FactorContext


class MyFactor(BaseFactor):
    """一句话说明因子含义与方向（正向 / 反向）。"""

    # 与 POST /api/factors 请求体里的 factor_id 必须一致
    factor_id = "my_factor"
    display_name = "示例因子"
    # 允许值：reversal / momentum / volatility / volume / custom
    category = "custom"
    description = ""

    default_params = {"window": 20}
    params_schema = {
        "window": {"type": "int", "min": 2, "max": 250, "default": 20},
    }
    supported_freqs = ("1d",)

    def required_warmup(self, params: dict) -> int:
        # 预热天数：窗口需要多少根 bar 才能吐出第一条有效值
        return int(params.get("window", 20))

    def compute(self, ctx: FactorContext, params: dict) -> pd.DataFrame:
        """返回 long 格式 DataFrame：必须含列 [date, symbol, value]。"""
        window = int(params.get("window", 20))
        df = ctx.bars.copy()
        # TODO: 按业务替换这一行
        df["value"] = (
            df.groupby("symbol")["close"]
              .pct_change(window)
        )
        return df[["date", "symbol", "value"]].dropna()
`

const tplModalOpen = ref(false)
const tplFactorId = ref('')
const tplCode = ref('')
const tplError = ref('')
const { mutateAsync: createFactor, isPending: tplPending } = useCreateFactor()

function openTemplateModal() {
  tplFactorId.value = ''
  tplCode.value = TEMPLATE_CODE
  tplError.value = ''
  tplModalOpen.value = true
}

async function submitTemplate() {
  tplError.value = ''
  const fid = tplFactorId.value.trim()
  // 与后端 _FACTOR_ID_RE（snake_case，3-48 位）保持一致，客户端先挡一道
  if (!/^[a-z][a-z0-9_]{2,47}$/.test(fid)) {
    tplError.value = 'factor_id 必须是 3-48 位 snake_case（小写字母开头，仅字母数字下划线）'
    return
  }
  // 自动把模板里的 factor_id 占位符替换为用户填的值，省得两边不一致被后端 400 拦
  // （后端 _verify_class_factor_id 会强制类属性等于请求体 factor_id）。
  let code = tplCode.value
  if (!code.includes(`factor_id = "${fid}"`)) {
    code = code.replace(/factor_id\s*=\s*"[^"]*"/, `factor_id = "${fid}"`)
  }
  try {
    const res = await createFactor({ factor_id: fid, code })
    message.success(`创建成功：${res.display_name}（v${res.version}）`)
    tplModalOpen.value = false
    router.push(`/factors/${res.factor_id}`)
  } catch (e: any) {
    tplError.value =
      e?.response?.data?.message ??
      e?.response?.data?.detail ??
      e?.message ??
      '创建失败'
  }
}
</script>

<template>
  <div>
    <n-page-header title="因子库" style="margin-bottom: 16px">
      <template #extra>
        <n-space>
          <n-button secondary @click="openTemplateModal">+ 从模板新建</n-button>
          <n-button type="primary" @click="openAIModal">+ AI 生成</n-button>
        </n-space>
      </template>
    </n-page-header>

    <!-- 快速检索 / 条件筛选 -->
    <n-space align="center" style="margin-bottom: 16px" :wrap="true">
      <n-input
        v-model:value="searchKeyword"
        placeholder="搜索因子 ID / 名称 / 描述 / 假设..."
        clearable
        style="width: 320px"
        @keydown.enter="applyFilters"
        @clear="applyFilters"
      >
        <template #prefix>
          <span style="font-size: 16px">🔍</span>
        </template>
      </n-input>
      <n-select
        v-model:value="selectedCategory"
        :options="categoryOptions"
        placeholder="按分类筛选"
        clearable
        style="width: 160px"
        @update:value="applyFilters"
      />
      <n-checkbox v-model:checked="sotaOnly" @update:checked="applyFilters">
        ⭐ 仅 SOTA
      </n-checkbox>
      <n-button size="small" secondary @click="applyFilters">搜索</n-button>
      <n-button size="small" quaternary @click="clearFilters">清除筛选</n-button>
      <span style="color: #848E9C; font-size: 12px">
        {{ (factors ?? []).length }} 个因子
      </span>
    </n-space>

    <n-spin :show="isLoading">
      <n-empty v-if="!isLoading && !(factors ?? []).length" description="暂无因子" />

      <div v-for="(items, category) in grouped" :key="category" style="margin-bottom: 24px">
        <h3 style="margin-bottom: 12px; color: #1E2026">
          {{ categoryLabels[category] ?? category }}
        </h3>
        <n-grid :cols="3" :x-gap="16" :y-gap="16" responsive="screen">
          <n-grid-item v-for="factor in items" :key="factor.factor_id">
            <n-card
              hoverable
              style="cursor: pointer"
              @click="router.push(`/factors/${factor.factor_id}`)"
            >
              <template #header>
                <span style="font-size: 15px; font-weight: 600">{{ factor.display_name }}</span>
                <!-- L2.D：SOTA 因子加 ⭐ 徽章 + generation > 1 时显示 v 号 -->
                <n-tag
                  v-if="factor.is_sota"
                  size="tiny" type="warning" :bordered="false"
                  style="margin-left: 6px"
                >
                  ⭐ SOTA
                </n-tag>
                <span
                  v-if="(factor.generation ?? 1) > 1"
                  style="color: #848E9C; font-size: 12px; margin-left: 6px"
                >
                  v{{ factor.generation }}
                </span>
              </template>
              <template #header-extra>
                <n-tag size="small" :bordered="false">{{ categoryLabels[factor.category] ?? factor.category }}</n-tag>
              </template>
              <div style="color: #848E9C; font-size: 13px; line-height: 1.6">
                {{ factor.description || '暂无描述' }}
              </div>
              <!-- 研究假设：有则显示 1 行截断；点击因子卡进详情看全文 -->
              <div
                v-if="factor.hypothesis"
                style="margin-top: 6px; font-size: 12px; color: #5AC8FA;
                       overflow: hidden; text-overflow: ellipsis; white-space: nowrap"
                :title="factor.hypothesis"
              >
                💡 {{ factor.hypothesis }}
              </div>
              <div style="margin-top: 8px; font-size: 12px; color: #848E9C">
                版本 v{{ factor.version ?? 1 }}
              </div>
            </n-card>
          </n-grid-item>
        </n-grid>
      </div>
    </n-spin>

    <!-- AI 生成因子对话框 -->
    <!-- 把 v-if/v-else 折到内部 div 而不是 <template>，并且只用一个 #action 插槽，
         避免 Vue 编译器报 "Codegen node is missing for element/if/for node"。 -->
    <n-modal
      v-model:show="aiModalOpen"
      preset="card"
      title="AI 生成因子（Phase 0）"
      style="width: 720px; max-width: 90vw"
      :mask-closable="!aiPending"
      :close-on-esc="!aiPending"
    >
      <!-- 未有生成结果 → 输入表单 -->
      <div v-if="!aiResult">
        <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
          用中文描述选股因子，LLM 会翻译成符合 BaseFactor 接口的 Python 源码并落盘到
          <code>backend/factors/llm_generated/</code>。代码会先过 AST 白名单安全校验，
          危险操作（<code>os/subprocess</code>、<code>exec/eval</code> 等）一律拒绝。
        </n-alert>

        <n-form>
          <n-form-item label="因子描述（必填）" required>
            <n-input
              v-model:value="aiDescription"
              type="textarea"
              placeholder="例：过去 20 日累计收益率的负值，用于短期反转选股"
              :autosize="{ minRows: 3, maxRows: 6 }"
              :disabled="aiPending"
              maxlength="2000"
              show-count
            />
          </n-form-item>

          <n-form-item label="补充信息（可选）">
            <n-input
              v-model:value="aiHints"
              type="textarea"
              placeholder="例：使用对数收益；预热期按 30 个自然日"
              :autosize="{ minRows: 2, maxRows: 4 }"
              :disabled="aiPending"
              maxlength="2000"
              show-count
            />
          </n-form-item>

          <n-form-item label="自动评估池（可选）">
            <n-select
              v-model:value="aiAutoEvalPoolId"
              :options="aiPoolOptions"
              placeholder="选定后会立即派发 60 天 IC 评估"
              clearable
              filterable
              :disabled="aiPending"
              style="width: 320px"
            />
            <span style="color: #848E9C; font-size: 12px; margin-left: 8px">
              用 60 天 / forward_periods=[1,5] / n_groups=5 跑一次轻量评估
            </span>
          </n-form-item>

          <n-form-item label="参考截图（可选，最多 4 张）">
            <div style="width: 100%">
              <n-upload
                v-model:file-list="aiFileList"
                list-type="image-card"
                accept="image/*"
                :max="IMAGE_MAX_COUNT"
                :multiple="true"
                :disabled="aiPending"
                :default-upload="false"
                :show-retry-button="false"
                :show-download-button="false"
                :on-before-upload="beforeAddImage"
              >
                点击、拖拽或 Ctrl/⌘+V 粘贴图片
              </n-upload>
              <div style="color: #848E9C; font-size: 12px; margin-top: 6px; line-height: 1.5">
                常见用法：截一两张理想的 K 线形态示例给模型看。支持从剪贴板直接粘贴（先用系统截图
                工具截到剪贴板，再在本对话框里 Ctrl/⌘+V）。单张 ≤ 2MB；图像只作为参考，不代表要
                完全复刻这张图的走势。
              </div>
            </div>
          </n-form-item>
        </n-form>

        <n-alert v-if="aiError" type="error" :show-icon="false" style="margin-top: 8px">
          {{ aiError }}
        </n-alert>
      </div>

      <!-- 有生成结果 → 展示 -->
      <div v-else>
        <n-alert type="success" :show-icon="false" style="margin-bottom: 12px">
          因子已落盘：<code>{{ aiResult.saved_path }}</code>
          <br />
          <span style="color: #848E9C; font-size: 12px">
            热加载开启时，几秒内会自动出现在因子列表里；否则需手动在因子详情页点刷新。
          </span>
        </n-alert>

        <!-- L1.1：auto-eval 已派发时给一个跳转入口 -->
        <n-alert
          v-if="aiResult.auto_eval_run_id"
          type="info"
          :show-icon="false"
          style="margin-bottom: 12px"
        >
          📊 自动 IC 评估已派发到后台（run_id
          <code>{{ aiResult.auto_eval_run_id.slice(0, 8) }}</code>），
          <a
            style="cursor: pointer; color: #5AC8FA"
            @click="router.push(`/evals/${aiResult.auto_eval_run_id}`)"
          >点击查看进度</a>。
        </n-alert>

        <n-form>
          <n-form-item label="因子 ID">
            <code>{{ aiResult.factor_id }}</code>
          </n-form-item>
          <n-form-item label="显示名">
            <span>{{ aiResult.display_name }}</span>
            <n-tag size="small" style="margin-left: 8px" :bordered="false">
              {{ categoryLabels[aiResult.category] ?? aiResult.category }}
            </n-tag>
          </n-form-item>
          <n-form-item label="描述">
            <span>{{ aiResult.description }}</span>
          </n-form-item>
          <n-form-item v-if="aiResult.hypothesis" label="研究假设">
            <span style="white-space: pre-wrap">{{ aiResult.hypothesis }}</span>
          </n-form-item>
          <n-form-item label="默认参数">
            <code style="font-size: 12px">{{ JSON.stringify(aiResult.default_params) }}</code>
          </n-form-item>
          <n-form-item label="源码预览">
            <py-code-editor
              :model-value="aiResult.code"
              readonly
              height="360px"
              @update:model-value="() => {}"
            />
          </n-form-item>
        </n-form>
      </div>

      <template #action>
        <n-space justify="end">
          <template v-if="!aiResult">
            <n-button :disabled="aiPending" @click="aiModalOpen = false">取消</n-button>
            <n-button type="primary" :loading="aiPending" @click="submitAI">
              {{ aiPending ? '生成中（最长 60 秒）…' : '生成' }}
            </n-button>
          </template>
          <template v-else>
            <n-button @click="aiModalOpen = false">关闭</n-button>
            <n-button type="primary" @click="goToGenerated">查看因子详情</n-button>
          </template>
        </n-space>
      </template>
    </n-modal>

    <!-- 从模板新建因子对话框（纯代码，不走 LLM）-->
    <n-modal
      v-model:show="tplModalOpen"
      preset="card"
      title="从模板新建因子"
      style="width: 960px; max-width: 95vw"
      :mask-closable="!tplPending"
      :close-on-esc="!tplPending"
    >
      <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
        直接写 Python 源码落盘到 <code>backend/factors/llm_generated/&lt;factor_id&gt;.py</code>，
        不经 LLM。保存前后端做 AST 白名单校验 + 类属性 <code>factor_id</code> 与请求一致性校验。
      </n-alert>

      <n-form>
        <n-form-item label="因子 ID（snake_case）" required>
          <n-input
            v-model:value="tplFactorId"
            placeholder="例：my_reversal_20d"
            :disabled="tplPending"
            maxlength="48"
            show-count
          />
        </n-form-item>

        <n-form-item label="源码">
          <py-code-editor
            v-model="tplCode"
            :disabled="tplPending"
            height="480px"
          />
        </n-form-item>
      </n-form>

      <n-alert v-if="tplError" type="error" :show-icon="false" style="margin-top: 8px">
        {{ tplError }}
      </n-alert>

      <template #action>
        <n-space justify="end">
          <n-button :disabled="tplPending" @click="tplModalOpen = false">取消</n-button>
          <n-button type="primary" :loading="tplPending" @click="submitTemplate">
            {{ tplPending ? '创建中…' : '创建' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
