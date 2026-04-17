// 因子 API 层
import { useQuery } from '@tanstack/vue-query'
import { client } from './client'
import type { Ref } from 'vue'

export interface Factor {
  factor_id: string
  display_name: string
  category: string
  description: string
  params_schema: Record<string, any>
  default_params: Record<string, any>
  supported_freqs: string[]
  version?: number
}

/** 获取全部因子列表 */
export function useFactors() {
  return useQuery<Factor[]>({
    queryKey: ['factors'],
    queryFn: () => client.get('/factors').then(r => r.data),
  })
}

/** 获取单个因子详情 */
export function useFactor(factorId: Ref<string>) {
  return useQuery<Factor>({
    queryKey: ['factor', factorId],
    queryFn: () => client.get(`/factors/${factorId.value}`).then(r => r.data),
    enabled: () => !!factorId.value,
  })
}
