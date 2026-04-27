<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import TurnoverChart from '@/components/charts/TurnoverChart.vue'
import { turnoverInterpretationFixture } from '@/pages/docs/fixtures/turnoverInterpretation'

const checklist: string[] = [
  '先看均值水平：日频策略 30-50% 健康，50-70% 偏高，> 70% 通常被交易成本吃光 alpha。',
  '再看波动幅度：均值低但波动剧烈（如 20% 与 80% 来回）说明信号方向频繁反转，与"低换手"语义相悖。',
  '注意尖峰日：单日跳到 90%+ 的孤立点常对应"信号方向整体翻转"或"股票池剧烈变动"，需对照 IC 时序看是否同步。',
  '结合调仓周期：rebalance_period=5 时 ≈ 周频，换手应进一步降到 < 25%；月频应在 < 10%。',
]

const goodConclusion =
  '结论：换手率稳定在 30-40% 区间，交易成本可控；这种因子在 3-5bp 成本下仍可保持 alpha，便于实盘落地。'

const badConclusion =
  '结论：换手率长期在 80%+ 且剧烈波动，意味着仓位每天都在大幅重构；除非 IC 极强（≥ 0.06），否则成本会把超额吃光。'
</script>

<template>
  <n-card title="图表解读：换手率（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先看均值，再看波动
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
          <turnover-chart :series="turnoverInterpretationFixture.good" />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图" size="small">
          <turnover-chart :series="turnoverInterpretationFixture.bad" />
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
