import { useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import BottomNav from './BottomNav'
import Header from './Header'
import { useWebSocket } from '../../hooks/useWebSocket'
import { usePolling } from '../../hooks/usePolling'
import { usePortfolioStore } from '../../stores/portfolioStore'
import { useSystemStore } from '../../stores/systemStore'
import { useAnalysisStore } from '../../stores/analysisStore'
import { useAuthStore } from '../../stores/authStore'

export default function AppLayout() {
  const location = useLocation()
  const initAuth = useAuthStore((s) => s.initFromStorage)
  const fetchPortfolio = usePortfolioStore((s) => s.fetchPortfolio)
  const fetchStatus = useSystemStore((s) => s.fetchStatus)
  const fetchAnalyses = useAnalysisStore((s) => s.fetchAnalyses)
  const fetchPending = useAnalysisStore((s) => s.fetchPendingSetups)

  // Init auth on mount
  useEffect(() => { initAuth() }, [initAuth])

  // WebSocket connection
  useWebSocket()

  // Polling (30s)
  usePolling(fetchPortfolio, 30000)
  usePolling(fetchStatus, 30000)
  usePolling(fetchAnalyses, 30000)
  usePolling(fetchPending, 30000)

  return (
    <div className="flex h-screen overflow-hidden bg-dark-bg">
      {/* Desktop Sidebar */}
      <Sidebar currentPath={location.pathname} />

      {/* Main content area */}
      <div className="flex flex-1 flex-col min-w-0">
        <Header />
        <main className="flex-1 overflow-y-auto p-4 lg:p-6 pb-20 lg:pb-6">
          <Outlet />
        </main>
      </div>

      {/* Mobile Bottom Nav */}
      <BottomNav currentPath={location.pathname} />
    </div>
  )
}
