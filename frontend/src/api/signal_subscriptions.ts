// 实盘监控订阅 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'
import type { CompositionFactorItem } from './compositions'

export interface SignalSubscription {
  subscription_id: string
  factor_items: CompositionFactorItem[]
  method: 'equal' | 'ic_weighted' | 'orthogonal_equal' | 'single'
  pool_id: number
  n_groups: number
  ic_lookback_days: number
  filter_price_limit: 0 | 1
  top_n: number | null
  refresh_interval_sec: number
  is_active: 0 | 1
  last_refresh_at: string | null
  last_run_id: string | null
  created_at: string
  updated_at: string
}

export interface CreateSubscriptionBody {
  factor_items: { factor_id: string; params?: Record<string, any> | null }[]
  method: string
  pool_id: number
  n_groups?: number
  ic_lookback_days?: number
  filter_price_limit?: boolean
  top_n?: number | null
  refresh_interval_sec?: number
}

/** 创建订阅；可附带 from_run_id 让后端做存在性校验。 */
export function useCreateSubscription() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      body, fromRunId,
    }: { body: CreateSubscriptionBody; fromRunId?: string }) => {
      const url = fromRunId
        ? `/signal-subscriptions?from_run_id=${encodeURIComponent(fromRunId)}`
        : '/signal-subscriptions'
      return client.post(url, body).then(r => r.data)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['signal-subscriptions'] }),
  })
}

export function useSubscriptions(
  params?: MaybeRefOrGetter<{ active?: 0 | 1 } | undefined>,
) {
  return useQuery<SignalSubscription[]>({
    queryKey: ['signal-subscriptions', params],
    queryFn: () =>
      client.get('/signal-subscriptions', { params: toValue(params) ?? {} }).then(r => r.data),
    refetchInterval: 5000,  // 5s 轮询：worker 刷新订阅时 last_refresh_at 会变
  })
}

export function useSubscription(id: Ref<string>) {
  return useQuery<SignalSubscription>({
    queryKey: ['signal-subscription', id],
    queryFn: () => client.get(`/signal-subscriptions/${id.value}`).then(r => r.data),
    enabled: () => !!id.value,
    refetchInterval: 5000,
  })
}

/** 切换 is_active / 改 refresh_interval_sec。 */
export function useUpdateSubscription() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id, body,
    }: { id: string; body: { is_active?: boolean; refresh_interval_sec?: number } }) =>
      client.put(`/signal-subscriptions/${id}`, body).then(r => r.data),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ['signal-subscriptions'] })
      qc.invalidateQueries({ queryKey: ['signal-subscription', vars.id] })
    },
  })
}

export function useDeleteSubscription() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      client.delete(`/signal-subscriptions/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['signal-subscriptions'] }),
  })
}

/** 立即刷新一条订阅：复用 run_id 重置为 pending 并 enqueue。
 *
 * 与 useCreateSignal（快速重跑那个按钮）的关键区别：本 hook 不会创建
 * 新 run_id，详情页 URL 不变；适合"我现在就想看新结果"这个场景。
 *
 * targetRunId：可选；传入时强制 UPDATE 这个 run（"原地刷新当前页"）。
 * SignalDetail 的按钮始终传当前 runId，确保"在哪个 run 详情页点的就刷哪个"，
 * 即便这个 run 不是订阅 last_run_id 上次产出的。
 */
export function useRefreshSubscriptionNow() {
  const qc = useQueryClient()
  return useMutation<
    { run_id: string; subscription_id: string; status: string },
    Error,
    { id: string; targetRunId?: string }
  >({
    mutationFn: ({ id, targetRunId }) => {
      const url = targetRunId
        ? `/signal-subscriptions/${id}/refresh-now?target_run_id=${encodeURIComponent(targetRunId)}`
        : `/signal-subscriptions/${id}/refresh-now`
      return client.post(url).then(r => r.data)
    },
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ['signal-subscriptions'] })
      qc.invalidateQueries({ queryKey: ['signal-subscription', vars.id] })
      // run 列表 / 详情都会被重置为 pending → 让相关查询重抓
      qc.invalidateQueries({ queryKey: ['signals'] })
      qc.invalidateQueries({ queryKey: ['signal'] })
    },
  })
}
