import clsx from 'clsx'

interface DirectionBadgeProps {
  direction: string
  size?: 'sm' | 'md'
}

export default function DirectionBadge({ direction, size = 'sm' }: DirectionBadgeProps) {
  const isBuy = direction?.toLowerCase() === 'buy'
  return (
    <span className={clsx(
      'inline-flex items-center font-semibold uppercase rounded',
      isBuy ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger',
      size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-3 py-1 text-xs',
    )}>
      <span className="material-symbols-outlined mr-0.5" style={{ fontSize: size === 'sm' ? 12 : 14 }}>
        {isBuy ? 'trending_up' : 'trending_down'}
      </span>
      {direction?.toUpperCase() || '—'}
    </span>
  )
}
