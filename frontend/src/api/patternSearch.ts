// 图形相似度检索 API 层。mutation 风格（手动触发）。
// 注意：client 拦截器已把 {code,data} 解包成 resp.data，故这里直接 return data。
import { useMutation } from '@tanstack/vue-query'
import { client } from './client'

export interface PatternMatch {
  label: string
  score: number
  scale: number
  start_date: string | null
  end_date: string | null
  curve: number[]
}
export interface PatternResult {
  query_curve: number[]
  matches: PatternMatch[]
}
export interface ByStockReq {
  symbol: string
  window_start?: string
  window_end?: string
  scales?: number[]
  top_k?: number
}

export function useByStockSearch() {
  return useMutation({
    mutationFn: async (req: ByStockReq): Promise<PatternResult> => {
      const { data } = await client.post('/pattern_search/by_stock', req)
      return data as PatternResult
    },
  })
}

export interface ByImageReq {
  image: string
  pool_id: number
  hint?: string
  scales?: number[]
  top_k?: number
}
export function useByImageSearch() {
  return useMutation({
    mutationFn: async (req: ByImageReq): Promise<PatternResult> => {
      const { data } = await client.post('/pattern_search/by_image', req)
      return data as PatternResult
    },
  })
}
