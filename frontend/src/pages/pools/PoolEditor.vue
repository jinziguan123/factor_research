<script setup lang="ts">
/**
 * 股票池编辑/新建页
 * 新建：POST /api/pools
 * 编辑：GET + PUT /api/pools/:id
 * 添加股票：
 *   - 搜索下拉（代码 / 名称模糊匹配，多选）→ 一键加入 / 加入当前搜索结果
 *   - 按规则批量添加（glob 模式，如 *.SZ / 60* / *）→ 先预览再一键加入
 *   - 批量粘贴（兼容老用法）
 * 移除股票：每个 tag 的 × 按钮
 */
import { ref, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NInput, NButton, NSpace,
  NTag, NSelect, NEmpty, NAlert, useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import {
  usePool, useCreatePool, useUpdatePool, useImportSymbols,
  useRemovePoolSymbol, useSearchSymbols, matchSymbolsByPattern,
  type StockSymbol,
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
const removeMut = useRemovePoolSymbol()

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

// 共享的"批量加入池"辅助：先滤重（客户端先过一遍减少无效请求），
// 再调 import 接口（后端 INSERT IGNORE 再次兜底）。返回 inserted 数量供上层 toast。
async function addSymbolsToPool(symbols: string[]): Promise<number> {
  if (!isEdit.value || !poolId.value) {
    message.warning('请先保存股票池再添加股票')
    return 0
  }
  const picks = symbols.filter(s => !existingSymbolSet.value.has(s))
  if (picks.length === 0) {
    message.info('选中的股票已全部在池中')
    return 0
  }
  const result = await importMut.mutateAsync({
    poolId: poolId.value,
    text: picks.join(','),
  })
  return result.inserted
}

async function handleAddSelected() {
  if (pendingSelected.value.length === 0) {
    message.warning('请先在下拉里选择股票')
    return
  }
  const inserted = await addSymbolsToPool(pendingSelected.value)
  pendingSelected.value = []
  searchQuery.value = ''
  if (inserted > 0) message.success(`已添加 ${inserted} 只股票`)
}

// 加入当前搜索结果：把下拉里能选的（未禁用的）全部加进池。
// 用户在空搜索框情况下点击 = 加入前 50 条候选；输入关键字 = 加入当前过滤结果。
async function handleAddAllSearchResults() {
  const candidates = (searchResults.value ?? [])
    .map(s => s.symbol)
    .filter(s => !existingSymbolSet.value.has(s))
  if (candidates.length === 0) {
    message.info('当前搜索结果里没有可添加的股票')
    return
  }
  const inserted = await addSymbolsToPool(candidates)
  pendingSelected.value = []
  searchQuery.value = ''
  if (inserted > 0) message.success(`已添加 ${inserted} 只股票`)
}

// ---- 按规则批量添加（glob pattern） ----
const patternInput = ref('')
const patternMatched = ref<StockSymbol[] | null>(null)
const patternLoading = ref(false)

async function handlePreviewPattern() {
  const p = patternInput.value.trim()
  if (!p) {
    message.warning('请输入匹配规则（例如 *.SZ / 60* / *）')
    return
  }
  patternLoading.value = true
  try {
    patternMatched.value = await matchSymbolsByPattern(p)
    if (!patternMatched.value.length) {
      message.info('没有匹配到任何股票')
    }
  } catch (e: any) {
    // 后端对非法字符返回 400，错误信息在 response.data.detail 里
    const detail = e?.response?.data?.detail ?? e?.message ?? '未知错误'
    message.error(`预览失败：${detail}`)
    patternMatched.value = null
  } finally {
    patternLoading.value = false
  }
}

async function handleAddAllMatched() {
  if (!patternMatched.value?.length) return
  const syms = patternMatched.value.map(s => s.symbol)
  const inserted = await addSymbolsToPool(syms)
  if (inserted > 0) message.success(`已添加 ${inserted} 只股票`)
  // 添加完清空状态，避免用户二次点误操作
  patternInput.value = ''
  patternMatched.value = null
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
// 走后端 DELETE /pools/:id/symbols/:symbol 增量接口；毫秒级返回，且两个人同时
// 删不同股票不会互相覆盖。
// 历史实现：PUT 整个 symbols 列表重建整个池，删一只 = DELETE 5000 行 + INSERT
// 4999 行，远程 MySQL 下过秒级超时 + 有并发覆盖风险，已废弃。
async function handleRemove(symbol: string) {
  if (!poolId.value) return
  await removeMut.mutateAsync({ poolId: poolId.value, symbol })
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
      <n-space align="center" style="max-width: 900px">
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
          加入选中（{{ pendingSelected.length }}）
        </n-button>
        <!-- "加入当前搜索结果" = 把下拉里能显示的前 50 条全量加入。
             用户场景：输入"银行"搜到 20 条 → 一键全加；或清空搜索加入前 50 条。 -->
        <n-button
          secondary
          :disabled="!(searchResults ?? []).length"
          :loading="importMut.isPending.value"
          @click="handleAddAllSearchResults"
        >
          加入当前搜索结果（{{ (searchResults ?? []).length }}）
        </n-button>
      </n-space>

      <!-- 按规则批量添加（glob 模式）：覆盖"全量添加"和"前后缀匹配"需求。
           先预览匹配数再确认添加，避免误操作（比如 `*` 会匹配全市场 5000+ 只）。 -->
      <h3 style="margin: 24px 0 12px">按规则批量添加</h3>
      <n-space vertical style="max-width: 800px">
        <n-space align="center">
          <n-input
            v-model:value="patternInput"
            placeholder="示例：*.SZ / *.SH / 60* / 300* / 688* / 000*.SZ / *"
            style="width: 480px"
            @keyup.enter="handlePreviewPattern"
          />
          <n-button
            secondary
            :loading="patternLoading"
            @click="handlePreviewPattern"
          >
            预览匹配
          </n-button>
        </n-space>
        <n-alert
          v-if="patternMatched !== null"
          :type="patternMatched.length ? 'info' : 'warning'"
          :show-icon="false"
        >
          <template v-if="patternMatched.length">
            匹配到 <b>{{ patternMatched.length }}</b> 只股票。示例：
            <code style="font-size: 12px">
              {{ patternMatched.slice(0, 5).map(s => `${s.symbol} ${s.name}`).join('，') }}
              {{ patternMatched.length > 5 ? '…' : '' }}
            </code>
            <div style="margin-top: 8px">
              <n-button
                type="primary"
                :loading="importMut.isPending.value"
                @click="handleAddAllMatched"
              >
                全部加入池
              </n-button>
            </div>
          </template>
          <template v-else>没有匹配到任何股票，请调整规则。</template>
        </n-alert>
        <div style="color: #848E9C; font-size: 12px">
          通配符：<code>*</code> = 任意长度、<code>?</code> = 单字符；
          仅匹配代码，不匹配名称。需要按名称加入请用上方搜索框。
        </div>
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
