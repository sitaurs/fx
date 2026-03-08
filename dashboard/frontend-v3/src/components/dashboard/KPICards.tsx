import { usePortfolioStore } from '../../stores/portfolioStore'
import { formatDollar, formatPercent, formatPercentSigned } from '../../lib/formatters'
import clsx from 'clsx'

export default function KPICards() {
  const p = usePortfolioStore((s) => s.portfolio)

  const balanceChange = p.initial_balance > 0
    ? (p.balance - p.initial_balance) / p.initial_balance
    : 0

  const cards = [
    {
      label: 'Balance',
      value: formatDollar(p.balance),
      sub: formatPercentSigned(balanceChange),
      subColor: balanceChange >= 0 ? 'text-success' : 'text-danger',
      icon: 'account_balance_wallet',
      iconBg: 'bg-primary/10 text-primary',
    },
    {
      label: 'Equity',
      value: formatDollar(p.effective_balance),
      sub: `Float: ${p.floating_pnl >= 0 ? '+' : ''}$${p.floating_pnl.toFixed(2)}`,
      subColor: p.floating_pnl >= 0 ? 'text-success' : 'text-danger',
      icon: 'trending_up',
      iconBg: 'bg-success/10 text-success',
    },
    {
      label: 'Daily Drawdown',
      value: formatPercent(p.daily_drawdown_pct),
      sub: `Max ${formatPercent(p.max_daily_drawdown)}`,
      subColor: p.daily_drawdown_pct > p.max_daily_drawdown * 0.8 ? 'text-danger' : 'text-gray-500',
      icon: 'show_chart',
      iconBg: p.daily_drawdown_pct > p.max_daily_drawdown * 0.8 ? 'bg-danger/10 text-danger' : 'bg-warning/10 text-warning',
      progress: p.max_daily_drawdown > 0 ? p.daily_drawdown_pct / p.max_daily_drawdown : 0,
    },
    {
      label: 'Total Drawdown',
      value: formatPercent(p.total_drawdown_pct),
      sub: `Max ${formatPercent(p.max_total_drawdown)}`,
      subColor: p.total_drawdown_pct > p.max_total_drawdown * 0.8 ? 'text-danger' : 'text-gray-500',
      icon: 'speed',
      iconBg: p.total_drawdown_pct > p.max_total_drawdown * 0.8 ? 'bg-danger/10 text-danger' : 'bg-info/10 text-info',
      progress: p.max_total_drawdown > 0 ? p.total_drawdown_pct / p.max_total_drawdown : 0,
    },
  ]

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 lg:gap-4">
      {cards.map((card) => (
        <div key={card.label} className="glass-card p-4">
          <div className="flex items-start justify-between mb-3">
            <div className={clsx('w-10 h-10 rounded-xl flex items-center justify-center', card.iconBg)}>
              <span className="material-symbols-outlined text-xl">{card.icon}</span>
            </div>
          </div>
          <p className="text-xs text-gray-500 mb-1">{card.label}</p>
          <p className="text-xl font-bold text-white">{card.value}</p>
          <p className={clsx('text-xs mt-1', card.subColor)}>{card.sub}</p>

          {/* DD progress bar */}
          {card.progress !== undefined && (
            <div className="dd-bar mt-2">
              <div
                className={clsx(
                  'h-full rounded-full transition-all duration-500',
                  card.progress > 0.8 ? 'bg-danger' : card.progress > 0.5 ? 'bg-warning' : 'bg-primary'
                )}
                style={{ width: `${Math.min(100, card.progress * 100)}%` }}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
