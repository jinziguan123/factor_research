<script setup lang="ts">
/**
 * 动态参数表单渲染器
 * 根据 params_schema（JSON Schema 风格）渲染对应的表单控件
 */
import { computed } from 'vue'
import { NFormItem, NInputNumber, NInput, NSwitch } from 'naive-ui'

const props = defineProps<{
  schema: Record<string, any>  // JSON Schema 的 properties
  modelValue: Record<string, any>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, any>): void
}>()

interface FieldDef {
  key: string
  type: string
  title: string
  default?: any
  description?: string
  minimum?: number
  maximum?: number
}

// 解析 schema.properties 为平铺字段列表
const fields = computed<FieldDef[]>(() => {
  const properties = props.schema?.properties ?? props.schema ?? {}
  return Object.entries(properties).map(([key, def]: [string, any]) => ({
    key,
    type: def.type ?? 'string',
    title: def.title ?? key,
    default: def.default,
    description: def.description,
    minimum: def.minimum,
    maximum: def.maximum,
  }))
})

function updateField(key: string, value: any) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}
</script>

<template>
  <div class="params-form-renderer">
    <n-form-item
      v-for="field in fields"
      :key="field.key"
      :label="field.title"
      :label-placement="'left'"
    >
      <!-- 整数 / 浮点 -->
      <n-input-number
        v-if="field.type === 'integer' || field.type === 'number'"
        :value="modelValue[field.key] ?? field.default"
        :min="field.minimum"
        :max="field.maximum"
        :precision="field.type === 'integer' ? 0 : undefined"
        :placeholder="field.description ?? field.title"
        style="width: 200px"
        @update:value="(v: number | null) => updateField(field.key, v)"
      />
      <!-- 布尔 -->
      <n-switch
        v-else-if="field.type === 'boolean'"
        :value="modelValue[field.key] ?? field.default ?? false"
        @update:value="(v: boolean) => updateField(field.key, v)"
      />
      <!-- 字符串（默认） -->
      <n-input
        v-else
        :value="modelValue[field.key] ?? field.default ?? ''"
        :placeholder="field.description ?? field.title"
        style="width: 280px"
        @update:value="(v: string) => updateField(field.key, v)"
      />
    </n-form-item>
  </div>
</template>
