<script setup lang="ts">
/**
 * 因子详情页
 * 展示因子信息 + 历史评估列表 + 操作按钮
 *
 * 编辑 / 删除按钮仅当 factor.editable === true 时渲染——即因子源码位于
 * backend/factors/llm_generated/ 下；手写业务目录（momentum / reversal / …）
 * 永远不给前端入口，后端还会在 PUT/DELETE 时再做一次 403 兜底。
 */
import { computed, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NDescriptions, NDescriptionsItem, NTag, NSpace,
  NButton, NDataTable, NSpin, NModal, NAlert,
  useMessage, useDialog,
} from 'naive-ui'
import PyCodeEditor from '@/components/forms/PyCodeEditor.vue'
import {
  useFactor, useFactorCode, useUpdateFactorCode, useDeleteFactor,
} from '@/api/factors'
import { useEvals } from '@/api/evals'
import type { EvalRun } from '@/api/evals'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import type { DataTableColumns } from 'naive-ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const factorId = computed(() => route.params.factorId as string)
const { data: factor, isLoading } = useFactor(factorId)

// 历史评估列表
const evalParams = computed(() => ({ factor_id: factorId.value }))
const { data: evals, isLoading: evalsLoading } = useEvals(evalParams)

const evalColumns: DataTableColumns<EvalRun> = [
  { title: 'Run ID', key: 'run_id', width: 200, ellipsis: { tooltip: true } },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => h(StatusBadge, { status: row.status }),
  },
  { title: '创建时间', key: 'created_at', width: 180 },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) => h(NButton, {
      size: 'small',
      quaternary: true,
      type: 'primary',
      onClick: () => router.push(`/evals/${row.run_id}`),
    }, { default: () => '查看' }),
  },
]

// ---------------- 编辑源码对话框 ----------------
const editOpen = ref(false)
const editCode = ref('')
const editError = ref('')
const { data: factorCode, isFetching: codeLoading } = useFactorCode(
  factorId,
  editOpen,
)

// 打开弹窗 / 后端返回新源码时把数据塞到文本域里。
// 用 watch 而不是 onSuccess：NInput v-model 需要响应式源，watch 简单直接。
watch(factorCode, (v) => {
  if (v && editOpen.value) editCode.value = v.code
})

function openEdit() {
  editError.value = ''
  // 用缓存里的 code 立即填充，避免空窗期。
  // 不能只靠下面的 watch(factorCode) 塞值：Vue Query 默认开 structural sharing，
  // 第二次 refetch 返回相同内容时 data 引用不变 → watch 不触发 → 编辑器停在空串。
  editCode.value = factorCode.value?.code ?? ''
  editOpen.value = true
}

const { mutateAsync: updateCode, isPending: savePending } = useUpdateFactorCode()

async function saveEdit() {
  editError.value = ''
  const code = editCode.value
  if (code.trim().length < 10) {
    editError.value = '源码过短（至少 10 字符），请检查是否清空了编辑器'
    return
  }
  try {
    const res = await updateCode({ factor_id: factorId.value, code })
    message.success(`保存成功：${res.display_name}（v${res.version}）`)
    editOpen.value = false
  } catch (e: any) {
    editError.value =
      e?.response?.data?.message ??
      e?.response?.data?.detail ??
      e?.message ??
      '保存失败'
  }
}

// ---------------- 删除 ----------------
const { mutateAsync: deleteFactor } = useDeleteFactor()

function confirmDelete() {
  dialog.warning({
    title: '确认删除因子？',
    content: () => h('div', [
      h('p', `即将删除 ${factor.value?.display_name ?? factorId.value}（${factorId.value}）。`),
      h('p', { style: 'color: #848E9C; font-size: 12px' },
        '源码文件会被物理删除，元数据表软删（is_active=0），' +
        '历史评估 / 回测记录仍可查阅。此操作仅限 llm_generated/ 下的因子。'),
    ]),
    positiveText: '确认删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteFactor(factorId.value)
        message.success('删除成功')
        router.replace('/factors')
      } catch (e: any) {
        const msg =
          e?.response?.data?.message ??
          e?.response?.data?.detail ??
          e?.message ??
          '删除失败'
        message.error(String(msg))
      }
    },
  })
}
</script>

