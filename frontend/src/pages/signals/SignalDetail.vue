<script setup lang="ts">
/**
 * 实盘信号详情页。
 *
 * 布局：
 * - 顶部：实时模式横幅（仅 use_realtime=1 时显示，红色醒目）
 * - 基础信息（method / 股票池 / as_of_time / use_realtime / top_n / spot_meta）
 * - 实盘监控订阅卡片：开关 / 立即刷新 / 改间隔 / 删除 / **实时刷新进度**
 * - top 组 / bottom 组排名表（含因子综合值 + 子因子分解）
 *
 * pending/running 时由 useSignal 自动 1.5s 轮询；success/failed 后停。
 * 已订阅时本页用一个 1s tick 的 nowMs 驱动倒计时 / 相对时间显示。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NPageHeader, NCard, NDescriptions, NDescriptionsItem,
  NProgress, NSpin, NAlert, NDataTable, NGrid, NGridItem, NTag, NSpace,
  NButton, NSelect, NSwitch, NIcon, NDivider, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useSignal } from '@/api/signals'
import type { SignalHolding } from '@/api/signals'
import {
  useSubscriptions, useCreateSubscription, useUpdateSubscription,
  useDeleteSubscription, useRefreshSubscriptionNow,
} from '@/api/signal_subscriptions'
import { usePoolNameMap } from '@/api/pools'
import StatusBadge from '@/components/layout/StatusBadge.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const runId = computed(() => route.params.runId as string)
const { data: run, isLoading } = useSignal(runId)
const { lookup: lookupPoolName } = usePoolNameMap()

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
const refreshSubMut = useRefreshSubscriptionNow()

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

/** 立即刷新订阅：原地刷新**当前正在看的** run_id（URL 不变），异步跑。
 *
 * 把 runId 作为 targetRunId 传过去：后端 prepare_subscription_refresh 会
 * UPDATE 这条 run + 把 sub.last_run_id 改指它。这样：
 * - 当前页 useSignal 的 1.5s 轮询会自动看到 pending → success
 * - 不会创建新 run（修复"立即刷新还在新建信号"）
 */
async function handleRefreshSubNow() {
  const sub = matchedSubscription.value
  if (!sub) return
  try {
    const res = await refreshSubMut.mutateAsync({
      id: sub.subscription_id,
      targetRunId: runId.value,
    })
    message.success(`已触发刷新（run ${res.run_id.slice(0, 8)}）；状态会自动更新`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '触发刷新失败')
  }
}

const isRunning = computed(
  () => run.value?.status === 'pending' || run.value?.status === 'running',
)

// ---- 实时刷新进度可视化（仅订阅 active 时渲染） ----
// 1s tick 的当前时间戳，驱动倒计时 / 相对时间 reactive 更新。
const nowMs = ref(Date.now())
let nowTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => { nowTimer = setInterval(() => { nowMs.value = Date.now() }, 1000) })
onBeforeUnmount(() => { if (nowTimer) clearInterval(nowTimer) })

/** 把毫秒数差转人话："5 秒前" / "1 分 23 秒前" / "刚刚"。 */
function fmtRelative(deltaMs: number): string {
  if (deltaMs < 1000) return '刚刚'
  const sec = Math.floor(deltaMs / 1000)
  if (sec < 60) return `${sec} 秒前`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  if (m < 60) return s ? `${m} 分 ${s} 秒前` : `${m} 分钟前`
  const h = Math.floor(m / 60)
  return `${h} 小时 ${m % 60} 分前`
}

