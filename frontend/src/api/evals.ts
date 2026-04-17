// 评估 API 层
import { useQuery, useMutation } from '@tanstack/vue-query'
import { client } from './client'
import type { Ref } from 'vue'

export interface EvalRun {
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

/** 创建评估任务 */
export function useCreateEval() {
  return useMutation({
    mutationFn: (body: Record<string, any>) =>
      client.post('/evals', body).then(r => r.data),
  })
}

/** 获取单个评估详情（带轮询） */
export function useEval(runId: Ref<string>) {
  return useQuery<EvalRun>({
    queryKey: ['eval', runId],
    queryFn: () => client.get(`/evals/${runId.value}`).then(r => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
  })
}

/** 获取评估列表 */
export function useEvals(params?: Record<string, any>) {
  return useQuery<EvalRun[]>({
    queryKey: ['evals', params],
    queryFn: () => client.get('/evals', { params }).then(r => r.data),
  })
}
