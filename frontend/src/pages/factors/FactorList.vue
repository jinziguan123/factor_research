<script setup lang="ts">
/**
 * 因子库列表页
 * 按 category 分组展示，NCard grid 布局
 *
 * "+ AI 生成" 按钮弹出对话框：用户用中文描述选股因子，后端走
 * LLM → AST 安全校验 → 落盘流水线。成功后展示元信息与源码片段，
 * 热加载 watchdog 扫到新文件后列表自动刷新（onSuccess 里也 invalidate 兜底）。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NGrid, NGridItem, NTag, NSpin, NEmpty,
  NButton, NModal, NInput, NForm, NFormItem, NSpace, NAlert,
  useMessage,
} from 'naive-ui'
import { useFactors } from '@/api/factors'
import type { Factor } from '@/api/factors'
import { useGenerateFactor, type GenerateFactorOut } from '@/api/factor_assistant'

const router = useRouter()
const message = useMessage()
const { data: factors, isLoading } = useFactors()

// 分类中文映射
const categoryLabels: Record<string, string> = {
  reversal: '反转',
  momentum: '动量',
  volatility: '波动率',
  volume: '成交量',
  custom: '自定义',
}

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

const { mutateAsync: generateFactor, isPending: aiPending } = useGenerateFactor()

function openAIModal() {
  aiDescription.value = ''
  aiHints.value = ''
  aiError.value = ''
  aiResult.value = null
  aiModalOpen.value = true
}

async function submitAI() {
  aiError.value = ''
  aiResult.value = null
  const desc = aiDescription.value.trim()
  // 后端 min_length=4，这里提前挡避免 422
  if (desc.length < 4) {
    aiError.value = '描述太短了，至少写 4 个字说清楚因子逻辑'
    return
  }
  try {
    const out = await generateFactor({
      description: desc,
      hints: aiHints.value.trim() || null,
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
</script>

<template>
  <div>
    <n-page-header title="因子库" style="margin-bottom: 16px">
      <template #extra>
        <n-button type="primary" @click="openAIModal">+ AI 生成</n-button>
      </template>
    </n-page-header>

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
              </template>
              <template #header-extra>
                <n-tag size="small" :bordered="false">{{ categoryLabels[factor.category] ?? factor.category }}</n-tag>
              </template>
              <div style="color: #848E9C; font-size: 13px; line-height: 1.6">
                {{ factor.description || '暂无描述' }}
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
          <n-form-item label="默认参数">
            <code style="font-size: 12px">{{ JSON.stringify(aiResult.default_params) }}</code>
          </n-form-item>
          <n-form-item label="源码预览">
            <n-input
              :value="aiResult.code"
              type="textarea"
              readonly
              :autosize="{ minRows: 8, maxRows: 20 }"
              style="font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px"
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
  </div>
</template>
