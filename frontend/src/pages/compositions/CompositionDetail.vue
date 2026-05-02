<script setup lang="ts">
/**
 * 多因子合成详情页。
 * 展示：基础信息 + 合成因子评估指标 + 多空净值 + 分组收益 + 相关性热力图 +
 * per-factor IC 对比表 + 权重表（仅 ic_weighted 方法）。
 */
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NAlert, NDataTable, NGrid, NGridItem, NTag, NSpace,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useComposition } from '@/api/compositions'
import type { PerFactorIcEntry } from '@/api/compositions'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'
import ChartCard from '@/components/charts/ChartCard.vue'
import GroupReturnsChart from '@/components/charts/GroupReturnsChart.vue'
import EquityCurveChart from '@/components/charts/EquityCurveChart.vue'
import CorrHeatmap from '@/components/charts/CorrHeatmap.vue'

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useComposition(runId)
const { lookup: lookupPoolName } = usePoolNameMap()

const isRunning = computed(
  () => run.value?.status === 'pending' || run.value?.status === 'running',
)

const payload = computed(() => run.value?.payload ?? null)
const corr = computed(() => run.value?.corr_matrix ?? null)
const perFactorIc = computed(() => run.value?.per_factor_ic ?? null)
const weights = computed(() => run.value?.weights ?? null)

// 合成多空净值曲线（来自 payload.long_short_equity）
const equity = computed(() => payload.value?.long_short_equity ?? { dates: [], values: [] })

function fmtPct(v: any, digits = 2): string {
  if (v == null) return '-'
  return typeof v === 'number' ? (v * 100).toFixed(digits) + '%' : String(v)
}
function fmtNum(v: any, digits = 3): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}

const methodLabel = computed(() => {
  const m = run.value?.method
  if (m === 'equal') return '等权 (equal)'
  if (m === 'ic_weighted') return 'IC 加权 (ic_weighted)'
  if (m === 'orthogonal_equal') return '正交等权 (orthogonal_equal)'
  if (m === 'ml_lgb') return 'LightGBM 合成 (ml_lgb)'
  return m || '-'
})

// per-factor IC 对比表：合成 vs. 原因子。
interface IcRow {
  factor_id: string
  ic_mean: number | null
  ic_ir: number | null
  ic_win_rate: number | null
  ic_contribution: number | null
}
const icRows = computed<IcRow[]>(() => {
  const pf = perFactorIc.value
  const rows: IcRow[] = []
  if (pf) {
    for (const [fid, v] of Object.entries(pf as Record<string, PerFactorIcEntry>)) {
      rows.push({
        factor_id: fid,
        ic_mean: v.ic_mean,
        ic_ir: v.ic_ir,
        ic_win_rate: v.ic_win_rate,
        ic_contribution: v.ic_contribution ?? null,
      })
    }
  }
  // 合成因子一行放最前，突出"是否比任何单因子都强"。
  // 合成因子本身不是"贡献者"而是"结果"，contribution 留空。
  if (run.value?.status === 'success') {
    rows.unshift({
      factor_id: '📊 合成因子',
      ic_mean: run.value?.ic_mean ?? null,
      ic_ir: run.value?.ic_ir ?? null,
      ic_win_rate: run.value?.ic_win_rate ?? null,
      ic_contribution: null,
    })
  }
  return rows
})

const icColumns: DataTableColumns<IcRow> = [
  { title: '因子', key: 'factor_id', width: 200 },
  { title: 'IC 均值', key: 'ic_mean', width: 120, render: (r) => fmtNum(r.ic_mean, 4) },
  { title: 'IC_IR', key: 'ic_ir', width: 110, render: (r) => fmtNum(r.ic_ir) },
  { title: 'IC 胜率', key: 'ic_win_rate', width: 110, render: (r) => fmtPct(r.ic_win_rate) },
  {
    // 显示规则：合成因子那行 contribution=null → "-"；
    // orthogonal_equal 方法下 tooltip 提示"基于原始 IC 近似"。
    title: 'IC 贡献度',
    key: 'ic_contribution',
    width: 140,
    render: (r) => {
      if (r.ic_contribution == null) return '-'
      return fmtPct(r.ic_contribution, 1)
    },
  },
  {
    // LGB 特征重要度：仅 ml_lgb 方法且 payload.feature_importance 有该 factor_id 时显示。
    // 归一化条形图（max=100%）+ 原始数值，方便横向比较各子因子在 LightGBM 模型中的相对贡献。
    title: 'LGB Importance',
    key: 'lgb_importance',
    width: 130,
    render: (r) => {
      if (run.value?.method !== 'ml_lgb') return '-'
      const fi = (payload.value?.feature_importance ?? {}) as Record<string, number>
      const vals = Object.values(fi)
      if (vals.length === 0) return '-'
      const v = fi[r.factor_id]
      if (v == null) return '-'
      const max = Math.max(...vals, 1)
      const pct = ((v / max) * 100).toFixed(0)
      return h('div', { style: 'display:flex;align-items:center;gap:6px' }, [
        h(
          'div',
          {
            style: 'width:60px;height:8px;background:#eee;border-radius:2px;overflow:hidden',
          },
          h('div', {
            style: `width:${pct}%;height:100%;background:#F0B90B`,
          }),
        ),
        h('span', { style: 'color:#848E9C;font-size:11px' }, v.toFixed(1)),
      ])
    },
  },
]

