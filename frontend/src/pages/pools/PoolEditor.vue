<script setup lang="ts">
/**
 * 股票池编辑/新建页
 * 新建：POST /api/pools
 * 编辑：GET + PUT /api/pools/:id
 * 支持批量粘贴导入
 */
import { ref, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NForm, NFormItem, NInput, NButton, NSpace,
  NList, NListItem, NTag, useMessage,
} from 'naive-ui'
import { usePool, useCreatePool, useUpdatePool, useImportSymbols } from '@/api/pools'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const poolId = computed(() => {
  const id = route.params.poolId as string | undefined
  return id ? parseInt(id) : 0
})
const isEdit = computed(() => !!poolId.value)

// 表单数据
const formData = ref({
  pool_name: '',
  description: '',
})
const importText = ref('')

// 编辑模式：加载已有数据
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
  // 对齐后端 PoolIn 契约：{ name, description?, symbols? }
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
  // 后端按 \s|,|; 切分并解析，前端原样传
  const result = await importMut.mutateAsync({ poolId: poolId.value, text })
  importText.value = ''
  message.success(`导入 ${result.inserted}/${result.total_input} 个股票成功`)
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

    <!-- 导入区域（仅编辑模式） -->
    <template v-if="isEdit">
      <h3 style="margin: 24px 0 12px">批量导入股票</h3>
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
          @click="handleImport"
          :loading="importMut.isPending.value"
        >
          导入
        </n-button>
      </n-space>

      <!-- 当前股票列表 -->
      <h3 style="margin: 24px 0 12px">
        当前股票列表（{{ poolData?.symbols?.length ?? 0 }}）
      </h3>
      <n-space v-if="poolData?.symbols?.length" :wrap="true">
        <n-tag v-for="sym in poolData.symbols" :key="sym" size="small">
          {{ sym }}
        </n-tag>
      </n-space>
      <div v-else style="color: #848E9C">暂无股票</div>
    </template>
  </div>
</template>
