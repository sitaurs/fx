import { Link, useNavigate } from 'react-router-dom'
import { BOTTOM_NAV_ITEMS } from '../../lib/constants'
import { useState } from 'react'
import clsx from 'clsx'

interface BottomNavProps {
  currentPath: string
}

export default function BottomNav({ currentPath }: BottomNavProps) {
  const navigate = useNavigate()
  const [showMore, setShowMore] = useState(false)

  const moreItems = [
    { path: '/history', label: 'History', icon: 'history' },
    { path: '/analytics', label: 'Analytics', icon: 'analytics' },
    { path: '/settings', label: 'Settings', icon: 'settings' },
  ]

  return (
    <>
      {/* More menu overlay */}
      {showMore && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={() => setShowMore(false)}
        >
          <div
            className="absolute bottom-16 right-4 bg-dark-surface border border-dark-border rounded-xl shadow-2xl p-2 min-w-[160px]"
            onClick={(e) => e.stopPropagation()}
          >
            {moreItems.map((item) => (
              <button
                key={item.path}
                onClick={() => {
                  navigate(item.path)
                  setShowMore(false)
                }}
                className={clsx(
                  'flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm transition-all',
                  currentPath === item.path
                    ? 'text-primary bg-primary/10'
                    : 'text-gray-300 hover:bg-dark-hover'
                )}
              >
                <span className="material-symbols-outlined text-xl">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Bottom nav bar */}
      <nav className="fixed bottom-0 left-0 right-0 z-30 lg:hidden bg-dark-surface border-t border-dark-border safe-bottom">
        <div className="flex items-center justify-around h-16 max-w-lg mx-auto">
          {BOTTOM_NAV_ITEMS.map((item) => {
            const isMore = item.path === '/more'
            const active = isMore
              ? moreItems.some((m) => m.path === currentPath)
              : currentPath === item.path

            if (isMore) {
              return (
                <button
                  key="more"
                  onClick={() => setShowMore(!showMore)}
                  className={clsx(
                    'flex flex-col items-center gap-0.5 py-1 px-3 transition-colors',
                    active || showMore ? 'text-primary' : 'text-gray-500'
                  )}
                >
                  <span className="material-symbols-outlined text-xl">{item.icon}</span>
                  <span className="text-[10px] font-medium">{item.label}</span>
                </button>
              )
            }

            return (
              <Link
                key={item.path}
                to={item.path}
                className={clsx(
                  'flex flex-col items-center gap-0.5 py-1 px-3 transition-colors',
                  active ? 'text-primary' : 'text-gray-500'
                )}
              >
                <span className={clsx('material-symbols-outlined text-xl', active && 'filled')}>
                  {item.icon}
                </span>
                <span className="text-[10px] font-medium">{item.label}</span>
                {active && <div className="w-4 h-0.5 rounded-full bg-primary mt-0.5" />}
              </Link>
            )
          })}
        </div>
      </nav>
    </>
  )
}