// 权重表（ic_weighted 才有）
interface WeightRow {
  factor_id: string
  weight: number
}
const weightRows = computed<WeightRow[]>(() => {
  const w = weights.value
  if (!w) return []
  return Object.entries(w).map(([fid, v]) => ({
    factor_id: fid,
    weight: v as number,
  }))
})
const weightColumns: DataTableColumns<WeightRow> = [
  { title: '因子', key: 'factor_id', width: 220 },
  {
    title: '归一化权重',
    key: 'weight',
    render: (r) =>
      h(
        'span',
        {
          style: {
            color: r.weight < 0 ? '#F6465D' : '#1E2026',
            fontWeight: 600,
          },
        },
        (r.weight >= 0 ? '+' : '') + (r.weight * 100).toFixed(2) + '%',
      ),
  },
]

// 合成 vs. 最好单因子的对比提示。
const improveHint = computed<string | null>(() => {
  if (run.value?.status !== 'success') return null
  if (!perFactorIc.value) return null
  const composedIr = run.value?.ic_ir
  if (composedIr == null) return null
  const singles = Object.values(perFactorIc.value as Record<string, PerFactorIcEntry>)
    .map((v) => v.ic_ir)
    .filter((v): v is number => typeof v === 'number')
  if (singles.length === 0) return null
  const best = Math.max(...singles.map(Math.abs))
  const composedAbs = Math.abs(composedIr)
  if (best > 1e-9 && composedAbs > best * 1.05) {
    return `合成因子 IC_IR=${composedIr.toFixed(3)}，显著优于最佳单因子（|IC_IR|≈${best.toFixed(3)}），合成起到了预期的叠加效果。`
  }
  if (best > 1e-9 && composedAbs < best * 0.95) {
    return `合成因子 IC_IR=${composedIr.toFixed(3)}，低于最佳单因子（|IC_IR|≈${best.toFixed(3)}）。可能原因：因子间相关度过高（查看相关性矩阵）或方向不一致。`
  }
  return null
})
</script>

