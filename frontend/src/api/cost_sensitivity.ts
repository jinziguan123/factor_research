// 成本敏感性分析 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

/** 单个 cost_bps 点的指标（与后端 cost_sensitivity_service._compute_point 对齐）。 */
export interface SensitivityPoint {
  cost_bps: number
  total_return: number | null
  annual_return: number | null
  sharpe_ratio: number | null
  max_drawdown: number | null
  win_rate: number | null
  trade_count: number
  turnover_total: number | null
  stats: Record<string, any>
}

export interface CostSensitivityRun {
  run_id: string
  factor_id: string
  factor_version: number
  params_hash: string
  params_json?: string
  pool_id: number
  freq: string
  start_date: string
  end_date: string
  n_groups: number
  rebalance_period: number
  position: string
  init_cash: number
  cost_bps_list: number[] | string
  status: 'pending' | 'running' | 'success' | 'failed'
  progress: number
  error_message?: string
  created_at: string
  started_at?: string
  finished_at?: string
  points?: SensitivityPoint[] | null
}

/** 创建成本敏感性分析任务 */
export function useCreateCostSensitivity() {
  return useMutation({
    mutationFn: (body: Record<string, any>) =>
      client.post('/cost-sensitivity', body).then((r) => r.data),
  })
}

/** 获取单次敏感性分析详情（未完成时轮询） */
export function useCostSensitivity(runId: Ref<string>) {
  return useQuery<CostSensitivityRun>({
    queryKey: ['cost-sensitivity', runId],
    queryFn: () =>
      client.get(`/cost-sensitivity/${runId.value}`).then((r) => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' ? 2000 : false
    },
  })
}

/** 列表 */
export function useCostSensitivityRuns(
  params?: MaybeRefOrGetter<Record<string, any> | undefined>,
) {
  return useQuery<CostSensitivityRun[]>({
    queryKey: ['cost-sensitivity-list', params],
    queryFn: () =>
      client
        .get('/cost-sensitivity', { params: toValue(params) ?? {} })
        .then((r) => r.data),
  })
}

/** 删除 */
export function useDeleteCostSensitivity() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.delete(`/cost-sensitivity/${runId}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cost-sensitivity-list'] }),
  })
}
