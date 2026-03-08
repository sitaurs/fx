import { useEffect } from 'react'
import { useEventStore } from '../../stores/eventStore'
import api from '../../lib/api'
import clsx from 'clsx'

const EVENT_COLORS: Record<string, string> = {
  ANALYSIS_UPDATE: 'bg-primary',
  STATE_CHANGE: 'bg-info',
  TRADE_CLOSED: 'bg-success',
  PORTFOLIO_UPDATE: 'bg-warning',
}

const EVENT_ICONS: Record<string, string> = {
  ANALYSIS_UPDATE: 'analytics',
  STATE_CHANGE: 'swap_horiz',
  TRADE_CLOSED: 'done_all',
  PORTFOLIO_UPDATE: 'account_balance_wallet',
}

export default function EventFeed() {
  const events = useEventStore((s) => s.events)
  const setEvents = useEventStore((s) => s.setEvents)

  useEffect(() => {
    api.get('/api/events').then(({ data }) => {
      if (Array.isArray(data)) setEvents(data)
    }).catch(() => {})
  }, [setEvents])

  return (
    <div className="glass-card p-4 lg:p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-white">Recent Events</h3>
          <p className="text-xs text-gray-500 mt-0.5">Live activity feed</p>
        </div>
        <span className="material-symbols-outlined text-lg text-gray-500">notifications</span>
      </div>

      <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
        {events.length === 0 ? (
          <div className="py-8 text-center text-gray-600 text-sm">
            <span className="material-symbols-outlined text-3xl mb-2 block">event_note</span>
            No events yet
          </div>
        ) : (
          events.slice(0, 20).map((evt, i) => (
            <div key={i} className="flex items-start gap-3 p-2 rounded-lg hover:bg-dark-bg/40 transition">
              <div className={clsx(
                'w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5',
                (EVENT_COLORS[evt.type] || 'bg-gray-500') + '/10'
              )}>
                <span className={clsx(
                  'material-symbols-outlined text-sm',
                  evt.type === 'ANALYSIS_UPDATE' && 'text-primary',
                  evt.type === 'STATE_CHANGE' && 'text-info',
                  evt.type === 'TRADE_CLOSED' && 'text-success',
                  evt.type === 'PORTFOLIO_UPDATE' && 'text-warning',
                )}>
                  {EVENT_ICONS[evt.type] || 'info'}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-gray-300">{evt.pair}</span>
                  <span className="text-[10px] text-gray-600">{evt.type.replace(/_/g, ' ')}</span>
                </div>
                <p className="text-xs text-gray-500 truncate">{evt.summary}</p>
              </div>
              <span className="text-[10px] text-gray-600 shrink-0">{evt.time}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
