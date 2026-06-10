// 图形相似度检索 API 层。
// 注意：client 拦截器已把 {code,data} 解包成 resp.data，故这里直接 return data。
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
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

// 相似K线选股：用「一段或多段」真实走势在股票池里找走势最像的其他股票（异步任务）。
export interface WindowSpec {
  symbol: string
  start?: string
  end?: string
}
export interface ByWindowReq {
  pool_id: number
  windows: WindowSpec[]
  scales?: number[]
  top_k?: number
  agg?: 'min' | 'mean'
  min_score?: number
}
/** 创建「相似K线选股」任务，返回 run_id（与截图检索复用同一记录页）。 */
export function useCreateWindowSearch() {
  return useMutation({
    mutationFn: async (req: ByWindowReq): Promise<{ run_id: string; status: RunStatus }> => {
      const { data } = await client.post('/pattern_search/by_window', req)
      return data
    },
  })
}

// ---------------------------- 需求1 by_image：异步任务 ----------------------------

export interface ByImageReq {
  pool_id: number
  images?: string[]       // 多张截图 base64（综合检索）
  image?: string          // 单张（兼容）
  image_names?: string[]  // 上传文件名（仅展示）
  hint?: string
  scales?: number[]
  top_k?: number
  agg?: 'min' | 'mean'
  min_score?: number   // 综合相似度阈值 0~1，低于此分不返回
}

export type RunStatus = 'pending' | 'running' | 'success' | 'failed' | 'aborting' | 'aborted'

/** 列表行（不含曲线/结果大字段）。 */
export interface PatternRun {
  run_id: string
  kind?: 'by_image' | 'by_window'
  pool_id: number
  image_names?: string[]
  query_json?: WindowSpec[]   // by_window 的查询窗口
  num_images: number
  hint?: string | null
  top_k?: number
  agg?: string
  status: RunStatus
  progress: number
  error_message?: string | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

/** 详情：列表字段 + 识别曲线 + 检索结果。 */
export interface PatternRunDetail extends PatternRun {
  query_curves: number[][]
  matches: PatternMatch[]
}

/** 创建截图检索任务，返回 run_id。 */
export function useCreateImageSearch() {
  return useMutation({
    mutationFn: async (req: ByImageReq): Promise<{ run_id: string; status: RunStatus }> => {
      const { data } = await client.post('/pattern_search/by_image', req)
      return data
    },
  })
}

/** 任务列表（有活跃任务时由页面驱动轮询）。 */
export function usePatternRuns(params?: MaybeRefOrGetter<Record<string, any> | undefined>) {
  return useQuery<PatternRun[]>({
    queryKey: ['pattern_runs', params],
    queryFn: () =>
      client.get('/pattern_search/runs', { params: toValue(params) ?? {} }).then(r => r.data),
  })
}

/** 任务详情（pending/running 时每 1.5s 轮询）。 */
export function usePatternRun(runId: Ref<string>) {
  return useQuery<PatternRunDetail>({
    queryKey: ['pattern_run', runId],
    queryFn: () => client.get(`/pattern_search/runs/${runId.value}`).then(r => r.data),
    enabled: () => !!runId.value,
    refetchInterval: (q) => {
      const s = q.state?.data?.status
      return s === 'pending' || s === 'running' || s === 'aborting' ? 1500 : false
    },
  })
}

/** 中断任务。 */
export function useAbortPatternRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.post(`/pattern_search/runs/${runId}/abort`).then(r => r.data),
    onSuccess: (_res, runId) => {
      qc.invalidateQueries({ queryKey: ['pattern_runs'] })
      qc.invalidateQueries({ queryKey: ['pattern_run', runId] })
    },
  })
}

/** 删除任务记录。 */
export function useDeletePatternRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      client.delete(`/pattern_search/runs/${runId}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pattern_runs'] }),
  })
}
