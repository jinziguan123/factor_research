<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import CorrHeatmap from '@/components/charts/CorrHeatmap.vue'
import { corrHeatmapInterpretationFixture } from '@/pages/docs/fixtures/corrHeatmapInterpretation'

const checklist: string[] = [
  '先看对角外平均水平：好图非对角元素绝对值多在 0.3 以内，意味着因子互补；坏图普遍 > 0.6，合成几乎等价于单因子。',
  '关注高相关红块：单元格颜色越深越红 / 越亮黄，意味着两个因子排序高度一致。出现 ≥ 0.7 的格子时，建议二选一或正交化。',
  '关注负相关：< -0.5 的负相关值得保留——多因子里负相关的"对冲性"可让合成 IC 比任一单因子都强。',
  '与"合成 vs. 单因子 IC 对比"互看：若合成 IC 不超过最佳单因子 × 1.05，且热图普遍高相关，说明合成无效，应换更互补的因子。',
]

const goodConclusion =
  '结论：5 个因子彼此相关性低，捕捉的是不同维度（动量/反转/波动/活跃度/质量）；等权合成会带来真实的"信息叠加"。'

const badConclusion =
  '结论：5 个因子全是动量族，相关性集中在 0.7-0.9，合成≈单因子。建议（a）把 momentum_*  族只留一个；（b）补入反转/波动/质量等不同维度的因子。'
</script>

<template>
  <n-card title="图表解读：因子相关性热图（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先看对角外水平，再看高相关块
          </n-tag>
        </template>
        <ul class="checklist">
          <li v-for="item in checklist" :key="item">
            {{ item }}
          </li>
        </ul>
      </n-card>

      <div class="chart-grid">
        <n-card title="典型好图（互补因子组合）" size="small">
          <corr-heatmap
            :factor-ids="corrHeatmapInterpretationFixture.good.factor_ids"
            :values="corrHeatmapInterpretationFixture.good.values"
          />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图（同源因子族）" size="small">
          <corr-heatmap
            :factor-ids="corrHeatmapInterpretationFixture.bad.factor_ids"
            :values="corrHeatmapInterpretationFixture.bad.values"
          />
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
