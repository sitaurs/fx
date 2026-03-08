import { create } from 'zustand'
import api from '../lib/api'
import type { Analysis, PendingSetup } from '../lib/types'

interface AnalysisState {
  analyses: Analysis[]
  pendingSetups: PendingSetup[]
  loading: boolean
  lastScan: string | null
  fetchAnalyses: () => Promise<void>
  fetchPendingSetups: () => Promise<void>
  updateFromWS: (data: Record<string, unknown>) => void
}

export const useAnalysisStore = create<AnalysisState>((set, get) => ({
  analyses: [],
  pendingSetups: [],
  loading: false,
  lastScan: null,

  fetchAnalyses: async () => {
    try {
      const { data } = await api.get<Analysis[]>('/api/analysis/live')
      set({ analyses: data, lastScan: new Date().toISOString() })
    } catch {
      // silent
    }
  },

  fetchPendingSetups: async () => {
    try {
      const { data } = await api.get<PendingSetup[]>('/api/pending-setups')
      set({ pendingSetups: data })
    } catch {
      // silent
    }
  },

  updateFromWS: (data) => {
    const pair = (data.pair as string)?.toUpperCase()
    if (!pair) return
    const existing = get().analyses.filter((a) => a.pair !== pair)
    existing.push(data as unknown as Analysis)
    set({ analyses: existing, lastScan: new Date().toISOString() })
  },
}))
