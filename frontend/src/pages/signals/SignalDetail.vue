<script setup lang="ts">
/**
 * 实盘信号详情页。
 *
 * 布局：
 * - 顶部：实时模式横幅（仅 use_realtime=1 时显示，红色醒目）
 * - 基础信息（method / 股票池 / as_of_time / use_realtime / top_n / spot_meta）
 * - 快速重跑卡片：复制原 config，可调 use_realtime / filter_price_limit / top_n / n_groups
 * - top 组 / bottom 组排名表（含因子综合值 + 子因子分解）
 *
 * pending/running 时由 useSignal 自动 1.5s 轮询；success/failed 后停。
 */
import { computed, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NAlert, NDataTable, NGrid, NGridItem, NTag, NSpace,
  NButton, NSelect, NSwitch, NIcon, NDivider, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useSignal, useCreateSignal } from '@/api/signals'
import type { SignalHolding } from '@/api/signals'
import {
  useSubscriptions, useCreateSubscription, useUpdateSubscription,
  useDeleteSubscription,
} from '@/api/signal_subscriptions'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useSignal(runId)
const { lookup: lookupPoolName } = usePoolNameMap()

// 快速重跑：复制原 config + 4 个可调旋钮
const rerunUseRealtime = ref(true)
const rerunFilterPriceLimit = ref(true)
const rerunTopN = ref<number>(20)  // 0 = sentinel "全部"
const rerunNGroups = ref(5)
const topNOptions = [
  { label: 'Top 5', value: 5 },
  { label: 'Top 10', value: 10 },
  { label: 'Top 20', value: 20 },
  { label: 'Top 50', value: 50 },
  { label: 'Top 100', value: 100 },
  { label: '全部（qcut 顶组所有）', value: 0 },
]
// run 加载完后初始化重跑表单为原值
watch(run, (r) => {
  if (!r) return
  rerunUseRealtime.value = !!r.use_realtime
  rerunFilterPriceLimit.value = !!r.filter_price_limit
  rerunTopN.value = r.top_n ?? 0  // null → 0 sentinel
  rerunNGroups.value = r.n_groups
}, { immediate: true })

const createSignalMut = useCreateSignal()

// 订阅相关：找当前 run 配置对应的订阅（如果有），用于 toggle 状态
const { data: allSubs } = useSubscriptions()
const matchedSubscription = computed(() => {
  // 匹配规则：当前 run 是否被某订阅"产出过"——last_run_id 指向当前 run
  // 或订阅的 factor_items + pool + method 与当前 run 一致（找最新创建的）
  const subs = allSubs.value ?? []
  const r = run.value
  if (!r) return null
  // 优先：last_run_id == 当前 run
  const exact = subs.find(s => s.last_run_id === r.run_id)
  if (exact) return exact
  // 次选：配置匹配（最新的一条）
  const sameConfig = subs
    .filter(s =>
      s.pool_id === r.pool_id
      && s.method === r.method
      && s.factor_items.length === r.factor_items.length
      && s.factor_items.every((it, i) =>
        it.factor_id === r.factor_items[i]?.factor_id
      )
    )
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
  return sameConfig[0] ?? null
})

const refreshIntervalOptions = [
  { label: '1 分钟', value: 60 },
  { label: '3 分钟', value: 180 },
  { label: '5 分钟', value: 300 },
  { label: '10 分钟', value: 600 },
  { label: '30 分钟', value: 1800 },
]

const createSubMut = useCreateSubscription()
const updateSubMut = useUpdateSubscription()
const deleteSubMut = useDeleteSubscription()

const subRefreshIntervalEdit = ref<number>(300)
watch(matchedSubscription, (s) => {
  if (s) subRefreshIntervalEdit.value = s.refresh_interval_sec
}, { immediate: true })

async function handleEnableMonitoring() {
  if (!run.value) return
  const r = run.value
  const body = {
    factor_items: r.factor_items.map((it: any) => ({
      factor_id: it.factor_id,
      params: it.params ?? null,
    })),
    method: r.method,
    pool_id: r.pool_id,
    n_groups: r.n_groups,
    ic_lookback_days: r.ic_lookback_days,
    filter_price_limit: !!r.filter_price_limit,
    top_n: r.top_n,
    refresh_interval_sec: subRefreshIntervalEdit.value,
  }
  try {
    const res = await createSubMut.mutateAsync({ body, fromRunId: r.run_id })
    message.success(`已开启实盘监控 (订阅 ${res.subscription_id.slice(0, 8)})`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '开启失败')
  }
}

