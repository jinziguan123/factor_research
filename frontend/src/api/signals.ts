// 实盘信号 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'
import type {
  CompositionFactorItem,
  PerFactorIcEntry,
} from './compositions'

/** payload.top / bottom 中每只票的展示项。 */
export interface SignalHolding {
  symbol: string
  factor_value_composite: number | null
  /** 多因子时填，每个子因子的 z-score；单因子留空 dict。 */
  factor_value_breakdown: Record<string, number | null>
  last_price: number | null
  pct_chg: number | null
}

export interface SignalSpotMeta {
  snapshot_at: string | null
  n_symbols_total: number
  n_spot_rows: number
  use_realtime: boolean
}

export interface SignalPayload {
  top: SignalHolding[]
  bottom: SignalHolding[]
  weights?: Record<string, number> | null
  per_factor_ic?: Record<string, PerFactorIcEntry> | null
  factor_items?: CompositionFactorItem[]
  spot_meta?: SignalSpotMeta
}

export interface SignalRun {
  run_id: string
  factor_items: CompositionFactorItem[]
  method: 'equal' | 'ic_weighted' | 'orthogonal_equal' | 'single'
  pool_id: number
  n_groups: number
  ic_lookback_days: number
  as_of_time: string
  as_of_date: string
  use_realtime: 0 | 1
  filter_price_limit: 0 | 1
  /** 可选 top K 限制；null 表示 qcut 顶组全部。 */
  top_n: number | null
  status: 'pending' | 'running' | 'success' | 'failed' | 'aborting' | 'aborted'
  progress: number
  error_message?: string
  n_holdings_top?: number | null
  n_holdings_bot?: number | null
  payload?: SignalPayload | null
  created_at: string
  started_at?: string
  finished_at?: string
  /** 订阅关联：非 null 表示该 run 由某订阅创建 / 绑定。 */
  subscription_id?: string | null
  /** 订阅当前 is_active；0=已暂停，1=活跃中，null=该 run 不属于任何订阅。 */
  subscription_active?: 0 | 1 | null
}

/** 创建信号任务 */
export function useCreateSignal() {
  return useMutation({
    mutationFn: (body: Record<string, any>) =>
      client.post('/signals', body).then((r) => r.data),
  })
}

/** 单条信号详情（含 payload）。pending/running 时 1.5s 轮询。 */
export function useSignal(runId: Ref<string>) {
  return useQuery<SignalRun>({
    queryKey: ['signal', runId],
    queryFn: () => client.get(`/signals/${runId.value}`).then((r) => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
  })
}

/** 信号列表（不含 payload；支持 pool_id / status / as_of_date 过滤）。 */
export function useSignals(
  params?: MaybeRefOrGetter<Record<string, any> | undefined>,
) {
  return useQuery<SignalRun[]>({
    queryKey: ['signals', params],
    queryFn: () =>
      client.get('/signals', { params: toValue(params) ?? {} }).then((r) => r.data),
  })
}

/** 删除单条 */
export function useDeleteSignal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.delete(`/signals/${runId}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['signals'] }),
  })
}

/** 批量删除（沿用后端的 BatchDeleteIn 协议） */
export function useBatchDeleteSignals() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runIds: string[]) =>
      client.post('/signals/batch-delete', { run_ids: runIds }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['signals'] }),
  })
}

/** 中断任务（协作式）。 */
export function useAbortSignal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.post(`/signals/${runId}/abort`).then((r) => r.data),
    onSuccess: (_res, runId) => {
      qc.invalidateQueries({ queryKey: ['signals'] })
      qc.invalidateQueries({ queryKey: ['signal', runId] })
    },
  })
}
