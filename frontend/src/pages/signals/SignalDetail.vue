<script setup lang="ts">
/**
 * 实盘信号详情页。
 *
 * 布局：
 * - 顶部：基础信息（method / 股票池 / as_of_time / use_realtime / spot_meta）
 * - 中部：top 组排名表（NDataTable，每只票含因子综合值 + 子因子分解）
 * - 下部：bottom 组排名表（同结构）
 * - 侧栏：spot_meta 摘要 + 多因子时显示 weights / per_factor_ic 表
 *
 * pending/running 时由 useSignal 自动 1.5s 轮询；success/failed 后停。
 */
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NAlert, NDataTable, NGrid, NGridItem, NTag, NSpace,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useSignal } from '@/api/signals'
import type { SignalHolding } from '@/api/signals'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const route = useRoute()
const router = useRouter()

const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useSignal(runId)
const { lookup: lookupPoolName } = usePoolNameMap()

const isRunning = computed(
  () => run.value?.status === 'pending' || run.value?.status === 'running',
)
const payload = computed(() => run.value?.payload ?? null)

function fmtNum(v: any, digits = 4): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(digits) : String(v)
}
function fmtPct(v: any, digits = 2): string {
  if (v == null) return '-'
  return typeof v === 'number' ? (v * 100).toFixed(digits) + '%' : String(v)
}
function fmtPrice(v: any): string {
  if (v == null) return '-'
  return typeof v === 'number' ? v.toFixed(2) : String(v)
}

const methodLabel = computed(() => {
  const m = run.value?.method
  if (m === 'single') return '单因子 (single)'
  if (m === 'equal') return '等权 (equal)'
  if (m === 'ic_weighted') return 'IC 加权 (ic_weighted)'
  if (m === 'orthogonal_equal') return '正交等权 (orthogonal_equal)'
  return m || '-'
})

// 子因子列：从第一只票的 breakdown 提取列名
const subfactorIds = computed<string[]>(() => {
  const top = payload.value?.top ?? []
  if (top.length === 0) return []
  const breakdown = top[0].factor_value_breakdown || {}
  return Object.keys(breakdown)
})

function makeHoldingColumns(): DataTableColumns<SignalHolding> {
  const cols: DataTableColumns<SignalHolding> = [
    {
      title: '#',
      key: 'rank',
      width: 50,
      render: (_row, idx) => idx + 1,
    },
    {
      title: '代码',
      key: 'symbol',
      width: 110,
      render: (row) => h('code', { style: 'font-size: 12px' }, row.symbol),
    },
    {
      title: '当下报价',
      key: 'last_price',
      width: 100,
      render: (row) => fmtPrice(row.last_price),
    },
    {
      title: '当下涨跌',
      key: 'pct_chg',
      width: 100,
      render: (row) => {
        const pct = row.pct_chg
        if (pct == null) return '-'
        const color = pct > 0 ? '#F6465D' : pct < 0 ? '#0ECB81' : '#666'
        return h('span', { style: { color, fontWeight: 600 } }, fmtPct(pct))
      },
    },
    {
      title: '因子综合值',
      key: 'factor_value_composite',
      width: 130,
      render: (row) => fmtNum(row.factor_value_composite),
    },
  ]
  // 多因子：动态加每个子因子列
  for (const fid of subfactorIds.value) {
    cols.push({
      title: fid,
      key: `subfactor_${fid}`,
      width: 110,
      render: (row) => fmtNum(row.factor_value_breakdown?.[fid]),
    })
  }
  return cols
}

const holdingColumns = computed(() => makeHoldingColumns())

// 多因子时显示子因子 IC + 贡献度
interface IcRow {
  factor_id: string
  ic_mean: number | null
  ic_ir: number | null
  ic_win_rate: number | null
  ic_contribution: number | null
  weight: number | null
}
const icRows = computed<IcRow[]>(() => {
  const pf = payload.value?.per_factor_ic
  const w = payload.value?.weights ?? null
  if (!pf) return []
  return Object.entries(pf).map(([fid, v]) => ({
    factor_id: fid,
    ic_mean: v.ic_mean,
    ic_ir: v.ic_ir,
    ic_win_rate: v.ic_win_rate,
    ic_contribution: v.ic_contribution ?? null,
    weight: w?.[fid] ?? null,
  }))
})

