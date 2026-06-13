<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import type { PatternMatch } from '../../api/patternSearch'

use([CanvasRenderer, LineChart, GridComponent])

import { reactive } from 'vue'

const props = defineProps<{
  matches: PatternMatch[]
  labelable?: boolean
  // 来自后端已存标注的初始高亮（symbol → 1/0）；切页回来时据此恢复 👍/👎。
  initialLabels?: Record<string, number>
}>()
const emit = defineEmits<{
  (e: 'open', m: PatternMatch): void
  (e: 'label', m: PatternMatch, value: number): void   // 1=正例 / 0=反例
}>()

// 本次会话内点过的标注（即时反馈）；与后端来的 initialLabels 合并，本地优先。
const localLabeled = reactive<Record<string, number>>({})
const labeled = computed<Record<string, number>>(() => ({
  ...(props.initialLabels ?? {}),
  ...localLabeled,
}))
function onThumb(m: PatternMatch, value: number) {
  localLabeled[m.label] = value
  emit('label', m, value)
}

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
      <div v-if="labelable" class="label-btns" @click.stop>
        <button class="thumb up" :class="{ active: labeled[m.label] === 1 }"
          title="这就是我要的（正例）" @click="onThumb(m, 1)">👍</button>
        <button class="thumb down" :class="{ active: labeled[m.label] === 0 }"
          title="像但我不要（反例）" @click="onThumb(m, 0)">👎</button>
      </div>
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
.label-btns { display: flex; gap: 4px; flex: none; }
.thumb { border: 1.5px solid transparent; background: transparent; cursor: pointer; font-size: 16px; padding: 2px 5px; border-radius: 6px; opacity: 0.5; transition: all .12s; }
.thumb:hover { background: rgba(0,0,0,0.08); opacity: 1; }
.thumb.up.active { opacity: 1; border-color: #18a058; background: rgba(24,160,88,0.14); }
.thumb.down.active { opacity: 1; border-color: #d03050; background: rgba(208,48,80,0.14); }
.empty { padding: 24px; text-align: center; opacity: 0.5; }
</style>
