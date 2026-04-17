// 回测 API 层
import { useQuery, useMutation } from '@tanstack/vue-query'
import { client } from './client'
import type { Ref } from 'vue'

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
