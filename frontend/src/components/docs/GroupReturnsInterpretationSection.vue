<script setup lang="ts">
import { NCard, NSpace, NTag } from 'naive-ui'
import GroupReturnsChart from '@/components/charts/GroupReturnsChart.vue'
import { groupReturnsInterpretationFixture } from '@/pages/docs/fixtures/groupReturnsInterpretation'

const checklist: string[] = [
  '先看单调性：好图应当大体呈现“高分组在上、低分组在下”的有序梯度，且排序长期不反转。',
  '再看层间距离：好图顶底组应保持可辨识的收益间距；若各组长期挤在一起，分层信息通常不够强。',
  '重点看交叉频率：坏图常见频繁交叉与来回换位，说明因子排序不稳定，样本外延续性偏弱。',
  '最后看稳健性：好图的分层结构应跨阶段大体一致；若只在短窗口有效，往往更像偶然噪声。',
]

const goodConclusion =
  '结论：这是结构清晰的分层曲线。分组顺序稳定、层间距离可持续，说明因子排序具备可解释性和可交易性。'

const badConclusion =
  '结论：这是不稳定的分层曲线。分组频繁交叉且距离收敛，排序信号弱，实盘中很难形成稳定超额。'
</script>

<template>
  <n-card title="图表解读：分组累计净值（典型好图 vs 典型坏图）" size="small">
    <n-space vertical :size="16">
      <n-card size="small" embedded>
        <template #header>
          判别清单
        </template>
        <template #header-extra>
          <n-tag type="warning" size="small">
            先分层，后收益
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
          <group-returns-chart :data="groupReturnsInterpretationFixture.good" />
          <p class="conclusion conclusion--good">
            {{ goodConclusion }}
          </p>
        </n-card>

        <n-card title="典型坏图" size="small">
          <group-returns-chart :data="groupReturnsInterpretationFixture.bad" />
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
