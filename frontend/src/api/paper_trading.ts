// 模拟盘(纸上交易)API 层
//
// 账户绑定一条选股策略(factor_items/method/pool/top_n)；调仓时后端同步跑一次
// signal、用快照价撮合、落库。详情页展示净值曲线 + 持仓 + 成交流水。
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import { type Ref } from 'vue'
import { client } from './client'

/** 列表项(GET /paper-accounts 返回的精简字段)。 */
export interface PaperAccount {
  account_id: string
  name: string
  method: string
  pool_id: number
  init_cash: number
  cash: number
  status: string
  created_at: string
  last_rebalance_at?: string | null
}

export interface PaperPosition {
  symbol: string
  qty: number
  avg_price: number
}

export interface PaperNavPoint {
  ts: string
  nav: number
  cash: number
  market_value: number
}

export interface PaperTrade {
  ts: string
  symbol: string
  side: string
  qty: number
  price: number
  fee: number
}

/** GET /paper-accounts/{id} 详情：账户 + 持仓 + 净值时序 + 成交。 */
export interface PaperAccountState {
  account: PaperAccount & {
    n_groups: number
    top_n: number | null
    factor_items: { factor_id: string; [k: string]: any }[]
  }
  positions: PaperPosition[]
  nav_series: PaperNavPoint[]
  trades: PaperTrade[]
}

/** 新建模拟盘账户，返回 { account_id, status }。 */
export function useCreatePaperAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, any>) =>
      client.post('/paper-accounts', body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['paper-accounts'] }),
  })
}

/** 账户列表。 */
export function usePaperAccounts() {
  return useQuery<PaperAccount[]>({
    queryKey: ['paper-accounts'],
    queryFn: () => client.get('/paper-accounts').then(r => r.data),
  })
}

/** 账户详情(账户 + 持仓 + 净值 + 成交)。 */
export function usePaperAccount(accountId: Ref<string>) {
  return useQuery<PaperAccountState>({
    queryKey: ['paper-account', accountId],
    queryFn: () =>
      client.get(`/paper-accounts/${accountId.value}`).then(r => r.data),
    enabled: () => !!accountId.value,
  })
}

/** 调仓一次(同步：跑 signal 然后快照价撮合 然后落库；可能数秒~数十秒，故放宽 timeout)。 */
export function useRebalance() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (accountId: string) =>
      client
        .post(`/paper-accounts/${accountId}/rebalance`, {}, { timeout: 120_000 })
        .then(r => r.data),
    onSuccess: (_res, accountId) => {
      qc.invalidateQueries({ queryKey: ['paper-account', accountId] })
      qc.invalidateQueries({ queryKey: ['paper-accounts'] })
    },
  })
}

/** 删除账户(级联删持仓/净值/成交)。 */
export function useDeletePaperAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (accountId: string) =>
      client.delete(`/paper-accounts/${accountId}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['paper-accounts'] }),
  })
}
