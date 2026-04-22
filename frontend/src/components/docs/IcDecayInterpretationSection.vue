<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import IcDecayChart from '@/components/charts/IcDecayChart.vue'
import { icDecayInterpretationFixture } from '@/pages/docs/fixtures/icDecayInterpretation'

const checklist: string[] = [
  '先看峰值位置与幅度：IC 与 Rank IC 在短前瞻期应同号且明显为正，峰值越靠前越符合日频信号特征。',
  '再看衰减形态：好图应当平滑下行、缓慢衰减；坏图常见尖峰后快速塌陷，甚至 3~5 日内就失真。',
  '重点看反号：中长前瞻期若频繁跌到 0 以下或反复穿越零轴，通常意味着噪声主导，交易方向不稳定。',
  '对照 IC 与 Rank IC：两条线若大体平行，说明排序与线性相关一致；若背离显著，常见于尾部极端值污染。',
  '结合调仓频率判断：半衰期越短，对高频调仓依赖越强；半衰期过短通常无法覆盖真实交易成本。',
]

const goodConclusion =
  '结论：这是可交易的衰减结构。两条曲线同号、平滑下降且未快速反号，说明信号有连续性，可支撑低频到中频调仓。'

const badConclusion =
  '结论：这是高风险结构。短期尖峰后快速衰减并出现反号，稳定性弱，实盘中很容易被成本和噪声吃掉。'
</script>

<template>
  <n-card title="图表解读：IC 衰减（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先趋势，后细节
          </n-tag>
        </template>
        <ul class="checklist">
          <li v-for="item in checklist" :key="item">
            {{ item }}
          </li>
        </ul>
      </n-card>

      <div class="chart-grid">
        <n-card title="典型好图" size="small">
          <ic-decay-chart
            :ic="icDecayInterpretationFixture.good.ic"
            :rank-ic="icDecayInterpretationFixture.good.rankIc"
          />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图" size="small">
          <ic-decay-chart
            :ic="icDecayInterpretationFixture.bad.ic"
            :rank-ic="icDecayInterpretationFixture.bad.rankIc"
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
.checklist {
  margin: 0;
  padding: 0;
  list-style: none;
}

.checklist li {
  position: relative;
  padding-left: 18px;
  margin-bottom: 10px;
  line-height: 1.65;
  color: #303133;
}

.checklist li:last-child {
  margin-bottom: 0;
}

.checklist li::before {
  content: '✓';
  position: absolute;
  left: 0;
  top: 0;
  color: #18a058;
  font-weight: 700;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.conclusion {
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px dashed #e5e6eb;
  line-height: 1.7;
  font-size: 13px;
}

.conclusion--good {
  color: #0e7a36;
}

.conclusion--bad {
  color: #c12f2f;
}

@media (max-width: 960px) {
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
