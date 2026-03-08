import { create } from 'zustand'
import api from '../lib/api'
import type {
  AnalyticsSummary,
  DailyReturn,
  StrategyPerformance,
  PairPerformance,
} from '../lib/types'

interface AnalyticsState {
  summary: AnalyticsSummary | null
  dailyReturns: DailyReturn[]
  strategyPerf: StrategyPerformance[]
  pairPerf: PairPerformance[]
  period: '7d' | '30d' | 'all'
  loading: boolean
  setPeriod: (p: '7d' | '30d' | 'all') => void
  fetchAll: () => Promise<void>
}

export const useAnalyticsStore = create<AnalyticsState>((set, get) => ({
  summary: null,
  dailyReturns: [],
  strategyPerf: [],
  pairPerf: [],
  period: 'all',
  loading: true,

  setPeriod: (p) => {
    set({ period: p })
    get().fetchAll()
  },

  fetchAll: async () => {
    set({ loading: true })
    const { period } = get()
    try {
      const [sumRes, perfRes, stratRes, pairRes] = await Promise.all([
        api.get('/api/analytics/summary', { params: { period } }).catch(() => null),
        api.get('/api/analytics/performance', { params: { period } }).catch(() => null),
        api.get('/api/analytics/by-strategy', { params: { period } }).catch(() => null),
        api.get('/api/analytics/by-pair', { params: { period } }).catch(() => null),
      ])
      set({
        summary: sumRes?.data ?? null,
        dailyReturns: perfRes?.data ?? [],
        strategyPerf: stratRes?.data ?? [],
        pairPerf: pairRes?.data ?? [],
        loading: false,
      })
    } catch {
      set({ loading: false })
    }
  },
}))
