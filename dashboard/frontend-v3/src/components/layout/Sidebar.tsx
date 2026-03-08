import { Link } from 'react-router-dom'
import { NAV_ITEMS } from '../../lib/constants'
import clsx from 'clsx'

interface SidebarProps {
  currentPath: string
}

export default function Sidebar({ currentPath }: SidebarProps) {
  return (
    <aside className="hidden lg:flex lg:flex-col w-[264px] bg-dark-surface border-r border-dark-border shrink-0">
      {/* Logo */}
      <div className="h-16 flex items-center gap-3 px-6 border-b border-dark-border">
        <div className="w-9 h-9 rounded-lg bg-primary flex items-center justify-center">
          <span className="text-white font-bold text-lg">₣</span>
        </div>
        <div>
          <h1 className="text-sm font-bold text-white leading-tight">AI Forex Agent</h1>
          <p className="text-[10px] text-gray-500">Powered by Gemini AI</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-3 space-y-1">
        <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider px-3 mb-2">
          Menu
        </p>
        {NAV_ITEMS.map((item) => {
          const active = currentPath === item.path
          return (
            <Link
              key={item.path}
              to={item.path}
              className={clsx(
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all',
                active
                  ? 'bg-primary/10 text-primary'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-dark-hover'
              )}
            >
              <span
                className={clsx(
                  'material-symbols-outlined text-xl',
                  active && 'filled'
                )}
              >
                {item.icon}
              </span>
              {item.label}
              {active && (
                <div className="ml-auto w-1.5 h-1.5 rounded-full bg-primary" />
              )}
            </Link>
          )
        })}
      </nav>

      {/* Bottom: Settings */}
      <div className="border-t border-dark-border p-3">
        <Link
          to="/settings"
          className={clsx(
            'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all',
            currentPath === '/settings'
              ? 'bg-primary/10 text-primary'
              : 'text-gray-400 hover:text-gray-200 hover:bg-dark-hover'
          )}
        >
          <span className="material-symbols-outlined text-xl">settings</span>
          Settings
        </Link>
      </div>
    </aside>
  )
}
