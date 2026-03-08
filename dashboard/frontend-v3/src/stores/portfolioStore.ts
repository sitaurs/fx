import { create } from 'zustand'
import api from '../lib/api'
import type { Portfolio, EquityPoint } from '../lib/types'

const DEFAULT_PORTFOLIO: Portfolio = {
  balance: 0,
  initial_balance: 0,
  high_water_mark: 0,
  daily_start_balance: 0,
  floating_pnl: 0,
  effective_balance: 0,
  daily_drawdown_pct: 0,
  total_drawdown_pct: 0,
  max_daily_drawdown: 0.05,
  max_total_drawdown: 0.15,
  is_halted: false,
  halt_reason: '',
  mode: 'demo',
  active_trades: [],
  active_count: 0,
  max_concurrent: 2,
  correlation_status: {},
  challenge_mode: 'none',
  position_sizing_mode: 'risk_percent',
  fixed_lot_size: 0.01,
  drawdown_guard_enabled: true,
  active_revalidation_enabled: true,
  active_revalidation_interval_minutes: 90,
}

interface PortfolioState {
  portfolio: Portfolio
  equityPoints: EquityPoint[]
  loading: boolean
  error: string | null
  fetchPortfolio: () => Promise<void>
  fetchEquity: (period?: string) => Promise<void>
  updateFromWS: (data: Record<string, unknown>) => void
}

export const usePortfolioStore = create<PortfolioState>((set, get) => ({
  portfolio: DEFAULT_PORTFOLIO,
  equityPoints: [],
  loading: true,
  error: null,

  fetchPortfolio: async () => {
    try {
      const { data } = await api.get<Portfolio>('/api/portfolio')
      set({ portfolio: data, loading: false, error: null })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to fetch portfolio'
      set({ loading: false, error: msg })
    }
  },

  fetchEquity: async (period?: string) => {
    try {
      const params = period ? { period } : {}
      const { data } = await api.get<{ points: EquityPoint[] }>('/api/portfolio/equity', { params })
      set({ equityPoints: data.points || [] })
    } catch {
      // silent fail for equity
    }
  },

  updateFromWS: (data) => {
    const p = get().portfolio
    set({
      portfolio: {
        ...p,
        balance: (data.balance as number) ?? p.balance,
        floating_pnl: (data.floating_pnl as number) ?? p.floating_pnl,
        active_count: (data.active_count as number) ?? p.active_count,
        is_halted: (data.is_halted as boolean) ?? p.is_halted,
      },
    })
  },
}))