/** 把秒数转 mm:ss / m分s秒 形式（倒计时用）。 */
function fmtCountdown(sec: number): string {
  if (sec <= 0) return '即将刷新…'
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`
}

const lastRefreshMs = computed<number | null>(() => {
  const ts = matchedSubscription.value?.last_refresh_at
  if (!ts) return null
  // last_refresh_at 是 ``YYYY-MM-DD HH:MM:SS`` 北京时区字符串，也可能是 ISO；
  // Date.parse 对前者会按本地时区解析，对运行在中国时区的浏览器是正确的。
  const t = Date.parse(String(ts).replace(' ', 'T'))
  return isNaN(t) ? null : t
})

const lastRefreshRelative = computed<string>(() => {
  if (lastRefreshMs.value == null) return '尚未刷新'
  return fmtRelative(nowMs.value - lastRefreshMs.value)
})

/** 距离下次刷新的剩余秒数；订阅未刷新过返 0（即"立刻刷新中"）。 */
const secondsToNextRefresh = computed<number>(() => {
  const sub = matchedSubscription.value
  if (!sub || lastRefreshMs.value == null) return 0
  const intervalMs = sub.refresh_interval_sec * 1000
  const elapsed = nowMs.value - lastRefreshMs.value
  return Math.max(0, Math.ceil((intervalMs - elapsed) / 1000))
})

/** 刷新进度百分比（0-100），用于 progress bar 视觉反馈。 */
const refreshProgressPct = computed<number>(() => {
  const sub = matchedSubscription.value
  if (!sub || lastRefreshMs.value == null) return 0
  const intervalMs = sub.refresh_interval_sec * 1000
  const elapsed = nowMs.value - lastRefreshMs.value
  return Math.max(0, Math.min(100, (elapsed / intervalMs) * 100))
})
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
  if (m === 'ml_lgb') return 'LightGBM 合成 (ml_lgb)'
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
              <span
                v-if="lastRefreshMs"
                style="color: #999; font-size: 11px; margin-left: 6px"
              >
                ({{ lastRefreshRelative }})
              </span>
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

            <n-button
              size="small"
              type="primary"
              :disabled="!matchedSubscription.is_active"
              :loading="refreshSubMut.isPending.value"
              @click="handleRefreshSubNow"
            >
              ⚡ 立即刷新
            </n-button>

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

          <div style="margin-top: 8px; color: #999; font-size: 12px">
            ⚡ <b>立即刷新</b> 不等下一个间隔，立刻按当前订阅配置重算（复用当前
            run_id，URL 不变、历史不膨胀）。
          </div>

          <!-- 实时刷新节奏：让用户直观看到"系统在定时抓数据 + 重算因子" -->
          <div
            v-if="matchedSubscription.is_active"
            style="margin-top: 14px; padding: 12px 14px;
                   background: #f0fdf4; border-left: 3px solid #18a058;
                   border-radius: 4px"
          >
            <!-- 当前 run 正在跑：显眼提示 + 后端 progress 条 -->
            <div
              v-if="isRunning"
              style="display: flex; align-items: center; gap: 8px"
            >
              <n-spin size="small" />
              <span style="font-weight: 600; color: #18a058">
                🔄 正在抓取行情快照 + 重算因子…
              </span>
              <span
                v-if="run?.progress != null"
                style="color: #888; font-size: 12px; margin-left: auto"
              >
                进度 {{ run.progress }}%
              </span>
            </div>

            <!-- 当前 run 跑完，距下一次刷新的倒计时进度条 -->
            <div v-else>
              <div
                style="display: flex; align-items: center; gap: 8px;
                       margin-bottom: 8px"
              >
                <span class="pulse-dot" />
                <span style="color: #18a058; font-weight: 600; font-size: 13px">
                  系统每 {{ matchedSubscription.refresh_interval_sec }} 秒自动抓取行情 + 重算因子
                </span>
                <span
                  style="color: #888; font-size: 12px; margin-left: auto"
                >
                  上次：{{ lastRefreshRelative }}
                </span>
              </div>
              <n-progress
                type="line"
                :percentage="refreshProgressPct"
                :show-indicator="false"
                :height="6"
                color="#18a058"
              />
              <div
                style="text-align: right; color: #888; font-size: 11px;
                       margin-top: 4px"
              >
                下次刷新：{{ fmtCountdown(secondsToNextRefresh) }}
              </div>
            </div>
          </div>

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

<style scoped>
/* 订阅活跃时的"心跳"小绿点：传达"系统在工作"的视觉反馈，对照倒计时进度条
   形成动+静两条信息（节奏 + 进度）。 */
.pulse-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background-color: #18a058;
  box-shadow: 0 0 0 0 rgba(24, 160, 88, 0.6);
  animation: pulse-grow 1.6s ease-out infinite;
}

@keyframes pulse-grow {
  0% { box-shadow: 0 0 0 0 rgba(24, 160, 88, 0.55); }
  70% { box-shadow: 0 0 0 8px rgba(24, 160, 88, 0); }
  100% { box-shadow: 0 0 0 0 rgba(24, 160, 88, 0); }
}
</style>