<template>
  <div>
    <n-page-header
      :title="`合成 ${runId.slice(0, 8)}...`"
      @back="router.back()"
      style="margin-bottom: 16px"
    >
      <template #extra>
        <status-badge v-if="run" :status="run.status" />
      </template>
    </n-page-header>

    <n-spin :show="isLoading && !run">
      <n-card v-if="run" title="基础信息" style="margin-bottom: 16px">
        <n-descriptions :column="3" bordered>
          <n-descriptions-item label="方法">{{ methodLabel }}</n-descriptions-item>
          <n-descriptions-item label="股票池">{{ lookupPoolName(run.pool_id) }}</n-descriptions-item>
          <n-descriptions-item label="日期">{{ run.start_date }} ~ {{ run.end_date }}</n-descriptions-item>
          <n-descriptions-item label="分组数">{{ run.n_groups }}</n-descriptions-item>
          <n-descriptions-item label="IC 权重前瞻期">{{ run.ic_weight_period }}</n-descriptions-item>
          <n-descriptions-item label="前瞻期">
            <span v-if="Array.isArray(run.forward_periods)">
              {{ (run.forward_periods as number[]).join(' / ') }}
            </span>
            <span v-else>{{ run.forward_periods }}</span>
          </n-descriptions-item>
          <n-descriptions-item label="因子清单" :span="3">
            <n-space :size="6">
              <n-tag
                v-for="it in (run.factor_items || [])"
                :key="it.factor_id"
                size="small"
                type="info"
                bordered
              >
                {{ it.factor_id }}
                <span v-if="it.factor_version" style="opacity: 0.7; margin-left: 4px">
                  v{{ it.factor_version }}
                </span>
              </n-tag>
            </n-space>
          </n-descriptions-item>
        </n-descriptions>

        <div v-if="isRunning" style="margin-top: 16px">
          <n-progress
            type="line"
            :percentage="run.progress || 0"
            :status="run.status === 'failed' ? 'error' : 'default'"
          />
        </div>
        <n-alert v-if="run.status === 'failed'" type="error" style="margin-top: 16px">
          <pre style="white-space: pre-wrap; font-size: 12px">{{ run.error_message }}</pre>
        </n-alert>
      </n-card>

      <n-card v-if="run?.status === 'success'" title="合成因子评估" style="margin-bottom: 16px">
        <n-descriptions :column="4" bordered>
          <n-descriptions-item label="IC 均值">{{ fmtNum(run.ic_mean, 4) }}</n-descriptions-item>
          <n-descriptions-item label="IC_IR">{{ fmtNum(run.ic_ir) }}</n-descriptions-item>
          <n-descriptions-item label="IC 胜率">{{ fmtPct(run.ic_win_rate) }}</n-descriptions-item>
          <n-descriptions-item label="IC t 统计量">{{ fmtNum(run.ic_t_stat, 2) }}</n-descriptions-item>
          <n-descriptions-item label="Rank IC 均值">{{ fmtNum(run.rank_ic_mean, 4) }}</n-descriptions-item>
          <n-descriptions-item label="Rank IC_IR">{{ fmtNum(run.rank_ic_ir) }}</n-descriptions-item>
          <n-descriptions-item label="多空 Sharpe">{{ fmtNum(run.long_short_sharpe) }}</n-descriptions-item>
          <n-descriptions-item label="多空年化">{{ fmtPct(run.long_short_annret) }}</n-descriptions-item>
          <n-descriptions-item label="换手率均值" :span="4">{{ fmtPct(run.turnover_mean) }}</n-descriptions-item>
        </n-descriptions>
      </n-card>

      <n-alert v-if="improveHint" type="info" style="margin-bottom: 16px">
        {{ improveHint }}
      </n-alert>

      <n-grid
        v-if="run?.status === 'success'"
        :cols="2"
        :x-gap="16"
        :y-gap="16"
        responsive="screen"
        item-responsive
        style="margin-bottom: 16px"
      >
        <n-grid-item span="1 m:1">
          <chart-card title="多空净值 (合成因子)">
            <equity-curve-chart :equity="equity" />
          </chart-card>
        </n-grid-item>
        <n-grid-item span="1 m:1">
          <chart-card title="相关性矩阵">
            <corr-heatmap
              v-if="corr && corr.factor_ids?.length"
              :factor-ids="corr.factor_ids"
              :values="corr.values"
            />
            <n-alert v-else type="default">暂无相关性数据</n-alert>
          </chart-card>
        </n-grid-item>
        <n-grid-item span="2 m:2">
          <chart-card title="分组收益 (合成因子)">
            <group-returns-chart
              v-if="payload?.group_returns"
              :data="payload.group_returns"
            />
          </chart-card>
        </n-grid-item>
      </n-grid>

      <n-card v-if="run?.status === 'success' && icRows.length > 0" title="合成 vs. 单因子 IC 对比" style="margin-bottom: 16px">
        <n-alert type="default" size="small" style="margin-bottom: 12px">
          <span style="font-size: 12px; opacity: 0.85">
            <b>IC 贡献度</b> = |IC × 权重| 归一化后的占比，回答"合成预测力具体由谁在贡献"。
            <span v-if="run.method === 'equal' || run.method === 'orthogonal_equal'">
              当前方法权重均为 1/N，故贡献度正比于 |IC|。
            </span>
            <span v-if="run.method === 'orthogonal_equal'">
              （正交化后子因子的独立 IC 未单独计算，此处用原始 IC 近似。）
            </span>
            <span v-if="run.method === 'ic_weighted'">
              当前方法权重已由 IC 决定，故贡献度正比于 IC²，强者占比更大。
            </span>
          </span>
        </n-alert>
        <n-data-table
          :columns="icColumns"
          :data="icRows"
          :bordered="false"
          :single-line="false"
          :row-key="(row: any) => row.factor_id"
        />
      </n-card>

      <n-card v-if="run?.method === 'ic_weighted' && weightRows.length > 0" title="因子权重 (ic_weighted)">
        <n-data-table
          :columns="weightColumns"
          :data="weightRows"
          :bordered="false"
          :single-line="false"
          :row-key="(row: any) => row.factor_id"
        />
      </n-card>
    </n-spin>
  </div>
</template>
