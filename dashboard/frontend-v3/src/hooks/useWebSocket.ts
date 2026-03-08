import { useEffect, useRef, useCallback } from 'react'
import { WS_BASE } from '../lib/constants'
import { usePortfolioStore } from '../stores/portfolioStore'
import { useAnalysisStore } from '../stores/analysisStore'
import { useEventStore } from '../stores/eventStore'

const WS_TOKEN = localStorage.getItem('ws_token') || ''

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()
  const updatePortfolio = usePortfolioStore((s) => s.updateFromWS)
  const updateAnalysis = useAnalysisStore((s) => s.updateFromWS)
  const addEvent = useEventStore((s) => s.addEvent)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const url = `${WS_BASE}/ws${WS_TOKEN ? `?token=${WS_TOKEN}` : ''}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      console.log('[WS] Connected')
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        const { type, data } = msg

        switch (type) {
          case 'PORTFOLIO_UPDATE':
            updatePortfolio(data)
            break
          case 'ANALYSIS_UPDATE':
            updateAnalysis(data)
            break
          case 'TRADE_CLOSED':
          case 'STATE_CHANGE':
            // Add to event feed
            addEvent({
              time: new Date().toISOString(),
              type,
              pair: data?.pair || '—',
              summary: data?.summary || JSON.stringify(data).slice(0, 80),
            })
            break
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      console.log('[WS] Disconnected, reconnecting in 3s...')
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [updatePortfolio, updateAnalysis, addEvent])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])
}
