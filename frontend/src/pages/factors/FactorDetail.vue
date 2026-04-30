<script setup lang="ts">
/**
 * 因子详情页
 * 展示因子信息 + 历史评估列表 + 操作按钮
 *
 * 「源码」按钮对所有因子无条件可见,弹窗默认 ReadOnly 态;点「编辑」才切到 Editing。
 * Editing 态按 factor.editable 分级警示:llm_generated 黄色 alert,业务因子红色 alert。
 * 「删除」按钮仍仅当 factor.editable === true 时渲染(后端 DELETE 对业务因子仍返回 403)。
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

// ---------------- 源码查看/编辑对话框 ----------------
// 打开后默认 ReadOnly 态;点"编辑"切到 Editing 态;保存成功后自动回到 ReadOnly。
// 业务因子（editable=false）进入 Editing 态时显示红色强警示;llm_generated 黄色。
const sourceOpen = ref(false)
const editing = ref(false)      // false=ReadOnly, true=Editing
const editCode = ref('')
const editError = ref('')
const originalCode = ref('')    // 进 Editing 态时的快照,用于"放弃修改"对比

const { data: factorCode, isFetching: codeLoading } = useFactorCode(
  factorId,
  sourceOpen,
)

// 后端返回新源码时同步到文本域。
// - ReadOnly 态:editCode + originalCode 都刷新为后端最新值。
// - Editing 态:两个都冻结,避免 window focus 自动 refetch / 保存后 invalidate refetch
//   静默覆盖用户正在编辑的内容或偷偷挪动 dirty 基线。基线在 enterEditing() 那一刻 snapshot,
//   保存成功后 saveEdit 显式重置基线并切回 ReadOnly。
// 用 watch 而不是 onSuccess:v-model 需要响应式源,watch 简单直接。
// 注意:Vue Query 默认开 structural sharing,第二次 refetch 返回相同内容时 data 引用不变 →
// watch 不触发 → 编辑器停在空串;所以 openSource() 也会用当前 cache 立即填充兜底。
watch(factorCode, (v) => {
  if (!v || !sourceOpen.value || editing.value) return
  originalCode.value = v.code
  editCode.value = v.code
})

function openSource() {
  editError.value = ''
  editing.value = false  // 默认只读
  // 用缓存立即填充;watch(factorCode) 刷新后再覆盖
  editCode.value = factorCode.value?.code ?? ''
  originalCode.value = factorCode.value?.code ?? ''
  sourceOpen.value = true
}

function enterEditing() {
  editError.value = ''
  // 进 Editing 态时把当前 code 存为快照,供"放弃修改"对比
  originalCode.value = editCode.value
  editing.value = true
}

function cancelEditing() {
  const dirty = editCode.value !== originalCode.value
  if (!dirty) {
    editing.value = false
    return
  }
  dialog.warning({
    title: '放弃未保存的修改？',
    content: '编辑器里有未保存的改动,切回查看态会丢失。',
    positiveText: '放弃修改',
    negativeText: '继续编辑',
    onPositiveClick: () => {
      editCode.value = originalCode.value  // 回滚
      editing.value = false
    },
  })
}

// 统一关闭路径:v-model:show 会让右上角 × 绕过 dirty 检测,这里改用
// :show + @update:show 手动拦截。Editing 态 dirty 时弹二次确认,否则直接关。
// 文案与 cancelEditing 区分:这里是"直接关闭会丢失"(意图关弹窗),cancelEditing 是
// "切回查看态会丢失"(意图切 ReadOnly、弹窗仍开)。
function handleSourceOpenChange(next: boolean) {
  if (next) {
    sourceOpen.value = true
    return
  }
  // 保存中禁止关闭,与 mask-closable / close-on-esc 保持一致,
  // 避免"放弃修改并关闭"之后 PUT 仍然成功导致状态与提示互相矛盾。
  if (savePending.value) return
  const dirty = editing.value && editCode.value !== originalCode.value
  if (!dirty) {
    sourceOpen.value = false
    return
  }
  dialog.warning({
    title: '放弃未保存的修改？',
    content: '编辑器里有未保存的改动,直接关闭会丢失。',
    positiveText: '放弃修改并关闭',
    negativeText: '继续编辑',
    onPositiveClick: () => {
      editCode.value = originalCode.value
      editing.value = false
      sourceOpen.value = false
    },
  })
}

const { mutateAsync: updateCode, isPending: savePending } = useUpdateFactorCode()

async function saveEdit() {
  editError.value = ''
  const code = editCode.value
  if (code.trim().length < 10) {
    editError.value = '源码过短（至少 10 字符）,请检查是否清空了编辑器'
    return
  }
  try {
    const res = await updateCode({ factor_id: factorId.value, code })
    const msg = res.backup_path
      ? `保存成功:${res.display_name}（v${res.version}）\n已备份至 ${res.backup_path}`
      : `保存成功:${res.display_name}（v${res.version}）`
    message.success(msg)
    // 保存成功后:刷新本地 code 快照,切回 ReadOnly 态
    originalCode.value = code
    editing.value = false
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
          <!-- 源码按钮:所有因子可见,弹窗内部默认只读,用户点"编辑"才切到可写态 -->
          <n-button secondary @click="openSource">源码</n-button>
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
        <n-descriptions-item label="研究假设" :span="2">
          <span v-if="factor.hypothesis" style="white-space: pre-wrap">{{ factor.hypothesis }}</span>
          <span v-else style="color: #999; font-style: italic">未填写（旧因子或手写源码未声明 hypothesis 类属性）</span>
        </n-descriptions-item>
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

    <!-- 源码查看/编辑弹窗 -->
    <n-modal
      :show="sourceOpen"
      @update:show="handleSourceOpenChange"
      preset="card"
      :title="editing ? `编辑源码:${factor?.display_name ?? factorId}` : `查看源码:${factor?.display_name ?? factorId}`"
      style="width: 960px; max-width: 95vw"
      :mask-closable="!savePending"
      :close-on-esc="!savePending"
    >
      <!-- Editing 态警示:按 factor.editable 分级 -->
      <n-alert
        v-if="editing && factor?.editable"
        type="warning"
        :show-icon="false"
        style="margin-bottom: 12px"
      >
        直接覆写 <code>backend/factors/llm_generated/{{ factorId }}.py</code>。
        保存前后端做 AST 白名单校验 + 类属性 <code>factor_id</code> 必须等于
        <code>{{ factorId }}</code>;保存成功后自动备份旧版本到
        <code>.backup/</code>（保留最近 5 份）、热加载生效。
      </n-alert>

      <n-alert
        v-else-if="editing && factor && !factor.editable"
        type="error"
        :show-icon="false"
        style="margin-bottom: 12px"
      >
        ⚠️ 这是业务因子（位于 <code>backend/factors/{{ factor.category }}/{{ factorId }}.py</code>）,
        保存会直接覆写 git working tree 里的源码文件。
        <br />
        建议先 <code>git commit</code> 当前状态再修改,以便出错时能用
        <code>git checkout</code> 回滚。后端会在覆写前自动备份到
        <code>.backup/</code>（保留最近 5 份）,但这只是手滑兜底,不是正式版本管理手段。
      </n-alert>

      <n-spin :show="codeLoading">
        <py-code-editor
          v-model="editCode"
          :readonly="!editing"
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
          <!-- ReadOnly 态:[关闭] [编辑] -->
          <template v-if="!editing">
            <n-button @click="handleSourceOpenChange(false)">关闭</n-button>
            <n-button type="primary" :disabled="codeLoading" @click="enterEditing">
              编辑
            </n-button>
          </template>
          <!-- Editing 态:[取消编辑] [保存] -->
          <template v-else>
            <n-button :disabled="savePending" @click="cancelEditing">
              取消编辑
            </n-button>
            <n-button
              type="primary"
              :loading="savePending"
              :disabled="codeLoading"
              @click="saveEdit"
            >
              {{ savePending ? '保存中…' : '保存' }}
            </n-button>
          </template>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
