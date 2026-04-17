<script setup lang="ts">
import { NMenu } from 'naive-ui'
import { h } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()
const menuOptions = [
  { label: () => h(RouterLink, { to: '/' }, { default: () => 'Dashboard' }), key: '/' },
  { label: () => h(RouterLink, { to: '/factors' }, { default: () => '因子库' }), key: '/factors' },
  { label: () => h(RouterLink, { to: '/pools' }, { default: () => '股票池' }), key: '/pools' },
  { label: () => h(RouterLink, { to: '/evals/new' }, { default: () => '新评估' }), key: '/evals/new' },
  { label: () => h(RouterLink, { to: '/backtests/new' }, { default: () => '新回测' }), key: '/backtests/new' },
  { label: () => h(RouterLink, { to: '/admin' }, { default: () => '数据维护' }), key: '/admin' },
]
</script>
<template>
  <div style="padding: 20px 0">
    <div style="text-align: center; font-size: 18px; font-weight: 700; color: #F0B90B; margin-bottom: 24px">
      因子研究
    </div>
    <n-menu :options="menuOptions" :value="route.path" />
  </div>
</template>
