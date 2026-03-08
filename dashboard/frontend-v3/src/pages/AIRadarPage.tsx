import { useEffect } from 'react'
import { useAnalysisStore } from '../stores/analysisStore'
import DirectionBadge from '../components/shared/DirectionBadge'
import ScoreBar from '../components/shared/ScoreBar'
import { CONFLUENCE_KEYS } from '../lib/constants'
import { formatPrice, formatTimeAgo } from '../lib/formatters'
import clsx from 'clsx'

export default function AIRadarPage() {
  const analyses = useAnalysisStore((s) => s.analyses)
  const lastScan = useAnalysisStore((s) => s.lastScan)
  const fetchAnalyses = useAnalysisStore((s) => s.fetchAnalyses)

  useEffect(() => { fetchAnalyses() }, [fetchAnalyses])

  // Sort by score descending
  const sorted = [...analyses].sort((a, b) => (b.score || 0) - (a.score || 0))

  // Separate by status
  const triggered = sorted.filter((a) => a.state === 'TRIGGERED' || a.state === 'ACTIVE')
  const watching = sorted.filter((a) => a.state === 'WATCHING' || a.state === 'VALIDATED')
  const developing = sorted.filter((a) => !['TRIGGERED', 'ACTIVE', 'WATCHING', 'VALIDATED', 'CLOSED', 'CANCELLED'].includes(a.state))

  const sessionSignals = analyses.filter((a) => a.direction).length

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">AI Scan Radar</h2>
          <p className="text-sm text-gray-500">Real-time market analysis</p>
        </div>
        <button
          onClick={() => fetchAnalyses()}
          className="px-4 py-2 bg-primary/10 text-primary rounded-lg text-sm font-medium hover:bg-primary/20 transition flex items-center gap-2"
        >
          <span className="material-symbols-outlined text-lg">refresh</span>
          Force Scan
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3">
        <div className="glass-card p-3">
          <p className="text-[10px] text-gray-500 mb-0.5">Last Scan</p>
          <p className="text-sm font-semibold text-white">{lastScan ? formatTimeAgo(lastScan) : '—'}</p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[10px] text-gray-500 mb-0.5">Session Signals</p>
          <p className="text-sm font-semibold text-white">{sessionSignals}</p>
        </div>
        <div className="glass-card p-3">
          <p className="text-[10px] text-gray-500 mb-0.5">Pairs Scanned</p>
          <p className="text-sm font-semibold text-white">{analyses.length}</p>
        </div>
      </div>

      {/* Setup sections */}
      {triggered.length > 0 && (
        <SetupSection title="Triggered / Active" items={triggered} statusColor="text-success" />
      )}
      {watching.length > 0 && (
        <SetupSection title="Watching" items={watching} statusColor="text-warning" />
      )}
      {developing.length > 0 && (
        <SetupSection title="Developing" items={developing} statusColor="text-gray-400" />
      )}

      {analyses.length === 0 && (
        <div className="glass-card p-12 text-center text-gray-600">
          <span className="material-symbols-outlined text-4xl mb-2 block">radar</span>
          No scan data available. Waiting for next analysis cycle.
        </div>
      )}
    </div>
  )
}

function SetupSection({ title, items, statusColor }: {
  title: string
  items: ReturnType<typeof useAnalysisStore.getState>['analyses']
  statusColor: string
}) {
  return (
    <div>
      <h3 className={clsx('text-sm font-semibold mb-3', statusColor)}>{title}</h3>
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
        {items.map((a) => (
          <SetupCard key={a.pair} analysis={a} />
        ))}
      </div>
    </div>
  )
}

function SetupCard({ analysis: a }: { analysis: ReturnType<typeof useAnalysisStore.getState>['analyses'][0] }) {
  const plan = a.plan?.primary_setup
  const score = plan?.confluence_score ?? a.score ?? 0
  const details = plan?.confluence_details ?? {}

  // Compute entry price from available fields
  const entryPrice = plan?.recommended_entry
    ?? plan?.entry_zone_mid
    ?? (plan?.entry_zone_low && plan?.entry_zone_high
      ? (plan.entry_zone_low + plan.entry_zone_high) / 2
      : undefined)

  // Risk:Reward from available fields
  const riskReward = plan?.risk_reward_ratio ?? plan?.risk_reward

  const statusBadge =
    a.state === 'TRIGGERED' || a.state === 'ACTIVE'
      ? { label: 'TRIGGERED', color: 'bg-success/10 text-success' }
      : a.state === 'WATCHING' || a.state === 'VALIDATED'
      ? { label: 'WATCHING', color: 'bg-warning/10 text-warning' }
      : { label: a.state, color: 'bg-gray-500/10 text-gray-400' }

  return (
    <div className="glass-card p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-bold text-white">{a.pair}</h4>
          {a.direction && <DirectionBadge direction={a.direction} />}
        </div>
        <span className={clsx('text-[10px] font-semibold px-2 py-0.5 rounded', statusBadge.color)}>
          {statusBadge.label}
        </span>
      </div>

      {/* Score bar */}
      <ScoreBar score={score} max={14} />

      {/* Confluence checklist */}
      {Object.keys(details).length > 0 ? (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          {Object.entries(CONFLUENCE_KEYS).map(([key, label]) => {
            const checked = details[key] ?? false
            return (
              <div key={key} className="flex items-center gap-1.5">
                <span className={clsx(
                  'material-symbols-outlined text-xs',
                  checked ? 'text-success filled' : 'text-gray-600'
                )}>
                  {checked ? 'check_circle' : 'radio_button_unchecked'}
                </span>
                <span className={clsx(
                  'text-[10px]',
                  checked ? 'text-gray-300' : 'text-gray-600'
                )}>
                  {label}
                </span>
              </div>
            )
          })}
        </div>
      ) : plan?.rationale ? (
        <p className="text-[11px] text-gray-400 leading-snug line-clamp-3">{plan.rationale}</p>
      ) : a.error ? (
        <p className="text-[11px] text-gray-500 italic">{a.error}</p>
      ) : null}

      {/* Price levels */}
      {plan && (
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="bg-dark-bg/40 rounded-lg p-2">
            <p className="text-[10px] text-gray-500">Entry</p>
            <p className="text-xs font-mono text-primary">
              {entryPrice != null ? formatPrice(entryPrice, a.pair) : '—'}
            </p>
          </div>
          <div className="bg-dark-bg/40 rounded-lg p-2">
            <p className="text-[10px] text-gray-500">SL</p>
            <p className="text-xs font-mono text-danger">
              {plan.stop_loss != null ? formatPrice(plan.stop_loss, a.pair) : '—'}
            </p>
          </div>
          <div className="bg-dark-bg/40 rounded-lg p-2">
            <p className="text-[10px] text-gray-500">TP1</p>
            <p className="text-xs font-mono text-success">
              {plan.take_profit_1 != null ? formatPrice(plan.take_profit_1, a.pair) : '—'}
            </p>
          </div>
        </div>
      )}

      {/* RR Badge */}
      {riskReward != null && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">Risk:Reward</span>
          <span className="text-xs font-semibold text-primary">1:{riskReward.toFixed(1)}</span>
        </div>
      )}
    </div>
  )
}
