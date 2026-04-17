<script setup lang="ts">
/**
 * 任务状态标签 pill
 * pending=灰、running=黄、success=绿、failed=红
 */
import { computed } from 'vue'
import { NTag } from 'naive-ui'

const props = defineProps<{
  status: string
}>()

const statusMap: Record<string, { type: 'default' | 'warning' | 'success' | 'error'; label: string }> = {
  pending:  { type: 'default', label: '等待中' },
  running:  { type: 'warning', label: '运行中' },
  success:  { type: 'success', label: '成功' },
  failed:   { type: 'error',   label: '失败' },
}

const info = computed(() => statusMap[props.status] ?? { type: 'default' as const, label: props.status })
</script>

<template>
  <n-tag :type="info.type" round size="small">
    {{ info.label }}
  </n-tag>
</template>
