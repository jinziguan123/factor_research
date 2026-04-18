// K 线 API 层：日线 + 分钟线，raw / qfq 切换。
// 仅查询类，不做 mutation；由页面手动 refetch。
import { useQuery } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter } from 'vue'
import { client } from './client'

export interface DailyKlineRow {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount_k: number
}

export interface MinuteKlineRow {
  ts: string // "YYYY-MM-DD HH:MM"
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount_k: number
}

export interface KlineQuery {
  symbol: string
  start: string // YYYY-MM-DD
  end: string
  adjust: 'qfq' | 'none'
}

/** 日线行情 query hook。enabled 由 symbol 是否填写决定。 */
export function useDailyKline(params: MaybeRefOrGetter<KlineQuery | null>) {
  return useQuery({
    queryKey: ['klines', 'daily', () => toValue(params)] as any,
    queryFn: async () => {
      const p = toValue(params)
      if (!p) throw new Error('no params')
      const { data } = await client.get('/bars/daily', { params: p })
      return data as { symbol: string; adjust: string; rows: DailyKlineRow[] }
    },
    enabled: () => {
      const p = toValue(params)
      return !!(p && p.symbol)
    },
    // 切换 symbol / 日期 / adjust 时立刻失效旧缓存，不显示过期数据。
    staleTime: 0,
  })
}

/** 分钟线行情 query hook。 */
export function useMinuteKline(params: MaybeRefOrGetter<KlineQuery | null>) {
  return useQuery({
    queryKey: ['klines', 'minute', () => toValue(params)] as any,
    queryFn: async () => {
      const p = toValue(params)
      if (!p) throw new Error('no params')
      const { data } = await client.get('/bars/minute', { params: p })
      return data as { symbol: string; adjust: string; rows: MinuteKlineRow[] }
    },
    enabled: () => {
      const p = toValue(params)
      return !!(p && p.symbol)
    },
    staleTime: 0,
  })
}
