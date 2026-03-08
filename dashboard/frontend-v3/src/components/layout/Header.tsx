import { usePortfolioStore } from '../../stores/portfolioStore'
import { useSystemStore } from '../../stores/systemStore'
import clsx from 'clsx'

export default function Header() {
  const portfolio = usePortfolioStore((s) => s.portfolio)
  const status = useSystemStore((s) => s.status)
  const unhalt = useSystemStore((s) => s.unhalt)

  return (
    <>
      {/* Halt banner */}
      {portfolio.is_halted && (
        <div className="bg-danger text-white px-4 py-2 text-center text-sm font-semibold animate-slide-down flex items-center justify-center gap-2">
          <span className="material-symbols-outlined text-base">warning</span>
          TRADING HALTED — {portfolio.halt_reason || 'Drawdown limit breached'}
          <button
            onClick={unhalt}
            className="ml-4 px-3 py-0.5 bg-white/20 rounded text-xs hover:bg-white/30 transition"
          >
            Reset
          </button>
        </div>
      )}

      {/* Header bar */}
      <header className="h-14 bg-dark-surface border-b border-dark-border flex items-center justify-between px-4 lg:px-6 shrink-0">
        {/* Left: Mobile logo */}
        <div className="flex items-center gap-3 lg:hidden">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
            <span className="text-white font-bold text-sm">₣</span>
          </div>
          <span className="text-sm font-bold text-white">AI Forex</span>
        </div>

        {/* Left: Desktop page breadcrumb area (empty — pages set title themselves) */}
        <div className="hidden lg:block" />

        {/* Right: Status pills */}
        <div className="flex items-center gap-2">
          {/* System status */}
          <div className={clsx(
            'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium',
            portfolio.is_halted
              ? 'bg-danger/10 text-danger'
              : 'bg-success/10 text-success'
          )}>
            <div className={clsx(
              'w-1.5 h-1.5 rounded-full',
              portfolio.is_halted ? 'bg-danger' : 'bg-success'
            )} />
            {portfolio.is_halted ? 'Halted' : 'Running'}
          </div>

          {/* Mode pill */}
          {portfolio.mode && portfolio.mode !== 'none' && (
            <div className={clsx(
              'px-2.5 py-1 rounded-full text-xs font-medium',
              portfolio.challenge_mode === 'extreme'
                ? 'bg-danger/10 text-danger'
                : portfolio.challenge_mode === 'cent'
                ? 'bg-warning/10 text-warning'
                : 'bg-primary/10 text-primary'
            )}>
              {portfolio.challenge_mode === 'extreme' ? 'Extreme Mode' :
               portfolio.challenge_mode === 'cent' ? 'Cent Mode' :
               portfolio.mode.toUpperCase()}
            </div>
          )}

          {/* OANDA status */}
          <div className="hidden md:flex items-center gap-1 px-2 py-1">
            <div className={clsx(
              'w-1.5 h-1.5 rounded-full',
              status?.api_status?.oanda ? 'bg-success' : 'bg-gray-500'
            )} />
            <span className="text-[10px] text-gray-500">OANDA</span>
          </div>

          {/* Gemini status */}
          <div className="hidden md:flex items-center gap-1 px-2 py-1">
            <div className={clsx(
              'w-1.5 h-1.5 rounded-full',
              status?.api_status?.gemini ? 'bg-success' : 'bg-gray-500'
            )} />
            <span className="text-[10px] text-gray-500">Gemini</span>
          </div>
        </div>
      </header>
    </>
  )
}