async function handleToggleActive(newActive: boolean) {
  const sub = matchedSubscription.value
  if (!sub) return
  try {
    await updateSubMut.mutateAsync({
      id: sub.subscription_id,
      body: { is_active: newActive },
    })
    message.success(newActive ? '订阅已恢复' : '订阅已暂停')
  } catch (e: any) {
    message.error(e?.message || '切换失败')
  }
}

async function handleUpdateInterval() {
  const sub = matchedSubscription.value
  if (!sub) return
  try {
    await updateSubMut.mutateAsync({
      id: sub.subscription_id,
      body: { refresh_interval_sec: subRefreshIntervalEdit.value },
    })
    message.success(`刷新间隔已改为 ${subRefreshIntervalEdit.value}s`)
  } catch (e: any) {
    message.error(e?.message || '更新失败')
  }
}

async function handleDeleteSubscription() {
  const sub = matchedSubscription.value
  if (!sub) return
  try {
    await deleteSubMut.mutateAsync(sub.subscription_id)
    message.success('订阅已删除（历史 run 保留）')
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function handleRerun() {
  if (!run.value) return
  const r = run.value
  // 用原 config 复制 factor_items + 调整后的开关
  const body: Record<string, any> = {
    factor_items: r.factor_items.map((it: any) => ({
      factor_id: it.factor_id,
      params: it.params ?? null,
    })),
    method: r.method,
    pool_id: r.pool_id,
    n_groups: rerunNGroups.value,
    ic_lookback_days: r.ic_lookback_days,
    use_realtime: rerunUseRealtime.value,
    filter_price_limit: rerunFilterPriceLimit.value,
    top_n: rerunTopN.value > 0 ? rerunTopN.value : null,
  }
  try {
    const res = await createSignalMut.mutateAsync(body)
    message.success('已重新触发，跳转到新 run')
    router.push(`/signals/${res.run_id}`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '触发失败')
  }
}

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
      <!-- 实时模式横幅：use_realtime=1 时给出醒目提示（红底+图标） -->
      <n-alert
        v-if="run && run.use_realtime"
        type="error"
        :show-icon="true"
        closable
        style="margin-bottom: 16px; border-left-width: 6px"
      >
        <template #header>
          <span style="font-weight: 700; font-size: 15px">
            ⚠️ 实盘监控已开启 — 该信号基于实时 spot 快照计算
          </span>
        </template>
        <span style="font-size: 13px">
          盘中场景：因子值取自当下成交价快照，越靠近收盘越稳定；
          盘后或 spot 陈旧（&gt;10min）时 service 自动降级到昨日 close。
        </span>
      </n-alert>

      <!-- 实盘监控订阅卡片：决定 worker 是否周期性重算这个信号 -->
      <n-card
        v-if="run"
        title="📡 实盘监控订阅"
        size="small"
        style="margin-bottom: 16px"
      >
        <!-- 情况 1：尚未订阅 -->
        <div v-if="!matchedSubscription">
          <n-space align="center" :size="16">
            <span style="font-size: 13px; color: #666">
              开启后 worker 会按设定间隔周期性重算本信号，每次产出新的 run（保留历史）。
            </span>
          </n-space>
          <n-space :size="12" align="center" style="margin-top: 12px">
            <span style="font-size: 13px">刷新间隔：</span>
            <n-select
              v-model:value="subRefreshIntervalEdit"
              :options="refreshIntervalOptions"
              style="width: 140px"
              size="small"
            />
            <n-button
              type="primary"
              :loading="createSubMut.isPending.value"
              @click="handleEnableMonitoring"
            >
              开启实盘监控
            </n-button>
          </n-space>
        </div>

        <!-- 情况 2：已订阅 -->
        <div v-else>
          <n-descriptions :column="3" size="small" bordered>
            <n-descriptions-item label="订阅 ID">
              <code>{{ matchedSubscription.subscription_id.slice(0, 8) }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="状态">
              <n-tag
                v-if="matchedSubscription.is_active"
                size="small" type="success" bordered
              >
                🟢 活跃中
              </n-tag>
              <n-tag v-else size="small" type="warning" bordered>
                ⏸ 已暂停
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="刷新间隔">
              {{ matchedSubscription.refresh_interval_sec }} 秒
            </n-descriptions-item>
            <n-descriptions-item label="上次刷新">
              {{ matchedSubscription.last_refresh_at ?? '尚未刷新' }}
            </n-descriptions-item>
            <n-descriptions-item label="最新 run">
              <code v-if="matchedSubscription.last_run_id">
                {{ matchedSubscription.last_run_id.slice(0, 8) }}
              </code>
              <span v-else style="color: #999">—</span>
            </n-descriptions-item>
            <n-descriptions-item label="创建时间">
              {{ matchedSubscription.created_at }}
            </n-descriptions-item>
          </n-descriptions>

          <n-space :size="12" align="center" style="margin-top: 16px" wrap>
            <span style="font-size: 13px">
              <b>实盘监控开关：</b>
            </span>
            <n-switch
              :value="!!matchedSubscription.is_active"
              :loading="updateSubMut.isPending.value"
              @update:value="handleToggleActive"
            />

            <n-divider vertical />

            <span style="font-size: 13px">改间隔：</span>
            <n-select
              v-model:value="subRefreshIntervalEdit"
              :options="refreshIntervalOptions"
              style="width: 140px"
              size="small"
            />
            <n-button
              size="small"
              :loading="updateSubMut.isPending.value"
              :disabled="subRefreshIntervalEdit === matchedSubscription.refresh_interval_sec"
              @click="handleUpdateInterval"
            >
              应用
            </n-button>

            <n-divider vertical />

            <n-button
              size="small" type="error" quaternary
              :loading="deleteSubMut.isPending.value"
              @click="handleDeleteSubscription"
            >
              删除订阅
            </n-button>
          </n-space>

          <n-alert
            v-if="!matchedSubscription.is_active"
            type="warning" size="small" :show-icon="false"
            style="margin-top: 12px"
          >
            订阅已暂停，worker 不再刷新；切回 🟢 即恢复。
          </n-alert>
        </div>
      </n-card>

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
          <n-descriptions-item label="Top 范围">
            {{ run.top_n != null ? `Top ${run.top_n}` : '全部（qcut 顶组）' }}
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

      <!-- 快速重跑：复制原 config，仅暴露 4 个常调旋钮，触发新 run。 -->
      <n-card
        v-if="run && run.status !== 'pending' && run.status !== 'running'"
        title="🔁 快速重跑（用同样因子配置 + 调整下面 4 个开关）"
        size="small"
        style="margin-bottom: 16px"
      >
        <n-space :size="24" align="center" wrap>
          <div>
            <div style="font-size: 12px; color: #999; margin-bottom: 4px">使用实时数据</div>
            <n-switch v-model:value="rerunUseRealtime" />
          </div>
          <div>
            <div style="font-size: 12px; color: #999; margin-bottom: 4px">涨跌停过滤</div>
            <n-switch v-model:value="rerunFilterPriceLimit" />
          </div>
          <div style="min-width: 220px">
            <div style="font-size: 12px; color: #999; margin-bottom: 4px">Top 范围</div>
            <n-select
              v-model:value="rerunTopN"
              :options="topNOptions"
              style="width: 220px"
              size="small"
            />
          </div>
          <div style="min-width: 100px">
            <div style="font-size: 12px; color: #999; margin-bottom: 4px">分组数</div>
            <n-select
              v-model:value="rerunNGroups"
              :options="[2,3,5,10,20].map(v => ({label: String(v), value: v}))"
              style="width: 100px"
              size="small"
            />
          </div>
          <n-button
            type="primary"
            :loading="createSignalMut.isPending.value"
            @click="handleRerun"
          >
            重新触发
          </n-button>
        </n-space>
        <div style="margin-top: 8px; color: #999; font-size: 12px">
          会创建一条新的 signal run（保留历史，便于审计）；其它配置（因子清单 / 方法 / 池）沿用本次。
        </div>
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
