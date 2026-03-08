import { useEffect, useRef } from 'react'

/**
 * Poll a function at a given interval (ms).
 * Calls fn immediately on mount, then every `interval` ms.
 */
export function usePolling(fn: () => void, interval: number, enabled = true) {
  const savedFn = useRef(fn)

  useEffect(() => {
    savedFn.current = fn
  }, [fn])

  useEffect(() => {
    if (!enabled) return
    savedFn.current()
    const id = setInterval(() => savedFn.current(), interval)
    return () => clearInterval(id)
  }, [interval, enabled])
}
