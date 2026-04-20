// 回测 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

export interface EquitySeries {
  dates: string[]
  values: (number | null)[]
  total: number
  sampled: boolean
}

export interface TradesPage {
  total: number
  page: number
  size: number
  columns: string[]
  rows: Record<string, any>[]
}

export interface BacktestRun {
  run_id: string
  factor_id: string
  /** 新增 aborting / aborted：见 StatusBadge 和 abort_check.py。 */
  status: 'pending' | 'running' | 'success' | 'failed' | 'aborting' | 'aborted'
  params: Record<string, any>
  pool_id: number
  start_date: string
  end_date: string
  created_at: string
  finished_at?: string
  error?: string
  payload?: Record<string, any>
  metrics?: Record<string, any>
}

/** 创建回测任务 */
export function useCreateBacktest() {
  return useMutation({
    mutationFn: (body: Record<string, any>) =>
      client.post('/backtests', body).then(r => r.data),
  })
}

/** 获取单个回测详情（带轮询） */
export function useBacktest(runId: Ref<string>) {
  return useQuery<BacktestRun>({
    queryKey: ['backtest', runId],
    queryFn: () => client.get(`/backtests/${runId.value}`).then(r => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
  })
}

/** 获取回测列表。params 支持响应式（Ref/Computed/Getter），变化会自动 refetch。 */
export function useBacktests(params?: MaybeRefOrGetter<Record<string, any> | undefined>) {
  return useQuery<BacktestRun[]>({
    queryKey: ['backtests', params],
    queryFn: () => client.get('/backtests', { params: toValue(params) ?? {} }).then(r => r.data),
  })
}

/** 删除回测记录 */
export function useDeleteBacktest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => client.delete(`/backtests/${runId}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }),
  })
}

/** 中断一个运行中的回测任务。协作式，语义与 useAbortEval 一致。 */
export function useAbortBacktest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.post(`/backtests/${runId}/abort`).then(r => r.data),
    onSuccess: (_res, runId) => {
      qc.invalidateQueries({ queryKey: ['backtests'] })
      qc.invalidateQueries({ queryKey: ['backtest', runId] })
    },
  })
}

/** 拉净值曲线（已在后端降采样，max_points 默认走后端 2000）。
 *  只有在回测 status=success 且 run_id 有值时才发请求，避免跑着的任务被无意义轮询。
 */
export function useEquitySeries(
  runId: Ref<string>,
  enabled: MaybeRefOrGetter<boolean>,
) {
  return useQuery<EquitySeries>({
    queryKey: ['backtest-equity', runId],
    queryFn: () =>
      client.get(`/backtests/${runId.value}/equity_series`).then(r => r.data),
    enabled: () => !!runId.value && !!toValue(enabled),
    staleTime: 60_000, // 完成态产物不会变，一分钟内不重复请求
  })
}

export interface TradesFilter {
  /** 股票代码子串（大小写不敏感）。空串 / undefined 表示不过滤。 */
  symbol?: string
  /** 按 Entry Timestamp 起始日，YYYY-MM-DD。 */
  startDate?: string | null
  /** 按 Entry Timestamp 结束日，YYYY-MM-DD（闭区间）。 */
  endDate?: string | null
}

/**
 * 拉分页交易列表。page / size / filter 均用 getter 以便 UI 切换时自动 refetch。
 *
 * filter 中的空字段会被剥掉，避免 axios 把 `symbol=""` 编进 URL——后端虽然会把
 * 空串当作"不过滤"，但无意义 refetch 也要省。
 */
export function useTradesPage(
  runId: Ref<string>,
  page: MaybeRefOrGetter<number>,
  size: MaybeRefOrGetter<number>,
  enabled: MaybeRefOrGetter<boolean>,
  filter?: MaybeRefOrGetter<TradesFilter | undefined>,
) {
  return useQuery<TradesPage>({
    queryKey: ['backtest-trades', runId, page, size, filter],
    queryFn: () => {
      const f = toValue(filter) ?? {}
      const params: Record<string, string | number> = {
        page: toValue(page),
        size: toValue(size),
      }
      if (f.symbol && f.symbol.trim()) params.symbol = f.symbol.trim()
      if (f.startDate) params.start_date = f.startDate
      if (f.endDate) params.end_date = f.endDate
      return client
        .get(`/backtests/${runId.value}/trades_page`, { params })
        .then(r => r.data)
    },
    enabled: () => !!runId.value && !!toValue(enabled),
    staleTime: 60_000,
  })
}
