import clsx from 'clsx'

interface ScoreBarProps {
  score: number
  max?: number
  label?: string
  showValue?: boolean
  className?: string
}

export default function ScoreBar({ score, max = 14, label, showValue = true, className }: ScoreBarProps) {
  const pct = Math.min(100, Math.max(0, (score / max) * 100))

  const colorClass =
    pct >= 80 ? 'from-primary to-success'
    : pct >= 60 ? 'from-success to-success'
    : pct >= 40 ? 'from-warning to-warning'
    : 'from-danger to-danger'

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      {label && <span className="text-xs text-gray-400 min-w-[60px]">{label}</span>}
      <div className="flex-1 h-2 rounded-full bg-dark-border overflow-hidden">
        <div
          className={clsx('h-full rounded-full bg-gradient-to-r transition-all duration-500', colorClass)}
          style={{ width: `${pct}%` }}
        />
      </div>
      {showValue && (
        <span className="text-xs font-mono text-gray-300 min-w-[40px] text-right">
          {score}/{max}
        </span>
      )}
    </div>
  )
}
