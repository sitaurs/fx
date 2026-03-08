import clsx from 'clsx'

interface StatusPillProps {
  label: string
  variant: 'success' | 'danger' | 'warning' | 'info' | 'neutral'
  pulse?: boolean
  className?: string
}

const variants = {
  success: 'bg-success/10 text-success',
  danger: 'bg-danger/10 text-danger',
  warning: 'bg-warning/10 text-warning',
  info: 'bg-info/10 text-info',
  neutral: 'bg-gray-500/10 text-gray-400',
}

export default function StatusPill({ label, variant, pulse, className }: StatusPillProps) {
  return (
    <span className={clsx(
      'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium',
      variants[variant],
      className,
    )}>
      <span className={clsx(
        'w-1.5 h-1.5 rounded-full',
        variant === 'success' && 'bg-success',
        variant === 'danger' && 'bg-danger',
        variant === 'warning' && 'bg-warning',
        variant === 'info' && 'bg-info',
        variant === 'neutral' && 'bg-gray-500',
        pulse && 'animate-pulse',
      )} />
      {label}
    </span>
  )
}
