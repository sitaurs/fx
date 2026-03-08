import { Link } from 'react-router-dom'
import { usePortfolioStore } from '../../stores/portfolioStore'
import DirectionBadge from '../shared/DirectionBadge'
import { formatCurrency, formatPips } from '../../lib/formatters'
import clsx from 'clsx'

export default function MiniPositionList() {
  const trades = usePortfolioStore((s) => s.portfolio.active_trades)

  return (
    <div className="glass-card p-4 lg:p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-white">Active Positions</h3>
          <p className="text-xs text-gray-500 mt-0.5">{trades.length} open</p>
        </div>
        <Link
          to="/positions"
          className="text-xs text-primary hover:text-primary-light transition flex items-center gap-1"
        >
          View All
          <span className="material-symbols-outlined text-sm">arrow_forward</span>
        </Link>
      </div>

      {trades.length === 0 ? (
        <div className="py-8 text-center text-gray-600 text-sm">
          <span className="material-symbols-outlined text-3xl mb-2 block">inbox</span>
          No active positions
        </div>
      ) : (
        <div className="space-y-3">
          {trades.slice(0, 5).map((t) => (
            <div
              key={t.trade_id}
              className="flex items-center justify-between p-3 rounded-lg bg-dark-bg/40 hover:bg-dark-bg/60 transition"
            >
              <div className="flex items-center gap-3">
                <div className={clsx(
                  'w-8 h-8 rounded-lg flex items-center justify-center',
                  t.direction === 'buy' ? 'bg-success/10' : 'bg-danger/10'
                )}>
                  <span className={clsx(
                    'material-symbols-outlined text-lg',
                    t.direction === 'buy' ? 'text-success' : 'text-danger'
                  )}>
                    {t.direction === 'buy' ? 'trending_up' : 'trending_down'}
                  </span>
                </div>
                <div>
                  <p className="text-sm font-semibold text-white">{t.pair}</p>
                  <DirectionBadge direction={t.direction} size="sm" />
                </div>
              </div>
              <div className="text-right">
                <p className={clsx(
                  'text-sm font-semibold',
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
          ))}
        </div>
      )}
    </div>
  )
}
