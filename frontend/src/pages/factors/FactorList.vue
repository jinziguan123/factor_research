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
import { useFactors, useCreateFactor } from '@/api/factors'
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
          <n-input
            v-model:value="tplCode"
            type="textarea"
            :autosize="{ minRows: 20, maxRows: 32 }"
            :disabled="tplPending"
            style="font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px"
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
