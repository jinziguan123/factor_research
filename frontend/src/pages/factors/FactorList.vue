<script setup lang="ts">
/**
 * 因子库列表页
 * 按 category 分组展示，NCard grid 布局
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NPageHeader, NCard, NGrid, NGridItem, NTag, NSpin, NEmpty } from 'naive-ui'
import { useFactors } from '@/api/factors'
import type { Factor } from '@/api/factors'

const router = useRouter()
const { data: factors, isLoading } = useFactors()

// 分类中文映射
const categoryLabels: Record<string, string> = {
  reversal: '反转',
  momentum: '动量',
  volatility: '波动率',
  volume: '成交量',
  custom: '自定义',
}

// 按 category 分组
const grouped = computed(() => {
  const groups: Record<string, Factor[]> = {}
  for (const f of factors.value ?? []) {
    const cat = f.category || 'custom'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(f)
  }
  return groups
})
</script>

<template>
  <div>
    <n-page-header title="因子库" style="margin-bottom: 16px" />

    <n-spin :show="isLoading">
      <n-empty v-if="!isLoading && !(factors ?? []).length" description="暂无因子" />

      <div v-for="(items, category) in grouped" :key="category" style="margin-bottom: 24px">
        <h3 style="margin-bottom: 12px; color: #1E2026">
          {{ categoryLabels[category] ?? category }}
        </h3>
        <n-grid :cols="3" :x-gap="16" :y-gap="16" responsive="screen">
          <n-grid-item v-for="factor in items" :key="factor.factor_id">
            <n-card
              hoverable
              style="cursor: pointer"
              @click="router.push(`/factors/${factor.factor_id}`)"
            >
              <template #header>
                <span style="font-size: 15px; font-weight: 600">{{ factor.display_name }}</span>
              </template>
              <template #header-extra>
                <n-tag size="small" :bordered="false">{{ categoryLabels[factor.category] ?? factor.category }}</n-tag>
              </template>
              <div style="color: #848E9C; font-size: 13px; line-height: 1.6">
                {{ factor.description || '暂无描述' }}
              </div>
              <div style="margin-top: 8px; font-size: 12px; color: #848E9C">
                版本 v{{ factor.version ?? 1 }}
              </div>
            </n-card>
          </n-grid-item>
        </n-grid>
      </div>
    </n-spin>
  </div>
</template>