const icColumns: DataTableColumns<IcRow> = [
  { title: '因子', key: 'factor_id', width: 200 },
  { title: 'IC 均值', key: 'ic_mean', width: 110, render: (r) => fmtNum(r.ic_mean) },
  { title: 'IC_IR', key: 'ic_ir', width: 110, render: (r) => fmtNum(r.ic_ir, 3) },
  { title: 'IC 胜率', key: 'ic_win_rate', width: 100, render: (r) => fmtPct(r.ic_win_rate) },
  {
    title: '权重',
    key: 'weight',
    width: 100,
    render: (r) => {
      if (r.weight == null) return '-'
      const color = r.weight < 0 ? '#F6465D' : '#1E2026'
      return h('span', { style: { color, fontWeight: 600 } }, fmtPct(r.weight))
    },
  },
  {
    title: 'IC 贡献度',
    key: 'ic_contribution',
    width: 110,
    render: (r) => fmtPct(r.ic_contribution, 1),
  },
]
</script>

<template>
  <div>
    <n-page-header
      :title="`信号 ${runId.slice(0, 8)}...`"
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
          <n-descriptions-item label="触发时刻">{{ run.as_of_time }}</n-descriptions-item>
          <n-descriptions-item label="当日">{{ run.as_of_date }}</n-descriptions-item>
          <n-descriptions-item label="分组数">{{ run.n_groups }}</n-descriptions-item>
          <n-descriptions-item label="IC 回看期">
            {{ run.method === 'ic_weighted' ? `${run.ic_lookback_days} 天` : '—' }}
          </n-descriptions-item>
          <n-descriptions-item label="实时数据">
            {{ run.use_realtime ? '已开启' : '关闭（用昨日 close）' }}
          </n-descriptions-item>
          <n-descriptions-item label="涨跌停过滤">
            {{ run.filter_price_limit ? '已开启' : '关闭' }}
          </n-descriptions-item>
          <n-descriptions-item label="持仓数">
            top {{ run.n_holdings_top ?? '-' }} / bot {{ run.n_holdings_bot ?? '-' }}
          </n-descriptions-item>
          <n-descriptions-item label="因子清单" :span="3">
            <n-space :size="6">
              <n-tag
                v-for="it in (run.factor_items || [])"
                :key="it.factor_id"
                size="small" type="info" bordered
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

      <n-alert
        v-if="payload?.spot_meta"
        :type="payload.spot_meta.use_realtime ? 'success' : 'warning'"
        :show-icon="false"
        style="margin-bottom: 16px"
      >
        <span style="font-size: 13px">
          <b>spot 摘要：</b>
          <span v-if="payload.spot_meta.use_realtime">
            实时模式，快照时刻 {{ payload.spot_meta.snapshot_at }}，
            覆盖 {{ payload.spot_meta.n_spot_rows }} / {{ payload.spot_meta.n_symbols_total }} 只票。
          </span>
          <span v-else>
            降级到昨日 close 模式（spot 数据不可用或陈旧）。
          </span>
        </span>
      </n-alert>

      <n-grid
        v-if="run?.status === 'success'"
        :cols="2" :x-gap="16" :y-gap="16"
        responsive="screen" item-responsive
        style="margin-bottom: 16px"
      >
        <n-grid-item span="2 m:1">
          <n-card title="多头组（top）" size="small">
            <n-data-table
              :columns="holdingColumns"
              :data="payload?.top ?? []"
              :bordered="false"
              :single-line="false"
              :row-key="(row: any) => row.symbol"
              :max-height="500"
            />
          </n-card>
        </n-grid-item>
        <n-grid-item span="2 m:1">
          <n-card title="空头组（bottom）" size="small">
            <n-data-table
              :columns="holdingColumns"
              :data="payload?.bottom ?? []"
              :bordered="false"
              :single-line="false"
              :row-key="(row: any) => row.symbol"
              :max-height="500"
            />
          </n-card>
        </n-grid-item>
      </n-grid>

      <n-card
        v-if="run?.status === 'success' && icRows.length > 0"
        title="子因子 IC + 权重 + 贡献度"
        size="small"
        style="margin-bottom: 16px"
      >
        <n-alert type="default" size="small" style="margin-bottom: 12px">
          <span style="font-size: 12px; opacity: 0.85">
            <b>IC 贡献度</b> = |IC × 权重| 归一化占比。回答"合成预测力具体由谁在贡献"。
            equal / orthogonal_equal 的权重视为 1/N（贡献度正比 |IC|）；
            ic_weighted 的权重已由 IC 决定（贡献度正比 IC²）。
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
    </n-spin>
  </div>
</template>
