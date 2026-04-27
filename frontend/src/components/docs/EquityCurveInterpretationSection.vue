<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'
import { equityCurveInterpretationFixture } from '@/pages/docs/fixtures/equityCurveInterpretation'

const checklist: string[] = [
  '先看终值高度：从 1 起步的累积净值，>1.4 是较好水平、< 1.0 直接淘汰；同期沪深 300 走势可作为参照。',
  '再看回撤深度：底图蓝色阴影即 underwater（水下）回撤；好图回撤多在 -10% 内，坏图常见 -20% 以上。',
  '关键看回撤恢复：净值跌下去多久回到前高？2-3 周恢复算健康，超过 1 季度未恢复说明因子在该制度下失效。',
  '区分系统性下跌与策略问题：若大盘也同期下跌，回撤的"超额回撤"才是真问题；本平台目前展示绝对净值，需要对照大盘判断。',
]

const goodConclusion =
  '结论：这是稳健的资金曲线。终值 > 1.4 且回撤浅、恢复快，意味着策略在这段窗口里持续创造超额收益。'

const badConclusion =
  '结论：这是高风险曲线。先涨后断崖、长期处于水下，说明因子在某个制度切换时失效；需要做样本外切分确认是否系统性问题。'
</script>

<template>
  <n-card title="图表解读：多空净值曲线（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先看终值，再看回撤
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
          <equity-curve-chart :equity="equityCurveInterpretationFixture.good" />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图" size="small">
          <equity-curve-chart :equity="equityCurveInterpretationFixture.bad" />
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
