<script setup lang="ts">
/**
 * 股票池下拉选择器
 * 调用 usePools() 拉取列表，渲染为 NSelect
 */
import { computed } from 'vue'
import { NSelect } from 'naive-ui'
import { usePools } from '@/api/pools'

defineProps<{
  value: number | null
}>()

const emit = defineEmits<{
  (e: 'update:value', val: number | null): void
}>()

const { data: pools, isLoading } = usePools()

const options = computed(() =>
  (pools.value ?? []).map(p => ({
    label: p.pool_name,
    value: p.pool_id,
  }))
)
</script>

<template>
  <n-select
    :value="value"
    :options="options"
    :loading="isLoading"
    placeholder="选择股票池"
    clearable
    filterable
    @update:value="(v: number | null) => emit('update:value', v)"
  />
</template>
