// 股票池 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { client } from './client'

export interface StockSymbol {
  symbol: string
  name: string
}

export interface Pool {
  pool_id: number
  pool_name: string
  description: string | null
  // 后端返回 [{symbol, name}, ...]；旧代码里错写成 string[]，渲染会显示 [object Object]。
  symbols: StockSymbol[]
  created_at: string
  updated_at: string
}

/** 获取全部股票池 */
export function usePools() {
  return useQuery<Pool[]>({
    queryKey: ['pools'],
    queryFn: () => client.get('/pools').then(r => r.data),
  })
}

/** 获取单个股票池 */
export function usePool(poolId: Ref<number | string>) {
  return useQuery<Pool>({
    queryKey: ['pool', poolId],
    queryFn: () => client.get(`/pools/${poolId.value}`).then(r => r.data),
    enabled: () => !!poolId.value,
  })
}

// 请求体契约：后端 PoolIn = { name, description?, symbols? }
export interface PoolBody {
  name: string
  description?: string | null
  symbols?: string[]
}

/** 创建股票池 */
export function useCreatePool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: PoolBody) =>
      client.post('/pools', body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pools'] }),
  })
}

/** 更新股票池 */
export function useUpdatePool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ poolId, body }: { poolId: number; body: PoolBody }) =>
      client.put(`/pools/${poolId}`, body).then(r => r.data),
    onSuccess: (_res, vars) => {
      // 列表和详情查询 key 不同，两个都要 invalidate，否则详情页的 symbol 列表不刷新。
      qc.invalidateQueries({ queryKey: ['pools'] })
      qc.invalidateQueries({ queryKey: ['pool', vars.poolId] })
    },
  })
}

/** 删除股票池 */
export function useDeletePool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (poolId: number) => client.delete(`/pools/${poolId}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pools'] }),
  })
}

/** 导入股票代码到股票池。后端期望 { text }，空白/逗号/分号均可分隔，router 端解析。 */
export function useImportSymbols() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ poolId, text }: { poolId: number; text: string }) =>
      client.post(`/pools/${poolId}:import`, { text }).then(r => r.data as { inserted: number; total_input: number }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ['pools'] })
      qc.invalidateQueries({ queryKey: ['pool', vars.poolId] })
    },
  })
}

/** 按代码 / 中文名模糊搜索股票（用于池编辑器的下拉补全）。
 * 传响应式的 q（Ref / ComputedRef / getter），值变化自动 refetch。
 */
export function useSearchSymbols(q: MaybeRefOrGetter<string>) {
  return useQuery<StockSymbol[]>({
    queryKey: ['symbols', q],
    queryFn: () => client.get('/symbols', {
      params: { q: toValue(q) ?? '', limit: 50 },
    }).then(r => r.data),
    // 空 q 时后端也返回前 50 条，点开下拉就能看到初始候选；所以不加 enabled 条件。
    staleTime: 30_000,
  })
}

/** 按 glob 模式批量匹配股票（一次性调用，不做 query 缓存）。
 *
 * 用于"按规则批量添加"和"全量添加"场景。与 ``useSearchSymbols`` 的区别：
 * - 返回**所有**匹配项（后端上限 10000），不是搜索补全的前 50 条；
 * - 只匹配 symbol 字段，不匹配 name；
 * - glob 通配符：``*`` 任意长度、``?`` 单字符。
 *
 * 例：
 *   await matchSymbolsByPattern('*.SZ')   // 全部深交所
 *   await matchSymbolsByPattern('60*')    // 60 开头（沪市主板）
 *   await matchSymbolsByPattern('*')      // 全部
 */
export async function matchSymbolsByPattern(
  pattern: string,
): Promise<StockSymbol[]> {
  const r = await client.get('/symbols', { params: { pattern } })
  return r.data as StockSymbol[]
}
