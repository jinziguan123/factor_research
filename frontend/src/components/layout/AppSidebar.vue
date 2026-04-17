<script setup lang="ts">
import { computed } from 'vue'
import { NMenu } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const menuOptions = [
  { label: 'Dashboard', key: '/' },
  { label: '因子库', key: '/factors' },
  { label: '股票池', key: '/pools' },
  { label: '新评估', key: '/evals/new' },
  { label: '新回测', key: '/backtests/new' },
  { label: '数据维护', key: '/admin' },
]

// 子路径激活父菜单：/factors/xxx → /factors
const activeKey = computed(() => {
  const path = route.path
  const match = menuOptions.find(o => o.key !== '/' && path.startsWith(o.key))
  return match?.key ?? path
})

function handleSelect(key: string) {
  if (key !== route.path) router.push(key)
}
</script>
<template>
  <div style="padding: 20px 0">
    <div style="text-align: center; font-size: 18px; font-weight: 700; color: #F0B90B; margin-bottom: 24px">
      因子研究
    </div>
    <n-menu :options="menuOptions" :value="activeKey" @update:value="handleSelect" />
  </div>
</template>
