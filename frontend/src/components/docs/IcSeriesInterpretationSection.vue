<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import IcSeriesChart from '@/components/charts/IcSeriesChart.vue'
import { icSeriesInterpretationFixture } from '@/pages/docs/fixtures/icSeriesInterpretation'

const checklist: string[] = [
  '先看累计 IC 趋势：好图应当持续向上，斜率代表"日均 IC × 时间"；坏图常见横盘或反复扭头。',
  '再看每日 IC 胜率：柱状条目正负占比 ≥ 60% 偏向同号是好图；坏图正负各半，无方向。',
  '重点看 MA 折线：5/20 日均线应当稳在 0 上方且较平滑；若 MA 频繁穿越零轴，意味着信号高频反向。',
  '区分异常日与系统性反号：单日 IC 跳到 ±0.06 是常见波动；连续 3+ 日同向反号才需警惕。',
]

const goodConclusion =
  '结论：这是稳定的预测力曲线。每日 IC 多数为正，累计 IC 持续向上，MA 在 0 上方平稳——支撑日频到周频调仓。'

const badConclusion =
  '结论：这是噪声主导的曲线。每日 IC 高频反号、累计 IC 横盘震荡，意味着没有可交易的方向；样本外通常加速衰减。'
</script>

<template>
  <n-card title="图表解读：IC 时序（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先看累计趋势，再看胜率
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
          <ic-series-chart
            :series="icSeriesInterpretationFixture.good"
            title="日 IC + 累计 IC"
          />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图" size="small">
          <ic-series-chart
            :series="icSeriesInterpretationFixture.bad"
            title="日 IC + 累计 IC"
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
.checklist li:last-child { margin-bottom: 0; }
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
.conclusion--good { color: #0e7a36; }
.conclusion--bad { color: #c12f2f; }
@media (max-width: 960px) {
  .chart-grid { grid-template-columns: 1fr; }
}
</style>
