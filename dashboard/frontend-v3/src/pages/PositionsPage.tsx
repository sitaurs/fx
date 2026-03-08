import { usePortfolioStore } from '../stores/portfolioStore'
import { useAnalysisStore } from '../stores/analysisStore'
import DirectionBadge from '../components/shared/DirectionBadge'
import { formatCurrency, formatPips, formatPrice, formatTimeAgo } from '../lib/formatters'
import api from '../lib/api'
import clsx from 'clsx'
import { useState } from 'react'

export default function PositionsPage() {
  const trades = usePortfolioStore((s) => s.portfolio.active_trades)
  const pending = useAnalysisStore((s) => s.pendingSetups)
  const fetchPortfolio = usePortfolioStore((s) => s.fetchPortfolio)
  const [filter, setFilter] = useState<'all' | 'buy' | 'sell'>('all')
  const [closing, setClosing] = useState<string | null>(null)

  const filtered = trades.filter((t) =>
    filter === 'all' ? true : t.direction === filter
  )

  const totalPnl = trades.reduce((s, t) => s + t.floating_dollar, 0)

  const handleClose = async (tradeId: string) => {
    if (!confirm('Close this position?')) return
    setClosing(tradeId)
    try {
      await api.post(`/api/positions/${tradeId}/close`, { reason: 'Manual close from dashboard V3' })
      await fetchPortfolio()
    } catch {
      alert('Failed to close position')
    } finally {
      setClosing(null)
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Active Positions</h2>
          <p className="text-sm text-gray-500">{trades.length} open trades</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Filter */}
          <div className="flex items-center gap-1 bg-dark-surface rounded-lg p-0.5">
            {(['all', 'buy', 'sell'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={clsx(
                  'px-3 py-1.5 rounded-md text-xs font-medium transition-all capitalize',
                  filter === f ? 'bg-primary text-white' : 'text-gray-500 hover:text-gray-300'
                )}
              >
                {f}
              </button>
            ))}
          </div>
          {/* Total P/L badge */}
          <div className={clsx(
            'px-3 py-1.5 rounded-lg text-sm font-semibold',
            totalPnl >= 0 ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'
          )}>
            {formatCurrency(totalPnl)}
          </div>
        </div>
      </div>

      {/* Trade cards */}
      {filtered.length === 0 ? (
        <div className="glass-card p-12 text-center text-gray-600">
          <span className="material-symbols-outlined text-4xl mb-2 block">inbox</span>
          No active positions
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map((t) => {
            // SL-to-TP progress
            const slDist = Math.abs(t.entry_price - t.original_sl)
            const tpDist = Math.abs(t.take_profit_1 - t.entry_price)
            const totalDist = slDist + tpDist
            const currentDist = t.current_price
              ? (t.direction === 'buy'
                  ? (t.current_price - t.original_sl)
                  : (t.original_sl - t.current_price))
              : slDist
            const progress = totalDist > 0 ? Math.max(0, Math.min(100, (currentDist / totalDist) * 100)) : 50

            return (
              <div key={t.trade_id} className="glass-card p-4 space-y-3">
                {/* Top row */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={clsx(
                      'w-10 h-10 rounded-xl flex items-center justify-center',
                      t.direction === 'buy' ? 'bg-success/10' : 'bg-danger/10'
                    )}>
                      <span className={clsx(
                        'material-symbols-outlined text-xl',
                        t.direction === 'buy' ? 'text-success' : 'text-danger'
                      )}>
                        {t.direction === 'buy' ? 'trending_up' : 'trending_down'}
                      </span>
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-white">{t.pair}</h3>
                      <div className="flex items-center gap-1.5">
                        <DirectionBadge direction={t.direction} />
                        <span className="text-[10px] text-gray-500">{t.strategy_mode}</span>
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className={clsx(
                      'text-lg font-bold',
                      t.floating_dollar >= 0 ? 'text-success' : 'text-danger'
                    )}>
                      {formatCurrency(t.floating_dollar)}
                    </p>
                    <p className={clsx(
                      'text-xs',
                      t.floating_pips >= 0 ? 'text-success/70' : 'text-danger/70'
                    )}>
                      {formatPips(t.floating_pips)}
                    </p>
                  </div>
                </div>

                {/* Badges */}
                <div className="flex items-center gap-1.5">
                  {t.sl_moved_to_be && (
                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-success/10 text-success">BE</span>
                  )}
                  {t.partial_closed && (
                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-info/10 text-info">
                      Partial {Math.round((1 - t.remaining_size) * 100)}%
                    </span>
                  )}
                  {t.trail_active && (
                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-warning/10 text-warning">Trailing</span>
                  )}
                </div>

                {/* Price grid */}
                <div className="grid grid-cols-4 gap-2 text-center">
                  {([
                    ['Entry', formatPrice(t.entry_price, t.pair)],
                    ['Current', t.current_price ? formatPrice(t.current_price, t.pair) : '—'],
                    ['SL', formatPrice(t.stop_loss, t.pair)],
                    ['TP', formatPrice(t.take_profit_1, t.pair)],
                  ] as const).map(([label, value]) => (
                    <div key={label} className="bg-dark-bg/40 rounded-lg p-2">
                      <p className="text-[10px] text-gray-500 mb-0.5">{label}</p>
                      <p className="text-xs font-mono text-gray-200">{value}</p>
                    </div>
                  ))}
                </div>

                {/* SL→TP Progress */}
                <div>
                  <div className="flex items-center justify-between text-[10px] text-gray-500 mb-1">
                    <span>SL</span>
                    <span>Entry</span>
                    <span>TP</span>
                  </div>
                  <div className="h-2 rounded-full bg-dark-border overflow-hidden relative">
                    <div
                      className={clsx(
                        'h-full rounded-full transition-all duration-500',
                        progress > 60 ? 'bg-success' : progress > 40 ? 'bg-primary' : 'bg-danger'
                      )}
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 pt-1">
                  <button className="flex-1 flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-dark-bg/60 text-gray-400 text-xs font-medium hover:bg-dark-bg transition">
                    <span className="material-symbols-outlined text-sm">candlestick_chart</span>
                    View Chart
                  </button>
                  <button
                    onClick={() => handleClose(t.trade_id)}
                    disabled={closing === t.trade_id}
                    className="flex-1 flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-danger/10 text-danger text-xs font-medium hover:bg-danger/20 transition disabled:opacity-50"
                  >
                    <span className="material-symbols-outlined text-sm">close</span>
                    {closing === t.trade_id ? 'Closing...' : 'Close Trade'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Pending Setups */}
      {pending.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-white mb-3">Pending Setups</h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {pending.map((p, i) => (
              <div key={i} className="glass-card p-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-white">{p.pair}</span>
                    <DirectionBadge direction={p.direction} />
                  </div>
                  <span className="text-[10px] text-gray-500">
                    Score {p.score}/14
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div className="bg-dark-bg/40 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">Entry Zone</p>
                    <p className="text-xs font-mono text-gray-200">
                      {formatPrice(p.recommended_entry, p.pair)}
                    </p>
                  </div>
                  <div className="bg-dark-bg/40 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">SL</p>
                    <p className="text-xs font-mono text-danger">{formatPrice(p.stop_loss, p.pair)}</p>
                  </div>
                  <div className="bg-dark-bg/40 rounded-lg p-2">
                    <p className="text-[10px] text-gray-500">TP</p>
                    <p className="text-xs font-mono text-success">{formatPrice(p.take_profit_1, p.pair)}</p>
                  </div>
                </div>
                <p className="text-[10px] text-gray-500 mt-2">
                  {p.strategy_mode} • Expires {formatTimeAgo(p.expiry_at)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
