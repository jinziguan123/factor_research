import { useQuery } from '@tanstack/vue-query'
import { toValue, type MaybeRefOrGetter } from 'vue'
import { client } from './client'

export interface ChanlunFx {
  dt: string
  mark: 'top' | 'bottom'
  price: number
  high: number
  low: number
}

export interface ChanlunBi {
  sdt: string
  edt: string
  direction: 'up' | 'down'
  high: number
  low: number
}

export interface ChanlunZs {
  sdt: string
  edt: string
  zg: number
  zd: number
  gg: number
  dd: number
}

export interface ChanlunBsp {
  dt: string
  bsp_type: 'buy1' | 'buy2' | 'buy3' | 'sell1' | 'sell2' | 'sell3'
  price: number
}

export interface ChanlunData {
  fx_list: ChanlunFx[]
  bi_list: ChanlunBi[]
  zs_list: ChanlunZs[]
  zs_up_list: ChanlunZs[]
  bsp_list: ChanlunBsp[]
}

export interface ChanlunQuery {
  symbol: string
  start: string
  end: string
  freq?: string
  adjust?: string
}

export function useChanlunAnalysis(params: MaybeRefOrGetter<ChanlunQuery | null>) {
  return useQuery<ChanlunData>({
    queryKey: ['chanlun', () => toValue(params)] as any,
    queryFn: async () => {
      const p = toValue(params)
      if (!p) throw new Error('no params')
      const { data } = await client.get('/chanlun/analyze', { params: p })
      return data as ChanlunData
    },
    enabled: () => {
      const p = toValue(params)
      return !!(p && p.symbol)
    },
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
}
