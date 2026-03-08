import { create } from 'zustand'
import api from '../lib/api'
import type { SystemStatus, RuntimeConfig } from '../lib/types'

interface SystemState {
  status: SystemStatus | null
  config: RuntimeConfig | null
  loading: boolean
  fetchStatus: () => Promise<void>
  fetchConfig: () => Promise<void>
  updateConfig: (patch: Partial<RuntimeConfig>) => Promise<boolean>
  unhalt: () => Promise<boolean>
}

export const useSystemStore = create<SystemState>((set) => ({
  status: null,
  config: null,
  loading: true,

  fetchStatus: async () => {
    try {
      const { data } = await api.get<SystemStatus>('/api/system/status')
      set({ status: data, loading: false })
    } catch {
      set({ loading: false })
    }
  },

  fetchConfig: async () => {
    try {
      const { data } = await api.get<{ success: boolean; config: RuntimeConfig }>('/api/system/config')
      if (data.success) set({ config: data.config })
    } catch {
      // silent
    }
  },

  updateConfig: async (patch) => {
    try {
      const { data } = await api.patch<{ success: boolean; config: RuntimeConfig }>('/api/system/config', patch)
      if (data.success) {
        set({ config: data.config })
        return true
      }
      return false
    } catch {
      return false
    }
  },

  unhalt: async () => {
    try {
      const { data } = await api.post<{ success: boolean }>('/api/system/unhalt')
      return data.success
    } catch {
      return false
    }
  },
}))
