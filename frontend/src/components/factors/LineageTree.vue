<script setup lang="ts">
/**
 * 因子进化链路可视化（L2.D）。
 *
 * 设计取舍：YAGNI ——绝大多数因子链是**单链** v1 → v2 → v3，少数有分叉
 * （用户没从 SOTA 出发再进化时形成）。所以用纯 CSS 嵌套缩进 + 箭头连线
 * 渲染，不引入 react-flow / d3 这类大库。
 *
 * 数据来源：``useFactors()``（已 enrich parent_factor_id / generation /
 * is_sota / root_factor_id）—— 不需后端新接口，前端按 root_factor_id 过
 * 滤同链所有因子，按 parent_factor_id 重建树。
 *
 * 高亮：当前 factor_id 用粗边框 / 主色；SOTA 用 ⭐ 黄色徽章；点击节点跳
 * 转该因子详情页。
 */
import { computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { NTag, NSpin, NEmpty } from 'naive-ui'
import { useFactors } from '@/api/factors'
import type { Factor } from '@/api/factors'

const props = defineProps<{
  /** 当前查看的 factor_id（高亮用）。 */
  currentFactorId: string
  /** 当前因子的 root_factor_id（用于过滤同链）。NULL 时认为自己就是根。 */
  rootFactorId: string | null | undefined
}>()

const router = useRouter()
const { data: factors, isLoading } = useFactors()

/** 同链全部因子（root_factor_id 等于 effectiveRoot 或 factor_id 自身就是 root）。 */
const sameRoot = computed<Factor[]>(() => {
  const all = factors.value ?? []
  // root_factor_id 为空表示自己就是 root；effectiveRoot 取 props.rootFactorId
  // 或 currentFactorId（fallback）。
  const effectiveRoot = props.rootFactorId || props.currentFactorId
  return all.filter(f => {
    return (
      f.factor_id === effectiveRoot
      || f.root_factor_id === effectiveRoot
    )
  })
})

/** 整理出 root 节点（generation=1 / parent=null 那个）。 */
const rootNode = computed<Factor | null>(() => {
  const list = sameRoot.value
  return list.find(f => !f.parent_factor_id) ?? list[0] ?? null
})

/** factor_id → children 列表的索引，便于递归渲染。 */
const childrenMap = computed<Map<string, Factor[]>>(() => {
  const m = new Map<string, Factor[]>()
  for (const f of sameRoot.value) {
    if (!f.parent_factor_id) continue
    const arr = m.get(f.parent_factor_id) ?? []
    arr.push(f)
    m.set(f.parent_factor_id, arr)
  }
  // 同父代下按 generation 排序，generation 相同按 factor_id 字典序
  for (const arr of m.values()) {
    arr.sort((a, b) => (a.generation ?? 1) - (b.generation ?? 1) || a.factor_id.localeCompare(b.factor_id))
  }
  return m
})

/** 递归渲染节点：返回 vnode 列表（节点 + 子代缩进）。 */
function renderNode(factor: Factor, depth: number): any {
  const isCurrent = factor.factor_id === props.currentFactorId
  const isSota = !!factor.is_sota
  const children = childrenMap.value.get(factor.factor_id) ?? []

  const nodeContent: any[] = [
    // 缩进 + 连线（CSS class 处理）
    h('div', { class: 'lineage-line', style: { paddingLeft: `${depth * 24}px` } }, [
      depth > 0
        ? h('span', { class: 'lineage-connector', style: 'color:#999;margin-right:4px' }, '└─ ')
        : null,
      h(
        'a',
        {
          class: ['lineage-node', { current: isCurrent }],
          style: {
            cursor: 'pointer',
            fontFamily: 'monospace',
            color: isCurrent ? '#F0B90B' : '#5AC8FA',
            fontWeight: isCurrent ? 700 : 500,
            padding: '2px 6px',
            borderRadius: '3px',
            backgroundColor: isCurrent ? 'rgba(240,185,11,0.12)' : 'transparent',
          },
          onClick: () => router.push(`/factors/${factor.factor_id}`),
        },
        factor.factor_id,
      ),
      h(
        'span',
        { style: 'color:#848E9C;font-size:11px;margin-left:6px' },
        `v${factor.generation ?? 1}`,
      ),
      isSota
        ? h(
            NTag,
            { type: 'warning', size: 'tiny', bordered: false, style: 'margin-left:6px' },
            { default: () => '⭐ SOTA' },
          )
        : null,
      isCurrent
        ? h(
            NTag,
            { type: 'info', size: 'tiny', bordered: false, style: 'margin-left:6px' },
            { default: () => '当前' },
          )
        : null,
      h(
        'span',
        { style: 'color:#848E9C;font-size:11px;margin-left:8px' },
        factor.display_name || '',
      ),
    ]),
  ]

  if (children.length > 0) {
    nodeContent.push(...children.map(c => renderNode(c, depth + 1)))
  }
  return h('div', { class: 'lineage-subtree' }, nodeContent)
}

const tree = computed(() => {
  if (!rootNode.value) return null
  return renderNode(rootNode.value, 0)
})
</script>

<template>
  <div class="lineage-tree-root">
    <n-spin v-if="isLoading" size="small" />
    <n-empty v-else-if="!tree" description="无血缘信息（因子不存在或 root 链查不到）" />
    <component :is="tree" v-else />
  </div>
</template>

<style scoped>
.lineage-tree-root {
  font-size: 13px;
  line-height: 1.8;
}
.lineage-line {
  display: flex;
  align-items: center;
  gap: 0;
}
.lineage-node:hover {
  text-decoration: underline;
}
</style>