<template>
  <div>
    <n-page-header
      :title="factor?.display_name ?? '加载中...'"
      @back="router.push('/factors')"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <n-space>
          <n-button
            type="primary"
            @click="router.push(`/evals/new?factor_id=${factorId}`)"
          >
            新评估
          </n-button>
          <n-button
            secondary
            @click="router.push(`/backtests/new?factor_id=${factorId}`)"
          >
            新回测
          </n-button>
          <n-button v-if="factor?.editable" secondary @click="openEdit">
            编辑源码
          </n-button>
          <n-button v-if="factor?.editable" type="error" secondary @click="confirmDelete">
            删除
          </n-button>
        </n-space>
      </template>
    </n-page-header>

    <n-spin :show="isLoading">
      <n-descriptions v-if="factor" bordered :column="2" label-placement="left" style="margin-bottom: 24px">
        <n-descriptions-item label="因子 ID">{{ factor.factor_id }}</n-descriptions-item>
        <n-descriptions-item label="分类">{{ factor.category }}</n-descriptions-item>
        <n-descriptions-item label="描述" :span="2">{{ factor.description || '-' }}</n-descriptions-item>
        <n-descriptions-item label="支持频率">
          <n-space>
            <n-tag v-for="f in factor.supported_freqs" :key="f" size="small">{{ f }}</n-tag>
          </n-space>
        </n-descriptions-item>
        <n-descriptions-item label="版本">v{{ factor.version ?? 1 }}</n-descriptions-item>
        <n-descriptions-item label="默认参数" :span="2">
          <code style="font-size: 12px">{{ JSON.stringify(factor.default_params) }}</code>
        </n-descriptions-item>
        <n-descriptions-item label="参数 Schema" :span="2">
          <code style="font-size: 12px">{{ JSON.stringify(factor.params_schema) }}</code>
        </n-descriptions-item>
      </n-descriptions>
    </n-spin>

    <!-- 历史评估列表 -->
    <h3 style="margin-bottom: 12px">历史评估</h3>
    <n-data-table
      :columns="evalColumns"
      :data="evals ?? []"
      :loading="evalsLoading"
      :bordered="false"
      :single-line="false"
      :row-key="(row: any) => row.run_id"
    />

    <!-- 编辑源码弹窗 -->
    <n-modal
      v-model:show="editOpen"
      preset="card"
      :title="`编辑源码：${factor?.display_name ?? factorId}`"
      style="width: 960px; max-width: 95vw"
      :mask-closable="!savePending"
      :close-on-esc="!savePending"
    >
      <n-alert type="warning" :show-icon="false" style="margin-bottom: 12px">
        直接覆写 <code>backend/factors/llm_generated/{{ factorId }}.py</code>。
        保存前后端会做 AST 白名单校验 + 类属性 <code>factor_id</code> 必须等于
        <code>{{ factorId }}</code>；保存成功后触发热加载 + 进程池重置，
        下一次评估 / 回测使用新代码。
      </n-alert>

      <n-spin :show="codeLoading">
        <py-code-editor
          v-model="editCode"
          :disabled="savePending"
          height="520px"
          placeholder="加载中..."
        />
      </n-spin>

      <n-alert v-if="editError" type="error" :show-icon="false" style="margin-top: 8px">
        {{ editError }}
      </n-alert>

      <template #action>
        <n-space justify="end">
          <n-button :disabled="savePending" @click="editOpen = false">取消</n-button>
          <n-button
            type="primary"
            :loading="savePending"
            :disabled="codeLoading"
            @click="saveEdit"
          >
            {{ savePending ? '保存中…' : '保存' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
