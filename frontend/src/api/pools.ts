// 股票池 API 层
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { client } from './client'
import type { Ref } from 'vue'

export interface Pool {
  pool_id: number
  pool_name: string
  description: string
  symbols: string[]
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

/** 创建股票池 */
export function useCreatePool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { pool_name: string; description?: string; symbols?: string[] }) =>
      client.post('/pools', body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pools'] }),
  })
}

/** 更新股票池 */
export function useUpdatePool() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ poolId, body }: { poolId: number; body: Record<string, any> }) =>
      client.put(`/pools/${poolId}`, body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pools'] }),
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

/** 导入股票代码到股票池 */
export function useImportSymbols() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ poolId, symbols }: { poolId: number; symbols: string[] }) =>
      client.post(`/pools/${poolId}:import`, { symbols }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pools'] }),
  })
}
