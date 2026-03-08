import { create } from 'zustand'
import type { DashboardEvent } from '../lib/types'

interface EventState {
  events: DashboardEvent[]
  addEvent: (evt: DashboardEvent) => void
  setEvents: (evts: DashboardEvent[]) => void
}

export const useEventStore = create<EventState>((set) => ({
  events: [],
  addEvent: (evt) =>
    set((s) => ({ events: [evt, ...s.events].slice(0, 100) })),
  setEvents: (evts) => set({ events: evts }),
}))
