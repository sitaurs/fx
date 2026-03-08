import { useEffect } from 'react'
import { useAnalyticsStore } from '../stores/analyticsStore'
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  Cell,
  BarChart,
} from 'recharts'
import { formatCurrency, formatPercent, formatRR } from '../lib/formatters'
import clsx from 'clsx'

const PERIOD_OPTIONS = [
  { value: '7d', label: '7 Days' },
  { value: '30d', label: 'This Month' },
  { value: 'all', label: 'All Time' },
] as const

const PIE_COLORS = ['#10b981', '#ef4444', '#6b7280']

export default function AnalyticsPage() {
  const { summary, dailyReturns, strategyPerf, pairPerf, period, setPeriod, loading, fetchAll } = useAnalyticsStore()

  useEffect(() => { fetchAll() }, [fetchAll])

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header + Period */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Analytics</h2>
          <p className="text-sm text-gray-500">Performance metrics & insights</p>
        </div>
        <div className="flex items-center gap-1 bg-dark-surface rounded-lg p-0.5">
          {PERIOD_OPTIONS.map((p) => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={clsx(
                'px-3 py-1.5 rounded-md text-xs font-medium transition-all',
                period === p.value ? 'bg-primary text-white' : 'text-gray-500 hover:text-gray-300'
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <span className="material-symbols-outlined animate-spin text-primary text-3xl">progress_activity</span>
        </div>
      ) : (
        <>
          {/* Metric cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <MetricCard
              label="Win Rate"
              value={summary ? `${(summary.win_rate * 100).toFixed(1)}%` : '—'}
              icon="emoji_events"
              color="success"
              ring={summary?.win_rate}
            />
            <MetricCard
              label="Avg RR"
              value={summary ? formatRR(summary.avg_rr) : '—'}
              icon="speed"
              color="info"
            />
            <MetricCard
              label="Total P/L"
              value={summary ? formatCurrency(summary.total_pnl) : '—'}
              icon="payments"
              color={(summary?.total_pnl ?? 0) >= 0 ? 'success' : 'danger'}
            />
            <MetricCard
              label="Profit Factor"
              value={summary ? summary.profit_factor.toFixed(1) : '—'}
              icon="trending_up"
              color="primary"
            />
          </div>

          {/* Performance chart + Win/Loss donut */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            {/* Main chart */}
            <div className="xl:col-span-2 glass-card p-4 lg:p-5">
              <h3 className="text-sm font-semibold text-white mb-4">Performance Overview</h3>
              <div className="h-[280px]">
                {dailyReturns.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={dailyReturns} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: '#6b7280' }}
                        tickLine={false}
                        axisLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 10, fill: '#6b7280' }}
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v: number) => `$${v}`}
                      />
                      <Tooltip
                        contentStyle={{
                          background: 'rgba(17,24,39,0.95)',
                          border: '1px solid rgba(55,65,81,0.5)',
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                      />
                      <Bar
                        dataKey="pnl"
                        name="Daily P/L"
                        fill="#374151"
                        radius={[4, 4, 0, 0]}
                      />
                      <Line
                        type="monotone"
                        dataKey="cumulative"
                        name="Cumulative"
                        stroke="#0ea5e9"
                        strokeWidth={2}
                        dot={false}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <PlaceholderChart />
                )}
              </div>
            </div>

            {/* Win/Loss donut */}
            <div className="glass-card p-4 lg:p-5">
              <h3 className="text-sm font-semibold text-white mb-4">Win/Loss Distribution</h3>
              <div className="h-[200px] flex items-center justify-center">
                {summary && summary.total_trades > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={[
                          { name: 'Wins', value: summary.wins },
                          { name: 'Losses', value: summary.losses },
                          { name: 'BE', value: summary.breakeven },
                        ]}
                        cx="50%"
                        cy="50%"
                        innerRadius={50}
                        outerRadius={80}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {PIE_COLORS.map((color, i) => (
                          <Cell key={i} fill={color} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{
                          background: 'rgba(17,24,39,0.95)',
                          border: '1px solid rgba(55,65,81,0.5)',
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="text-gray-600 text-sm text-center">
                    <span className="material-symbols-outlined text-3xl mb-2 block">donut_large</span>
                    No data
                  </div>
                )}
              </div>
              {summary && (
                <div className="flex items-center justify-center gap-4 mt-2 text-xs">
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-success" />Wins: {summary.wins}</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-danger" />Losses: {summary.losses}</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-gray-500" />BE: {summary.breakeven}</span>
                </div>
              )}
            </div>
          </div>

          {/* Strategy + Pair performance */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {/* Strategy table */}
            <div className="glass-card p-4 lg:p-5">
              <h3 className="text-sm font-semibold text-white mb-4">Strategy Performance</h3>
              {strategyPerf.length > 0 ? (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-dark-border">
                      <th className="text-left pb-2 font-medium">Strategy</th>
                      <th className="text-right pb-2 font-medium">Trades</th>
                      <th className="text-right pb-2 font-medium">Win Rate</th>
                      <th className="text-right pb-2 font-medium">Net P/L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strategyPerf.map((s) => (
                      <tr key={s.strategy} className="border-b border-dark-border/30">
                        <td className="py-2 text-white font-medium">{s.strategy}</td>
                        <td className="py-2 text-right text-gray-400">{s.trades}</td>
                        <td className="py-2 text-right text-gray-300">{formatPercent(s.win_rate)}</td>
                        <td className={clsx('py-2 text-right font-semibold', s.net_pnl >= 0 ? 'text-success' : 'text-danger')}>
                          {formatCurrency(s.net_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-gray-600 text-sm text-center py-4">No strategy data</p>
              )}
            </div>

            {/* Pair performance bars */}
            <div className="glass-card p-4 lg:p-5">
              <h3 className="text-sm font-semibold text-white mb-4">Pair Performance</h3>
              {pairPerf.length > 0 ? (
                <div className="h-[250px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={pairPerf} layout="vertical" margin={{ top: 0, right: 10, left: 50, bottom: 0 }}>
                      <XAxis
                        type="number"
                        tick={{ fontSize: 10, fill: '#6b7280' }}
                        tickLine={false}
                        axisLine={false}
                        tickFormatter={(v: number) => `$${v}`}
                      />
                      <YAxis
                        type="category"
                        dataKey="pair"
                        tick={{ fontSize: 11, fill: '#e5e7eb' }}
                        tickLine={false}
                        axisLine={false}
                      />
                      <Tooltip
                        contentStyle={{
                          background: 'rgba(17,24,39,0.95)',
                          border: '1px solid rgba(55,65,81,0.5)',
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                      />
                      <Bar dataKey="net_pnl" name="Net P/L" radius={[0, 4, 4, 0]}>
                        {pairPerf.map((p, i) => (
                          <Cell key={i} fill={p.net_pnl >= 0 ? '#10b981' : '#ef4444'} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="text-gray-600 text-sm text-center py-4">No pair data</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function MetricCard({ label, value, icon, color, ring }: {
  label: string
  value: string
  icon: string
  color: string
  ring?: number
}) {
  const colorClass = {
    success: 'text-success bg-success/10',
    danger: 'text-danger bg-danger/10',
    warning: 'text-warning bg-warning/10',
    info: 'text-info bg-info/10',
    primary: 'text-primary bg-primary/10',
  }[color] || 'text-primary bg-primary/10'

  return (
    <div className="glass-card p-4 flex items-center gap-3">
      <div className={clsx('w-12 h-12 rounded-xl flex items-center justify-center relative', colorClass)}>
        <span className="material-symbols-outlined text-xl">{icon}</span>
        {ring !== undefined && (
          <svg className="absolute inset-0 w-full h-full -rotate-90">
            <circle cx="24" cy="24" r="20" fill="none" stroke="rgba(55,65,81,0.3)" strokeWidth="3" />
            <circle
              cx="24" cy="24" r="20" fill="none" stroke="currentColor" strokeWidth="3"
              strokeDasharray={`${ring * 125.6} 125.6`}
              strokeLinecap="round"
              className={color === 'success' ? 'text-success' : 'text-primary'}
            />
          </svg>
        )}
      </div>
      <div>
        <p className="text-[10px] text-gray-500">{label}</p>
        <p className="text-lg font-bold text-white">{value}</p>
      </div>
    </div>
  )
}

function PlaceholderChart() {
  return (
    <div className="h-full flex items-center justify-center text-gray-600 text-sm">
      <span className="material-symbols-outlined mr-2">analytics</span>
      No performance data yet
    </div>
  )
}
