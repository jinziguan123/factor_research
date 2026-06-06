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
  sub_scores?: number[]   // 多图检索时对每张图的分项相似度
}
export interface PatternResult {
  query_curve: number[]
  query_curves?: number[][]  // 多图检索时每张图的查询曲线
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
  pool_id: number
  images?: string[]   // 多张截图（综合检索）
  image?: string      // 单张（兼容）
  hint?: string
  scales?: number[]
  top_k?: number
  agg?: 'min' | 'mean'
}
export function useByImageSearch() {
  return useMutation({
    mutationFn: async (req: ByImageReq): Promise<PatternResult> => {
      const { data } = await client.post('/pattern_search/by_image', req)
      return data as PatternResult
    },
  })
}
