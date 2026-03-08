import { useEffect, useState } from 'react'
import { useSystemStore } from '../stores/systemStore'
import { usePortfolioStore } from '../stores/portfolioStore'
import clsx from 'clsx'

export default function SettingsPage() {
  const { config, fetchConfig, updateConfig } = useSystemStore()
  const { status, fetchStatus } = useSystemStore()
  const portfolio = usePortfolioStore((s) => s.portfolio)
  const unhalt = useSystemStore((s) => s.unhalt)

  const [form, setForm] = useState({
    challenge_mode: 'none',
    risk_per_trade: 0.01,
    drawdown_guard_enabled: true,
    max_daily_drawdown: 0.05,
    max_total_drawdown: 0.15,
    max_concurrent_trades: 2,
    active_revalidation_enabled: true,
    active_revalidation_interval_minutes: 90,
  })
  const [saving, setSaving] = useState(false)

  useEffect(() => { fetchConfig(); fetchStatus() }, [fetchConfig, fetchStatus])

  useEffect(() => {
    if (config) {
      setForm({
        challenge_mode: (config.challenge_mode as string) || 'none',
        risk_per_trade: (config.risk_per_trade as number) || 0.01,
        drawdown_guard_enabled: config.drawdown_guard_enabled ?? true,
        max_daily_drawdown: (config.max_daily_drawdown as number) || 0.05,
        max_total_drawdown: (config.max_total_drawdown as number) || 0.15,
        max_concurrent_trades: (config.max_concurrent_trades as number) || 2,
        active_revalidation_enabled: config.active_revalidation_enabled ?? true,
        active_revalidation_interval_minutes: (config.active_revalidation_interval_minutes as number) || 90,
      })
    }
  }, [config])

  const handleSave = async () => {
    setSaving(true)
    await updateConfig(form)
    setSaving(false)
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 animate-fade-in">
      {/* Header */}
      <div>
        <h2 className="text-lg font-bold text-white">Settings</h2>
        <p className="text-sm text-gray-500">Configure trading and system parameters</p>
      </div>

      {/* Trading Mode */}
      <div className="glass-card p-5 space-y-4">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <span className="material-symbols-outlined text-lg text-primary">tune</span>
          Trading Mode
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {([
            { value: 'none', label: 'Standard', desc: 'Normal trading rules', icon: 'shield', color: 'primary' },
            { value: 'cent', label: 'Cent Account', desc: 'Conservative risk limits', icon: 'savings', color: 'warning' },
            { value: 'extreme', label: 'Extreme', desc: 'Aggressive challenge mode', icon: 'bolt', color: 'danger' },
          ] as const).map((mode) => (
            <button
              key={mode.value}
              onClick={() => setForm({ ...form, challenge_mode: mode.value })}
              className={clsx(
                'p-4 rounded-xl border text-left transition-all',
                form.challenge_mode === mode.value
                  ? `border-${mode.color} bg-${mode.color}/10`
                  : 'border-dark-border bg-dark-bg/40 hover:bg-dark-bg/60'
              )}
            >
              <span className={clsx(
                'material-symbols-outlined text-xl mb-2 block',
                form.challenge_mode === mode.value ? `text-${mode.color}` : 'text-gray-500'
              )}>
                {mode.icon}
              </span>
              <p className={clsx(
                'text-sm font-semibold',
                form.challenge_mode === mode.value ? 'text-white' : 'text-gray-400'
              )}>
                {mode.label}
              </p>
              <p className="text-[10px] text-gray-500 mt-0.5">{mode.desc}</p>
            </button>
          ))}
        </div>
      </div>

      {/* Risk Management */}
      <div className="glass-card p-5 space-y-4">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <span className="material-symbols-outlined text-lg text-warning">security</span>
          Risk Management
        </h3>

        {/* Risk per trade slider */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs text-gray-400">Risk Per Trade</label>
            <span className="text-xs font-mono text-white">{(form.risk_per_trade * 100).toFixed(1)}%</span>
          </div>
          <input
            type="range"
            min="0.005"
            max="0.05"
            step="0.005"
            value={form.risk_per_trade}
            onChange={(e) => setForm({ ...form, risk_per_trade: parseFloat(e.target.value) })}
            className="w-full accent-primary"
          />
          <div className="flex justify-between text-[10px] text-gray-600 mt-1">
            <span>0.5%</span>
            <span>5%</span>
          </div>
        </div>

        {/* DD Guard toggle */}
        <div className="flex items-center justify-between p-3 bg-dark-bg/40 rounded-lg">
          <div>
            <p className="text-sm text-white">Drawdown Guard</p>
            <p className="text-[10px] text-gray-500">Auto-halt on excessive drawdown</p>
          </div>
          <button
            onClick={() => setForm({ ...form, drawdown_guard_enabled: !form.drawdown_guard_enabled })}
            className={clsx(
              'w-11 h-6 rounded-full transition-all relative',
              form.drawdown_guard_enabled ? 'bg-primary' : 'bg-dark-border'
            )}
          >
            <div className={clsx(
              'w-5 h-5 rounded-full bg-white absolute top-0.5 transition-all',
              form.drawdown_guard_enabled ? 'left-[22px]' : 'left-0.5'
            )} />
          </button>
        </div>

        {/* DD steppers */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Max Daily DD</label>
            <div className="flex items-center bg-dark-bg/40 rounded-lg">
              <button onClick={() => setForm({ ...form, max_daily_drawdown: Math.max(0.01, form.max_daily_drawdown - 0.01) })}
                className="px-3 py-2 text-gray-400 hover:text-white">−</button>
              <span className="flex-1 text-center text-sm font-mono text-white">
                {(form.max_daily_drawdown * 100).toFixed(0)}%
              </span>
              <button onClick={() => setForm({ ...form, max_daily_drawdown: Math.min(0.20, form.max_daily_drawdown + 0.01) })}
                className="px-3 py-2 text-gray-400 hover:text-white">+</button>
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Max Total DD</label>
            <div className="flex items-center bg-dark-bg/40 rounded-lg">
              <button onClick={() => setForm({ ...form, max_total_drawdown: Math.max(0.05, form.max_total_drawdown - 0.01) })}
                className="px-3 py-2 text-gray-400 hover:text-white">−</button>
              <span className="flex-1 text-center text-sm font-mono text-white">
                {(form.max_total_drawdown * 100).toFixed(0)}%
              </span>
              <button onClick={() => setForm({ ...form, max_total_drawdown: Math.min(0.30, form.max_total_drawdown + 0.01) })}
                className="px-3 py-2 text-gray-400 hover:text-white">+</button>
            </div>
          </div>
        </div>

        {/* Max concurrent */}
        <div>
          <label className="text-xs text-gray-400 mb-1 block">Max Concurrent Trades</label>
          <div className="flex items-center bg-dark-bg/40 rounded-lg w-fit">
            <button onClick={() => setForm({ ...form, max_concurrent_trades: Math.max(1, form.max_concurrent_trades - 1) })}
              className="px-3 py-2 text-gray-400 hover:text-white">−</button>
            <span className="px-4 text-sm font-mono text-white">{form.max_concurrent_trades}</span>
            <button onClick={() => setForm({ ...form, max_concurrent_trades: Math.min(6, form.max_concurrent_trades + 1) })}
              className="px-3 py-2 text-gray-400 hover:text-white">+</button>
          </div>
        </div>
      </div>

      {/* AI Configuration */}
      <div className="glass-card p-5 space-y-4">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <span className="material-symbols-outlined text-lg text-info">psychology</span>
          AI Configuration
        </h3>

        <div className="flex items-center justify-between p-3 bg-dark-bg/40 rounded-lg">
          <div>
            <p className="text-sm text-white">Active Revalidation</p>
            <p className="text-[10px] text-gray-500">AI re-checks open positions periodically</p>
          </div>
          <button
            onClick={() => setForm({ ...form, active_revalidation_enabled: !form.active_revalidation_enabled })}
            className={clsx(
              'w-11 h-6 rounded-full transition-all relative',
              form.active_revalidation_enabled ? 'bg-primary' : 'bg-dark-border'
            )}
          >
            <div className={clsx(
              'w-5 h-5 rounded-full bg-white absolute top-0.5 transition-all',
              form.active_revalidation_enabled ? 'left-[22px]' : 'left-0.5'
            )} />
          </button>
        </div>

        {form.active_revalidation_enabled && (
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Revalidation Interval</label>
            <div className="flex items-center bg-dark-bg/40 rounded-lg w-fit">
              <button onClick={() => setForm({ ...form, active_revalidation_interval_minutes: Math.max(15, form.active_revalidation_interval_minutes - 15) })}
                className="px-3 py-2 text-gray-400 hover:text-white">−</button>
              <span className="px-4 text-sm font-mono text-white">{form.active_revalidation_interval_minutes}min</span>
              <button onClick={() => setForm({ ...form, active_revalidation_interval_minutes: Math.min(360, form.active_revalidation_interval_minutes + 15) })}
                className="px-3 py-2 text-gray-400 hover:text-white">+</button>
            </div>
          </div>
        )}
      </div>

      {/* Connections */}
      <div className="glass-card p-5 space-y-4">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <span className="material-symbols-outlined text-lg text-success">link</span>
          Connections
        </h3>
        <div className="space-y-2">
          <ConnectionRow label="OANDA v20" connected={status?.api_status?.oanda ?? false} />
          <ConnectionRow label="Gemini AI" connected={status?.api_status?.gemini ?? false} />
          <ConnectionRow label="WhatsApp" connected={false} />
        </div>
      </div>

      {/* Danger Zone */}
      <div className="glass-card p-5 border-danger/30 space-y-4">
        <h3 className="text-sm font-semibold text-danger flex items-center gap-2">
          <span className="material-symbols-outlined text-lg">warning</span>
          Danger Zone
        </h3>
        <button
          onClick={async () => {
            if (confirm('EMERGENCY STOP: This will halt ALL trading immediately. Continue?')) {
              await unhalt()
            }
          }}
          className="w-full py-3 bg-danger/10 text-danger font-semibold rounded-lg border border-danger/30 hover:bg-danger/20 transition flex items-center justify-center gap-2"
        >
          <span className="material-symbols-outlined text-lg">power_settings_new</span>
          Emergency Stop
        </button>
      </div>

      {/* Save button */}
      <div className="flex justify-end pb-8">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2.5 bg-primary hover:bg-primary-dark text-white font-semibold rounded-lg transition-all disabled:opacity-50 flex items-center gap-2"
        >
          {saving ? (
            <span className="material-symbols-outlined animate-spin text-lg">progress_activity</span>
          ) : (
            <span className="material-symbols-outlined text-lg">save</span>
          )}
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}

function ConnectionRow({ label, connected }: { label: string; connected: boolean }) {
  return (
    <div className="flex items-center justify-between p-3 bg-dark-bg/40 rounded-lg">
      <div className="flex items-center gap-2">
        <div className={clsx('w-2 h-2 rounded-full', connected ? 'bg-success' : 'bg-gray-500')} />
        <span className="text-sm text-white">{label}</span>
      </div>
      <span className={clsx('text-xs', connected ? 'text-success' : 'text-gray-500')}>
        {connected ? 'Connected' : 'Disconnected'}
      </span>
    </div>
  )
}
