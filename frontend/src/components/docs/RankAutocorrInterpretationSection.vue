<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import RankAutocorrChart from '@/components/charts/RankAutocorrChart.vue'
import { rankAutocorrInterpretationFixture } from '@/pages/docs/fixtures/rankAutocorrInterpretation'

const checklist: string[] = [
  '先看均值水平：相邻日因子排名的相关系数 ≥ 0.7 算"信号慢变"，0.4-0.7 中等，< 0.3 接近随机重排。',
  '再看波动：好图应保持在窄带内（如 0.75-0.88）；坏图常见 ±0.5 间剧烈跳变，意味着排序不稳定。',
  '关注负值：autocorr < 0 表示"今天的多头组明天变空头组"，与因子定义自相矛盾，通常是公式 bug 或量级溢出。',
  '与换手率交叉验证：autocorr 高 → 换手低；若两者方向不一致，说明 qcut 边界附近样本剧烈洗牌。',
]

const goodConclusion =
  '结论：rank autocorr 稳定在 0.8 附近，意味着持仓排序"昨日 ≈ 今日"，换手低、信号可持续——典型周/月频可交易因子的特征。'

const badConclusion =
  '结论：rank autocorr 在 ±0.5 间反复横跳，排名几乎每日重洗。即便 IC 短期为正，实盘也无法维持仓位。'
</script>

<template>
  <n-card title="图表解读：因子排名自相关（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先看均值，再看跳变
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
          <rank-autocorr-chart :series="rankAutocorrInterpretationFixture.good" />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图" size="small">
          <rank-autocorr-chart :series="rankAutocorrInterpretationFixture.bad" />
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
