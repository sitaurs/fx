import { create } from 'zustand'
import api from '../lib/api'
import type { ClosedTrade } from '../lib/types'

interface TradeFilters {
  search: string
  result: 'all' | 'win' | 'loss' | 'be'
  dateFrom: string
  dateTo: string
}

interface TradeState {
  trades: ClosedTrade[]
  total: number
  page: number
  pageSize: number
  filters: TradeFilters
  loading: boolean
  fetchTrades: () => Promise<void>
  setPage: (page: number) => void
  setFilters: (f: Partial<TradeFilters>) => void
  getTrade: (tradeId: string) => Promise<ClosedTrade | null>
}

export const useTradeStore = create<TradeState>((set, get) => ({
  trades: [],
  total: 0,
  page: 1,
  pageSize: 20,
  filters: { search: '', result: 'all', dateFrom: '', dateTo: '' },
  loading: false,

  fetchTrades: async () => {
    set({ loading: true })
    const { page, pageSize, filters } = get()
    try {
      const params: Record<string, string | number> = {
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }
      if (filters.search) params.search = filters.search
      if (filters.result !== 'all') params.result_filter = filters.result
      if (filters.dateFrom) params.date_from = filters.dateFrom
      if (filters.dateTo) params.date_to = filters.dateTo
      const { data } = await api.get<ClosedTrade[]>('/api/trades', { params })
      set({ trades: data, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  setPage: (page) => {
    set({ page })
    get().fetchTrades()
  },

  setFilters: (f) => {
    set({ filters: { ...get().filters, ...f }, page: 1 })
    get().fetchTrades()
  },

  getTrade: async (tradeId) => {
    try {
      const { data } = await api.get<ClosedTrade>(`/api/trades/${tradeId}`)
      return data
    } catch {
      return null
    }
  },
}))
