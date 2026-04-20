// 参数敏感性扫描 API 层（异步 + 持久化，结构同 cost_sensitivity）
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

/** 单个参数取值的指标（与后端 param_sensitivity_service._compute_point 对齐）。 */
export interface ParamSensitivityPoint {
  value: number
  ic_mean: number | null
  rank_ic_mean: number | null
  ic_ir: number | null
  rank_ic_ir: number | null
  long_short_sharpe: number | null
  long_short_annret: number | null
  turnover_mean: number | null
  n_ic_days: number | null
  error: string | null
}

export interface ParamSensitivitySchemaEntry {
  type?: string
  min?: number
  max?: number
  default?: number
  desc?: string
}

export interface ParamSensitivityRun {
  run_id: string
  factor_id: string
  factor_version: number
  param_name: string
  values: number[] | null
  base_params?: Record<string, any> | null
  pool_id: number
  freq: string
  start_date: string
  end_date: string
  n_groups: number
  forward_periods: number[] | null
  status: 'pending' | 'running' | 'success' | 'failed' | 'aborting' | 'aborted'
  progress: number
  error_message?: string
  created_at: string
  started_at?: string
  finished_at?: string
  // 详情页专属（list 不返回）
  points?: ParamSensitivityPoint[] | null
  default_value?: number | null
  schema_entry?: ParamSensitivitySchemaEntry | null
}

export interface ParamSensitivityInput {
  factor_id: string
  param_name: string
  values: number[]
  pool_id: number
  start_date: string
  end_date: string
  freq?: string
  n_groups?: number
  forward_periods?: number[]
  base_params?: Record<string, any> | null
}

/** 创建参数敏感性扫描任务 */
export function useCreateParamSensitivity() {
  return useMutation<{ run_id: string; status: string }, Error, ParamSensitivityInput>({
    mutationFn: (body) =>
      client.post('/param-sensitivity', body).then((r) => r.data),
  })
}

/** 获取单次扫描详情（未完成时轮询） */
export function useParamSensitivity(runId: Ref<string>) {
  return useQuery<ParamSensitivityRun>({
    queryKey: ['param-sensitivity', runId],
    queryFn: () =>
      client.get(`/param-sensitivity/${runId.value}`).then((r) => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' ? 2000 : false
    },
  })
}

/** 列表 */
export function useParamSensitivityRuns(
  params?: MaybeRefOrGetter<Record<string, any> | undefined>,
) {
  return useQuery<ParamSensitivityRun[]>({
    queryKey: ['param-sensitivity-list', params],
    queryFn: () =>
      client
        .get('/param-sensitivity', { params: toValue(params) ?? {} })
        .then((r) => r.data),
  })
}

/** 删除 */
export function useDeleteParamSensitivity() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.delete(`/param-sensitivity/${runId}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['param-sensitivity-list'] }),
  })
}

/** 中断运行中的参数扫描。每个扫描点前会 check_abort，最坏等一个点（~30-60s）。 */
export function useAbortParamSensitivity() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.post(`/param-sensitivity/${runId}/abort`).then((r) => r.data),
    onSuccess: (_res, runId) => {
      qc.invalidateQueries({ queryKey: ['param-sensitivity-list'] })
      qc.invalidateQueries({ queryKey: ['param-sensitivity', runId] })
    },
  })
}
