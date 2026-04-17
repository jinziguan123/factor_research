// 回测 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

export interface BacktestRun {
  run_id: string
  factor_id: string
  status: 'pending' | 'running' | 'success' | 'failed'
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
