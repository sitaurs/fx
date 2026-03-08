import { Link } from 'react-router-dom'
import { useAnalysisStore } from '../../stores/analysisStore'
import clsx from 'clsx'

export default function RadarGrid() {
  const analyses = useAnalysisStore((s) => s.analyses)

  return (
    <div className="glass-card p-4 lg:p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-white">AI Scan Radar</h3>
          <p className="text-xs text-gray-500 mt-0.5">{analyses.length} pairs scanned</p>
        </div>
        <Link
          to="/radar"
          className="text-xs text-primary hover:text-primary-light transition flex items-center gap-1"
        >
          View Details
          <span className="material-symbols-outlined text-sm">arrow_forward</span>
        </Link>
      </div>

      {analyses.length === 0 ? (
        <div className="py-8 text-center text-gray-600 text-sm">
          <span className="material-symbols-outlined text-3xl mb-2 block">radar</span>
          No scan data yet
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {analyses.map((a) => {
            const pct = Math.round(a.confidence * 100) || Math.round((a.score / 14) * 100)
            const dir = a.direction?.toLowerCase()
            return (
              <div
                key={a.pair}
                className="p-3 rounded-lg bg-dark-bg/40 hover:bg-dark-bg/60 transition cursor-pointer"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-white">{a.pair}</span>
                  <span className={clsx(
                    'text-[10px] font-semibold px-1.5 py-0.5 rounded',
                    dir === 'buy' ? 'bg-success/10 text-success' :
                    dir === 'sell' ? 'bg-danger/10 text-danger' :
                    'bg-gray-500/10 text-gray-400'
                  )}>
                    {dir === 'buy' ? 'BUY' : dir === 'sell' ? 'SELL' : 'NEUTRAL'}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-dark-border overflow-hidden">
                  <div
                    className={clsx(
                      'h-full rounded-full transition-all duration-500',
                      pct >= 70 ? 'bg-success' : pct >= 50 ? 'bg-warning' : 'bg-gray-500'
                    )}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <p className="text-[10px] text-gray-500 mt-1">{pct}%</p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
