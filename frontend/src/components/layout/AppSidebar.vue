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
  { label: '评估记录', key: '/evals' },
  { label: '回测记录', key: '/backtests' },
  { label: '成本敏感性', key: '/cost-sensitivity' },
  { label: '参数敏感性', key: '/param-sensitivity' },
  { label: '多因子合成', key: '/compositions' },
  { label: 'K 线查看', key: '/klines' },
  { label: '数据维护', key: '/admin' },
  { label: '数据健康度', key: '/data/health' },
  { label: '指数成分', key: '/data/indices' },
  { label: '财报探查', key: '/data/fundamentals/profit' },
  { label: '因子手册', key: '/docs/factor-guide' },
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
