<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import ValueHistogram from '@/components/charts/ValueHistogram.vue'
import { valueHistogramInterpretationFixture } from '@/pages/docs/fixtures/valueHistogramInterpretation'

const checklist: string[] = [
  '先看整体形状：好图近似单峰钟形（正态 / 略偏态），分布平滑；坏图常见双峰、长尾、或大量值堆在 0 附近的尖刺。',
  '再看尾部厚度：极端 bin（|x| > 2.5）占比应 < 5%；若极端尾占 > 10%，IC 容易被尾部主导，Pearson 与 Rank IC 会显著背离。',
  '关注双峰：中间稀疏 + 两侧高峰通常源于非线性变换（如 sign(x) * |x|^p）或硬阈值切分；后续 qcut 分组容易把"中性票"也分到极端组。',
  '关注尖刺：单个 bin 计数远高于邻居，常因为很多票的因子值相同（如 0、+1、-1）。检查是否有 fillna(0)、winsorize 把尾部砸成同一值。',
]

const goodConclusion =
  '结论：分布近似正态，尾部薄、无双峰，是已 z-score / winsorize 处理过的健康因子值。下游 qcut 分组语义清晰、Pearson 与 Rank IC 通常一致。'

const badConclusion =
  '结论：双峰 + 右长尾，意味着 (a) 因子公式存在非线性裂解，或 (b) 极端值未做 winsorize。需要先看是否有少量票的因子值占据 5% 以上的尾部 bin。'
</script>

<template>
  <n-card title="图表解读：因子值分布（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先看形状，再看尾部
          </n-tag>
        </template>
        <ul class="checklist">
          <li v-for="item in checklist" :key="item">
            {{ item }}
          </li>
        </ul>
      </n-card>

      <div class="chart-grid">
        <n-card title="典型好图（近似正态）" size="small">
          <value-histogram :data="valueHistogramInterpretationFixture.good" />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图（双峰 + 长尾）" size="small">
          <value-histogram :data="valueHistogramInterpretationFixture.bad" />
          <p class="conclusion conclusion--bad">
            {{ badConclusion }}
          </p>
        </n-card>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
.checklist { margin: 0; padding: 0; list-style: none; }
.checklist li { position: relative; padding-left: 18px; margin-bottom: 10px; line-height: 1.65; color: #303133; }
.checklist li:last-child { margin-bottom: 0; }
.checklist li::before { content: '✓'; position: absolute; left: 0; top: 0; color: #18a058; font-weight: 700; }
.chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.conclusion { margin: 12px 0 0; padding-top: 10px; border-top: 1px dashed #e5e6eb; line-height: 1.7; font-size: 13px; }
.conclusion--good { color: #0e7a36; }
.conclusion--bad { color: #c12f2f; }
@media (max-width: 960px) { .chart-grid { grid-template-columns: 1fr; } }
</style>
