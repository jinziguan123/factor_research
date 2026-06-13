<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NIcon } from 'naive-ui'

const router = useRouter()

// 是否有上一页可返回：Vue Router 在 history.state 里维护 back/current/forward 链，
// back 非空说明当前页是从应用内某页跳转进来的 → 显示「返回上一级」。
const canGoBack = ref(false)
function refresh() {
  canGoBack.value = !!(window.history.state && window.history.state.back)
}
router.afterEach(() => refresh())
onMounted(refresh)

function goBack() {
  router.back()
}
</script>

<template>
  <div style="display: flex; align-items: center; justify-content: space-between; height: 100%">
    <n-button v-if="canGoBack" quaternary size="small" @click="goBack">
      <template #icon>
        <n-icon>
          <svg viewBox="0 0 24 24" width="18" height="18">
            <path fill="currentColor" d="M15.41 7.41L14 6l-6 6l6 6l1.41-1.41L10.83 12z" />
          </svg>
        </n-icon>
      </template>
      返回上一级
    </n-button>
    <span v-else></span>
    <span style="font-size: 14px; color: #848E9C">因子研究平台 v0.1</span>
  </div>
</template>
