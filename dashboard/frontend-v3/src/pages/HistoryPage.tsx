import { useEffect, useState } from 'react'
import { useTradeStore } from '../stores/tradeStore'
import DirectionBadge from '../components/shared/DirectionBadge'
import { formatCurrency, formatPips, formatRR, formatDuration, formatPrice } from '../lib/formatters'
import { RESULT_COLORS } from '../lib/constants'
import clsx from 'clsx'

export default function HistoryPage() {
  const { trades, loading, fetchTrades, filters, setFilters, page, setPage, pageSize } = useTradeStore()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => { fetchTrades() }, [fetchTrades])

  // Stats from loaded trades
  const totalTrades = trades.length
  const wins = trades.filter((t) => ['TP1_HIT', 'TP2_HIT', 'TRAIL_PROFIT'].includes(t.result || '')).length
  const winRate = totalTrades > 0 ? (wins / totalTrades * 100) : 0
  const totalPnl = trades.reduce((s, t) => s + (t.demo_pnl || 0), 0)
  const avgRR = trades.reduce((s, t) => s + (t.rr_achieved || 0), 0) / (totalTrades || 1)

  const handleSearch = () => {
    setFilters({ search })
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div>
        <h2 className="text-lg font-bold text-white">Trade History</h2>
        <p className="text-sm text-gray-500">Complete trade journal with AI post-mortem</p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Total Trades" value={String(totalTrades)} icon="receipt_long" color="text-primary" />
        <StatCard label="Win Rate" value={`${winRate.toFixed(1)}%`} icon="emoji_events" color="text-success" />
        <StatCard label="Total P/L" value={formatCurrency(totalPnl)} icon="payments" color={totalPnl >= 0 ? 'text-success' : 'text-danger'} />
        <StatCard label="Avg RR" value={formatRR(avgRR)} icon="speed" color="text-info" />
      </div>

      {/* Search + Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="flex-1 relative">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 text-lg">search</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            placeholder="Search by pair..."
            className="w-full pl-10 pr-4 py-2 bg-dark-surface border border-dark-border rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-primary/50 transition"
          />
        </div>
        <div className="flex items-center gap-1 bg-dark-surface rounded-lg p-0.5">
          {(['all', 'win', 'loss', 'be'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilters({ result: f })}
              className={clsx(
                'px-3 py-1.5 rounded-md text-xs font-medium transition-all capitalize',
                filters.result === f ? 'bg-primary text-white' : 'text-gray-500 hover:text-gray-300'
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <button className="px-3 py-2 bg-dark-surface border border-dark-border rounded-lg text-xs text-gray-400 hover:text-white transition flex items-center gap-1">
          <span className="material-symbols-outlined text-sm">download</span>
          Export CSV
        </button>
      </div>

      {/* Trade table */}
      <div className="glass-card overflow-hidden">
        {loading ? (
          <div className="p-8 text-center">
            <span className="material-symbols-outlined animate-spin text-primary text-3xl">progress_activity</span>
          </div>
        ) : trades.length === 0 ? (
          <div className="p-12 text-center text-gray-600">
            <span className="material-symbols-outlined text-4xl mb-2 block">history</span>
            No trades found
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden lg:block overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-dark-border text-xs text-gray-500">
                    <th className="text-left p-3 font-medium">Date</th>
                    <th className="text-left p-3 font-medium">Pair</th>
                    <th className="text-left p-3 font-medium">Dir</th>
                    <th className="text-right p-3 font-medium">Entry → Exit</th>
                    <th className="text-right p-3 font-medium">Pips</th>
                    <th className="text-right p-3 font-medium">P/L</th>
                    <th className="text-right p-3 font-medium">RR</th>
                    <th className="text-right p-3 font-medium">Score</th>
                    <th className="text-left p-3 font-medium">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <>
                      <tr
                        key={t.trade_id}
                        onClick={() => setExpandedId(expandedId === t.trade_id ? null : t.trade_id)}
                        className={clsx(
                          'border-b border-dark-border/50 cursor-pointer hover:bg-dark-hover/30 transition',
                          expandedId === t.trade_id && 'bg-dark-hover/20'
                        )}
                      >
                        <td className="p-3 text-gray-400 text-xs">
                          {t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '—'}
                        </td>
                        <td className="p-3 font-semibold text-white">{t.pair}</td>
                        <td className="p-3"><DirectionBadge direction={t.direction} /></td>
                        <td className="p-3 text-right font-mono text-xs text-gray-300">
                          {formatPrice(t.entry_price, t.pair)} → {t.exit_price ? formatPrice(t.exit_price, t.pair) : '—'}
                        </td>
                        <td className={clsx('p-3 text-right font-mono', (t.pips || 0) >= 0 ? 'text-success' : 'text-danger')}>
                          {t.pips?.toFixed(1) || '—'}
                        </td>
                        <td className={clsx('p-3 text-right font-semibold', (t.demo_pnl || 0) >= 0 ? 'text-success' : 'text-danger')}>
                          {formatCurrency(t.demo_pnl || 0)}
                        </td>
                        <td className="p-3 text-right text-gray-300">{t.rr_achieved ? formatRR(t.rr_achieved) : '—'}</td>
                        <td className="p-3 text-right text-gray-300">{t.confluence_score}/14</td>
                        <td className={clsx('p-3', RESULT_COLORS[t.result || ''] || 'text-gray-400')}>
                          {t.result?.replace(/_/g, ' ') || '—'}
                        </td>
                      </tr>
                      {/* Expanded post-mortem */}
                      {expandedId === t.trade_id && (
                        <tr key={`${t.trade_id}-pm`}>
                          <td colSpan={9} className="p-4 bg-dark-bg/40">
                            <PostMortemPanel trade={t} />
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div className="lg:hidden space-y-2 p-3">
              {trades.map((t) => (
                <div key={t.trade_id} className="bg-dark-bg/40 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-white">{t.pair}</span>
                      <DirectionBadge direction={t.direction} />
                    </div>
                    <span className={clsx('text-sm font-semibold', (t.demo_pnl || 0) >= 0 ? 'text-success' : 'text-danger')}>
                      {formatCurrency(t.demo_pnl || 0)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs text-gray-500">
                    <span>{t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '—'}</span>
                    <span>{formatPips(t.pips || 0)} • {t.rr_achieved ? formatRR(t.rr_achieved) : '—'}</span>
                    <span className={RESULT_COLORS[t.result || '']}>{t.result?.replace(/_/g, ' ')}</span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between text-sm">
        <p className="text-gray-500">Page {page}</p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
            disabled={page <= 1}
            className="px-3 py-1.5 rounded-lg bg-dark-surface text-gray-400 text-xs hover:text-white disabled:opacity-30 transition"
          >
            Previous
          </button>
          <button
            onClick={() => setPage(page + 1)}
            disabled={trades.length < pageSize}
            className="px-3 py-1.5 rounded-lg bg-dark-surface text-gray-400 text-xs hover:text-white disabled:opacity-30 transition"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, icon, color }: { label: string; value: string; icon: string; color: string }) {
  return (
    <div className="glass-card p-3 flex items-center gap-3">
      <div className={clsx('w-10 h-10 rounded-xl flex items-center justify-center bg-dark-bg/60', color)}>
        <span className="material-symbols-outlined text-xl">{icon}</span>
      </div>
      <div>
        <p className="text-[10px] text-gray-500">{label}</p>
        <p className="text-sm font-bold text-white">{value}</p>
      </div>
    </div>
  )
}

function PostMortemPanel({ trade }: { trade: ReturnType<typeof useTradeStore.getState>['trades'][0] }) {
  const pm = trade.post_mortem

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold text-white flex items-center gap-2">
        <span className="material-symbols-outlined text-sm text-info">psychology</span>
        AI Post-Mortem Analysis
      </h4>
      {pm ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div>
            <p className="text-gray-500 mb-1">Summary</p>
            <p className="text-gray-300">{pm.summary}</p>
          </div>
          <div>
            <p className="text-gray-500 mb-1">Entry Quality</p>
            <p className="text-gray-300">{pm.entry_quality}</p>
          </div>
          <div>
            <p className="text-gray-500 mb-1">Exit Quality</p>
            <p className="text-gray-300">{pm.exit_quality}</p>
          </div>
          {pm.lessons && pm.lessons.length > 0 && (
            <div>
              <p className="text-gray-500 mb-1">Lessons</p>
              <ul className="list-disc list-inside text-gray-300 space-y-0.5">
                {pm.lessons.map((l, i) => <li key={i}>{l}</li>)}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-gray-600">No post-mortem available for this trade.</p>
      )}
      <div className="flex items-center gap-4 text-xs text-gray-500 pt-1 border-t border-dark-border/50">
        <span>Strategy: {trade.strategy_mode}</span>
        <span>Duration: {formatDuration(trade.duration_minutes)}</span>
        <span>Score: {trade.confluence_score}/14</span>
      </div>
    </div>
  )
}
