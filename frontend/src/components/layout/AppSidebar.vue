<script setup lang="ts">
import { computed } from 'vue'
import type { MenuOption } from 'naive-ui'
import { NMenu } from 'naive-ui'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

// 分组 key 用 group- 前缀（不与路由冲突）；叶子项 key = 路由 path。
// NMenu 选中 group key 时本组件不会触发 router.push（详见 handleSelect 守卫）。
const menuOptions: MenuOption[] = [
  { label: 'Dashboard', key: '/' },

  {
    label: '因子开发',
    key: 'group-factor',
    children: [
      { label: '因子库', key: '/factors' },
      { label: '因子手册', key: '/docs/factor-guide' },
    ],
  },

  { label: '股票池', key: '/pools' },

  {
    label: '因子分析',
    key: 'group-analysis',
    children: [
      { label: '评估记录', key: '/evals' },
      { label: '多因子合成', key: '/compositions' },
      { label: '回测记录', key: '/backtests' },
      { label: '成本敏感性', key: '/cost-sensitivity' },
      { label: '参数敏感性', key: '/param-sensitivity' },
    ],
  },

  { label: '实盘信号', key: '/signals' },

  {
    label: '数据',
    key: 'group-data',
    children: [
      { label: 'K 线查看', key: '/klines' },
      { label: '数据健康度', key: '/data/health' },
      { label: '指数成分', key: '/data/indices' },
      { label: '财报探查', key: '/data/fundamentals/profit' },
      { label: '数据维护', key: '/admin' },
    ],
  },
]

// 默认展开所有分组：常用工具几乎每项都要点，折叠没必要省那点视觉噪音。
const defaultExpandedKeys = menuOptions
  .filter(o => Array.isArray((o as any).children))
  .map(o => o.key as string)

/** 把树形 menu 拍扁成叶子节点列表（仅保留有路由 key 的）。 */
function flattenLeaves(opts: MenuOption[]): MenuOption[] {
  const out: MenuOption[] = []
  for (const o of opts) {
    const children = (o as any).children as MenuOption[] | undefined
    if (children) out.push(...flattenLeaves(children))
    else out.push(o)
  }
  return out
}
const leaves = flattenLeaves(menuOptions)

// 子路径激活父菜单：/factors/xxx → /factors。
// 注意 root '/' 与所有 key 都"前缀匹配"——单独排除；其它项按 key 长度
// 取最长前缀匹配（避免 /data 误匹配 /data/health 的祖先）。
const activeKey = computed(() => {
  const path = route.path
  if (path === '/') return '/'
  const matches = leaves.filter(o => {
    const k = o.key as string
    return k !== '/' && path.startsWith(k)
  })
  matches.sort(
    (a, b) => (b.key as string).length - (a.key as string).length,
  )
  return (matches[0]?.key as string) ?? path
})

function handleSelect(key: string) {
  // 分组节点的 key 是 group-xxx，没对应路由——点击只折叠/展开，不导航。
  if (key.startsWith('group-')) return
  if (key !== route.path) router.push(key)
}
</script>
<template>
  <div style="padding: 20px 0">
    <div style="text-align: center; font-size: 18px; font-weight: 700; color: #F0B90B; margin-bottom: 24px">
      因子研究
    </div>
    <n-menu
      :options="menuOptions"
      :value="activeKey"
      :default-expanded-keys="defaultExpandedKeys"
      @update:value="handleSelect"
    />
  </div>
</template>
