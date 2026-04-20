<script setup lang="ts">
/**
 * 任务状态标签 pill
 * pending=灰、running=黄、success=绿、failed=红
 * aborting=橙（用户已请求中断，worker 还在当前阶段跑）
 * aborted=灰（已终止，语义和 failed 区分开：不是出错，是用户主动停）
 */
import { computed } from 'vue'
import { NTag } from 'naive-ui'

const props = defineProps<{
  status: string
}>()

const statusMap: Record<string, { type: 'default' | 'warning' | 'success' | 'error' | 'info'; label: string }> = {
  pending:  { type: 'default', label: '等待中' },
  running:  { type: 'warning', label: '运行中' },
  success:  { type: 'success', label: '成功' },
  failed:   { type: 'error',   label: '失败' },
  aborting: { type: 'warning', label: '中断中' },
  aborted:  { type: 'default', label: '已中断' },
}

const info = computed(() => statusMap[props.status] ?? { type: 'default' as const, label: props.status })
</script>

<template>
  <n-tag :type="info.type" round size="small">
    {{ info.label }}
  </n-tag>
</template>
