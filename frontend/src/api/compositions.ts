// 多因子合成 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

/** 请求里 / 入库后 resolved 后的单个因子项。 */
export interface CompositionFactorItem {
  factor_id: string
  params?: Record<string, any> | null
  /** 仅 resolved（任务完成后）才有以下两项：方便前端展示实际跑的版本。 */
  factor_version?: number
  params_hash?: string
}

/** 相关性矩阵：factor_ids 与 values 的行列顺序一致。 */
export interface CorrMatrix {
  factor_ids: string[]
  values: (number | null)[][]
}

/** 每个原始因子的 IC 汇总（供"合成 vs. 单因子"对比）。 */
export interface PerFactorIcEntry {
  ic_mean: number | null
  ic_ir: number | null
  ic_win_rate: number | null
}

export interface CompositionRun {
  run_id: string
  pool_id: number
  freq: string
  start_date: string
  end_date: string
  method: 'equal' | 'ic_weighted' | 'orthogonal_equal'
  factor_items: CompositionFactorItem[]
  n_groups: number
  forward_periods: number[] | string
  ic_weight_period: number
  status: 'pending' | 'running' | 'success' | 'failed'
  progress: number
  error_message?: string
  // 结构化指标
  ic_mean: number | null
  ic_std: number | null
  ic_ir: number | null
  ic_win_rate: number | null
  ic_t_stat: number | null
  rank_ic_mean: number | null
  rank_ic_std: number | null
  rank_ic_ir: number | null
  turnover_mean: number | null
  long_short_sharpe: number | null
  long_short_annret: number | null
  // 解析后的 JSON 字段（后端已 parse）
  corr_matrix?: CorrMatrix | null
  per_factor_ic?: Record<string, PerFactorIcEntry> | null
  weights?: Record<string, number> | null
  payload?: any
  created_at: string
  started_at?: string
  finished_at?: string
}

export function useCreateComposition() {
  return useMutation({
    mutationFn: (body: Record<string, any>) =>
      client.post('/compositions', body).then((r) => r.data),
  })
}

/** 详情：未完成时轮询；状态机与其它 run 保持一致。 */
export function useComposition(runId: Ref<string>) {
  return useQuery<CompositionRun>({
    queryKey: ['composition', runId],
    queryFn: () =>
      client.get(`/compositions/${runId.value}`).then((r) => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' ? 2000 : false
    },
  })
}

export function useCompositionRuns(
  params?: MaybeRefOrGetter<Record<string, any> | undefined>,
) {
  return useQuery<CompositionRun[]>({
    queryKey: ['composition-list', params],
    queryFn: () =>
      client.get('/compositions', { params: toValue(params) ?? {} }).then((r) => r.data),
  })
}

export function useDeleteComposition() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.delete(`/compositions/${runId}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['composition-list'] }),
  })
}
