import { useEffect } from 'react'
import { usePortfolioStore } from '../stores/portfolioStore'
import KPICards from '../components/dashboard/KPICards'
import EquityCurve from '../components/dashboard/EquityCurve'
import MiniPositionList from '../components/dashboard/MiniPositionList'
import RadarGrid from '../components/dashboard/RadarGrid'
import EventFeed from '../components/dashboard/EventFeed'

export default function DashboardPage() {
  const fetchEquity = usePortfolioStore((s) => s.fetchEquity)

  useEffect(() => {
    fetchEquity()
  }, [fetchEquity])

  return (
    <div className="space-y-6 animate-fade-in">
      {/* KPI Cards */}
      <KPICards />

      {/* Main grid: Equity + Sidebar */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Equity Curve — 2 cols */}
        <div className="xl:col-span-2">
          <EquityCurve />
        </div>

        {/* Active Positions sidebar */}
        <div>
          <MiniPositionList />
        </div>
      </div>

      {/* Bottom grid: Radar + Events */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <RadarGrid />
        <EventFeed />
      </div>
    </div>
  )
}
