/**
 * 参数敏感性 API（MVP）：单一同步接口 /param-sensitivity/preview。
 * 同步跑完返回，无列表 / 无状态查询。因扫描可能 1-3 分钟，timeout 显式拉到 10 分钟。
 */
import { useMutation } from '@tanstack/vue-query'
import { client } from './client'

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

export interface ParamSensitivityResult {
  factor_id: string
  param_name: string
  default_value: number | null
  base_params: Record<string, any>
  schema_entry: {
    type?: string
    min?: number
    max?: number
    default?: number
    desc?: string
  } | null
  pool_id: number
  start_date: string
  end_date: string
  forward_periods: number[]
  n_groups: number
  points: ParamSensitivityPoint[]
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

export function usePreviewParamSensitivity() {
  return useMutation<ParamSensitivityResult, Error, ParamSensitivityInput>({
    mutationFn: (body) =>
      client
        .post('/param-sensitivity/preview', body, { timeout: 600_000 })
        .then((r) => r.data),
  })
}
