import { useState } from 'react'
import { usePortfolioStore } from '../../stores/portfolioStore'
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine } from 'recharts'
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const RefLine = ReferenceLine as any
import clsx from 'clsx'

const PERIODS = ['1W', '1M', 'YTD', 'ALL'] as const

export default function EquityCurve() {
  const equityPoints = usePortfolioStore((s) => s.equityPoints)
  const fetchEquity = usePortfolioStore((s) => s.fetchEquity)
  const [period, setPeriod] = useState<string>('ALL')

  const handlePeriod = (p: string) => {
    setPeriod(p)
    fetchEquity(p.toLowerCase())
  }

  // Find HWM for reference line
  const hwm = equityPoints.length > 0 ? Math.max(...equityPoints.map((p) => p.hwm)) : 0

  return (
    <div className="glass-card p-4 lg:p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-white">Equity Curve</h3>
          <p className="text-xs text-gray-500 mt-0.5">Balance & High Water Mark</p>
        </div>
        <div className="flex items-center gap-1 bg-dark-bg/60 rounded-lg p-0.5">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => handlePeriod(p)}
              className={clsx(
                'px-3 py-1 rounded-md text-xs font-medium transition-all',
                period === p
                  ? 'bg-primary text-white'
                  : 'text-gray-500 hover:text-gray-300'
              )}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="h-[240px] lg:h-[300px]">
        {equityPoints.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={equityPoints} margin={{ top: 5, right: 5, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: '#6b7280' }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#6b7280' }}
                tickLine={false}
                axisLine={false}
                domain={['auto', 'auto']}
                tickFormatter={(v: number) => `$${v.toFixed(0)}`}
              />
              <Tooltip
                contentStyle={{
                  background: 'rgba(17,24,39,0.95)',
                  border: '1px solid rgba(55,65,81,0.5)',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelStyle={{ color: '#9ca3af' }}
              />
              {hwm > 0 && (
                <RefLine
                  y={hwm}
                  stroke="#6b7280"
                  strokeDasharray="4 4"
                  label={{ value: 'HWM', position: 'right', fill: '#6b7280', fontSize: 10 }}
                />
              )}
              <Area
                type="monotone"
                dataKey="balance"
                stroke="#0ea5e9"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#equityGrad)"
                name="Balance"
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full flex items-center justify-center text-gray-600 text-sm">
            <span className="material-symbols-outlined mr-2">show_chart</span>
            No equity data yet
          </div>
        )}
      </div>
    </div>
  )
}
