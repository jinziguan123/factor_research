<script setup lang="ts">
/**
 * 图表卡片：在 NCard 基础上加"全屏/退出全屏"按钮。
 *
 * 为什么不用 NModal 二次渲染图表？
 * - NModal 会把 slot 内容复制到 modal body，但 Vue slot 默认只能渲染一次；
 *   即使强制复制，ECharts 也会丢失 dataZoom / tooltip 等交互状态；
 * - 这里改为"原地放大"：fullscreen 状态下给外层加 fixed 定位 + 高 z-index，
 *   vue-echarts 的 autoresize 会自动重新计算尺寸。ESC 键退出。
 */
import { ref, watchEffect } from 'vue'
import { NCard, NButton } from 'naive-ui'

defineProps<{
  title?: string
}>()

const fullscreen = ref(false)

// 监听 ESC 键；仅在 fullscreen 打开期间挂钩，避免对非全屏状态的按键造成干扰。
watchEffect((onCleanup) => {
  if (!fullscreen.value) return
  const handler = (e: KeyboardEvent) => {
    if (e.key === 'Escape') fullscreen.value = false
  }
  window.addEventListener('keydown', handler)
  onCleanup(() => window.removeEventListener('keydown', handler))
})
</script>

<template>
  <div :class="['chart-card', { 'chart-card--fullscreen': fullscreen }]">
    <n-card :title="title" size="small" class="chart-card__card">
      <template #header-extra>
        <n-button
          text
          size="small"
          :title="fullscreen ? '退出全屏 (ESC)' : '全屏查看'"
          @click="fullscreen = !fullscreen"
        >
          <!-- 使用内联 SVG 避免引入图标库；两个形态用同一个按钮切换 -->
          <svg
            v-if="!fullscreen"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M8 3H5a2 2 0 0 0-2 2v3" />
            <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
            <path d="M3 16v3a2 2 0 0 0 2 2h3" />
            <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
          </svg>
          <svg
            v-else
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M8 3v3a2 2 0 0 1-2 2H3" />
            <path d="M21 8h-3a2 2 0 0 1-2-2V3" />
            <path d="M3 16h3a2 2 0 0 1 2 2v3" />
            <path d="M16 21v-3a2 2 0 0 1 2-2h3" />
          </svg>
        </n-button>
      </template>
      <slot />
    </n-card>
  </div>
</template>

<style scoped>
.chart-card {
  /* 占位：非全屏时保持原来的布局 */
  position: relative;
}

.chart-card--fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(30, 32, 38, 0.55);
  padding: 32px;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
}

.chart-card--fullscreen .chart-card__card {
  flex: 1;
  display: flex;
  flex-direction: column;
}

/* 把 vue-echarts 内部的固定高度撑大到可用空间；inline style 的 height 必须用
   !important 才覆盖得掉。vue-echarts 默认根元素 class 是 echarts。*/
.chart-card--fullscreen :deep(.echarts) {
  height: 100% !important;
  width: 100% !important;
  min-height: calc(100vh - 180px);
}

.chart-card--fullscreen :deep(.n-card__content) {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.chart-card--fullscreen :deep(.n-card__content > *) {
  flex: 1;
}
</style>
