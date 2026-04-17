// 评估 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

// 后端 GET /api/evals/{run_id} 的返回结构：
// - run 层字段来自 fr_factor_eval_runs（params 以 JSON 字符串存在 params_json 列）
// - metrics 层字段来自 fr_factor_eval_metrics（ic_mean / turnover_mean 等结构化指标）
// - metrics.payload 由 payload_json 反序列化而来（IC 曲线 / 分组净值 / 直方图 等图表数据）
export interface EvalMetrics {
  ic_mean?: number | null
  ic_std?: number | null
  ic_ir?: number | null
  ic_win_rate?: number | null
  ic_t_stat?: number | null
  rank_ic_mean?: number | null
  turnover_mean?: number | null
  long_short_sharpe?: number | null
  long_short_annret?: number | null
  payload?: Record<string, any> | null
}

export interface EvalRun {
  run_id: string
  factor_id: string
  status: 'pending' | 'running' | 'success' | 'failed'
  params_json?: string
  pool_id: number
  start_date: string
  end_date: string
  created_at: string
  finished_at?: string
  error_message?: string
  metrics?: EvalMetrics | null
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

/** 获取评估列表。params 支持响应式（Ref/Computed/Getter），变化会自动 refetch。 */
export function useEvals(params?: MaybeRefOrGetter<Record<string, any> | undefined>) {
  return useQuery<EvalRun[]>({
    // queryKey 里直接放 ref/getter，Vue Query 会 track 响应式变化
    queryKey: ['evals', params],
    queryFn: () => client.get('/evals', { params: toValue(params) ?? {} }).then(r => r.data),
  })
}

/** 删除评估记录 */
export function useDeleteEval() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => client.delete(`/evals/${runId}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['evals'] }),
  })
}
