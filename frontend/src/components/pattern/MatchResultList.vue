<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import type { PatternMatch } from '../../api/patternSearch'

use([CanvasRenderer, LineChart, GridComponent])

const props = defineProps<{ matches: PatternMatch[] }>()
const emit = defineEmits<{ (e: 'open', m: PatternMatch): void }>()

function sparkOption(curve: number[]) {
  return {
    animation: false,
    grid: { left: 2, right: 2, top: 2, bottom: 2 },
    xAxis: { type: 'category', show: false, data: curve.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{ type: 'line', data: curve, showSymbol: false, lineStyle: { width: 1.5 } }],
  }
}
const rows = computed(() => props.matches)
</script>

<template>
  <div class="match-list">
    <div v-if="rows.length === 0" class="empty">未找到相似图形</div>
    <div v-for="m in rows" :key="m.label" class="match-row" @click="emit('open', m)">
      <v-chart class="spark" :option="sparkOption(m.curve)" autoresize />
      <div class="meta">
        <div class="label">{{ m.label }}</div>
        <div class="sub">{{ m.start_date }} ~ {{ m.end_date }} · {{ m.scale }}日</div>
        <div v-if="m.sub_scores && m.sub_scores.length > 1" class="subscores">
          <span v-for="(s, i) in m.sub_scores" :key="i">图{{ i + 1 }} {{ (s * 100).toFixed(0) }}%</span>
        </div>
      </div>
      <div class="score">{{ (m.score * 100).toFixed(1) }}%</div>
    </div>
  </div>
</template>

<style scoped>
.match-row { display: flex; align-items: center; gap: 12px; padding: 8px; cursor: pointer; border-bottom: 1px solid var(--n-border-color, #eee); }
.match-row:hover { background: rgba(0,0,0,0.03); }
.spark { width: 120px; height: 40px; flex: none; }
.meta { flex: 1; min-width: 0; }
.label { font-weight: 600; }
.sub { font-size: 12px; opacity: 0.6; }
.subscores { font-size: 11px; opacity: 0.55; display: flex; gap: 8px; margin-top: 2px; }
.score { font-variant-numeric: tabular-nums; font-weight: 700; color: #e6584a; }
.empty { padding: 24px; text-align: center; opacity: 0.5; }
</style>
