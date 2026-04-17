<script setup lang="ts">
/**
 * 股票池编辑/新建页
 * 新建：POST /api/pools
 * 编辑：GET + PUT /api/pools/:id
 * 添加股票：
 *   - 搜索下拉（代码 / 名称模糊匹配，多选）→ 一键加入
 *   - 批量粘贴（兼容老用法）
 * 移除股票：每个 tag 的 × 按钮
 */
import { ref, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NInput, NButton, NSpace,
  NTag, NSelect, NEmpty, useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import {
  usePool, useCreatePool, useUpdatePool, useImportSymbols,
  useSearchSymbols,
} from '@/api/pools'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const poolId = computed(() => {
  const id = route.params.poolId as string | undefined
  return id ? parseInt(id) : 0
})
const isEdit = computed(() => !!poolId.value)

// ---- 基本信息表单 ----
const formData = ref({ pool_name: '', description: '' })

const { data: poolData } = usePool(computed(() => poolId.value || ''))
watch(poolData, (p) => {
  if (p) {
    formData.value.pool_name = p.pool_name
    formData.value.description = p.description ?? ''
  }
}, { immediate: true })

const createMut = useCreatePool()
const updateMut = useUpdatePool()
const importMut = useImportSymbols()

async function handleSave() {
  const name = formData.value.pool_name.trim()
  if (!name) {
    message.warning('请输入股票池名称')
    return
  }
  const body = { name, description: formData.value.description || null }
  if (isEdit.value) {
    await updateMut.mutateAsync({ poolId: poolId.value, body })
    message.success('保存成功')
  } else {
    const result = await createMut.mutateAsync(body)
    message.success('创建成功')
    router.replace(`/pools/${result.pool_id}`)
  }
}

// ---- 搜索加入 ----
// 简易防抖：NSelect on-search 每个按键都会触发，100ms 内归并一次请求。
const searchQuery = ref('')
let searchTimer: number | undefined
function handleSearchInput(q: string) {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    searchQuery.value = q
  }, 100)
}
const { data: searchResults, isLoading: searchLoading } = useSearchSymbols(searchQuery)

// 已在池里的 symbol 集合，下拉里展示但禁用，避免用户误加重复（后端 INSERT IGNORE
// 兜底不会出错，但点了没反应的 UX 很糟）。
const existingSymbolSet = computed(
  () => new Set((poolData.value?.symbols ?? []).map(s => s.symbol))
)
const searchOptions = computed<SelectOption[]>(() =>
  (searchResults.value ?? []).map(s => ({
    label: `${s.symbol}  ${s.name}`,
    value: s.symbol,
    disabled: existingSymbolSet.value.has(s.symbol),
  }))
)

// 已选中但尚未"点添加"的 symbol。添加后清空。
const pendingSelected = ref<string[]>([])

async function handleAddSelected() {
  if (!isEdit.value || !poolId.value) {
    message.warning('请先保存股票池再添加股票')
    return
  }
  const picks = pendingSelected.value.filter(s => !existingSymbolSet.value.has(s))
  if (picks.length === 0) {
    message.warning('请先在下拉里选择股票')
    return
  }
  const result = await importMut.mutateAsync({
    poolId: poolId.value,
    text: picks.join(','),
  })
  pendingSelected.value = []
  searchQuery.value = ''
  message.success(`已添加 ${result.inserted}/${result.total_input} 只股票`)
}

// ---- 批量粘贴 ----
const importText = ref('')
async function handleImport() {
  if (!isEdit.value || !poolId.value) {
    message.warning('请先保存股票池再导入')
    return
  }
  const text = importText.value.trim()
  if (!text) {
    message.warning('请粘贴需要导入的股票代码')
    return
  }
  const result = await importMut.mutateAsync({ poolId: poolId.value, text })
  importText.value = ''
  message.success(`导入 ${result.inserted}/${result.total_input} 只股票成功`)
}

// ---- 单只移除 ----
// 没单独 DELETE 一只的后端接口，这里用 PUT 重写 symbols 列表（去掉目标一只）。
// 池规模最多几千只，PUT 一次完整列表在可接受范围。
async function handleRemove(symbol: string) {
  if (!poolData.value || !poolId.value) return
  const remaining = poolData.value.symbols
    .map(s => s.symbol)
    .filter(s => s !== symbol)
  await updateMut.mutateAsync({
    poolId: poolId.value,
    body: {
      name: poolData.value.pool_name,
      description: poolData.value.description,
      symbols: remaining,
    },
  })
  message.success(`已移除 ${symbol}`)
}
</script>

<template>
  <div>
    <n-page-header
      :title="isEdit ? '编辑股票池' : '新建股票池'"
      @back="router.push('/pools')"
      style="margin-bottom: 16px"
    />

    <n-form label-placement="left" label-width="100px" style="max-width: 600px">
      <n-form-item label="名称" required>
        <n-input v-model:value="formData.pool_name" placeholder="输入股票池名称" />
      </n-form-item>
      <n-form-item label="描述">
        <n-input
          v-model:value="formData.description"
          type="textarea"
          placeholder="股票池描述（可选）"
          :rows="3"
        />
      </n-form-item>
      <n-form-item>
        <n-button
          type="primary"
          @click="handleSave"
          :loading="createMut.isPending.value || updateMut.isPending.value"
        >
          {{ isEdit ? '保存' : '创建' }}
        </n-button>
      </n-form-item>
    </n-form>

    <!-- 仅编辑模式有"添加股票"区域 -->
    <template v-if="isEdit">
      <!-- 搜索添加 -->
      <h3 style="margin: 24px 0 12px">添加股票</h3>
      <n-space align="center" style="max-width: 800px">
        <n-select
          v-model:value="pendingSelected"
          multiple
          filterable
          clearable
          remote
          :options="searchOptions"
          :loading="searchLoading"
          placeholder="按代码或名称搜索（支持多选）"
          style="width: 480px"
          @search="handleSearchInput"
        />
        <n-button
          type="primary"
          :disabled="!pendingSelected.length"
          :loading="importMut.isPending.value"
          @click="handleAddSelected"
        >
          加入池（{{ pendingSelected.length }}）
        </n-button>
      </n-space>

      <!-- 批量粘贴（老用法保留） -->
      <h3 style="margin: 24px 0 12px">批量粘贴导入</h3>
      <n-space vertical style="max-width: 600px">
        <n-input
          v-model:value="importText"
          type="textarea"
          placeholder="粘贴股票代码，支持逗号/换行/空格分隔&#10;例如: 000001.SZ, 600519.SH"
          :rows="4"
        />
        <n-button
          type="primary"
          secondary
          :loading="importMut.isPending.value"
          @click="handleImport"
        >
          导入
        </n-button>
      </n-space>

      <!-- 当前成员：tag 可单只移除 -->
      <h3 style="margin: 24px 0 12px">
        当前股票列表（{{ poolData?.symbols?.length ?? 0 }}）
      </h3>
      <n-space v-if="poolData?.symbols?.length" :wrap="true">
        <n-tag
          v-for="s in poolData.symbols"
          :key="s.symbol"
          closable
          size="small"
          @close="handleRemove(s.symbol)"
        >
          {{ s.symbol }} {{ s.name }}
        </n-tag>
      </n-space>
      <n-empty v-else description="暂无股票，用上方搜索或粘贴添加" />
    </template>
  </div>
</template>
