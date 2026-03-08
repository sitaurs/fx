import { useState, useEffect, useRef, useCallback } from 'react'
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickData, Time } from 'lightweight-charts'
import { usePortfolioStore } from '../stores/portfolioStore'
import api from '../lib/api'
import clsx from 'clsx'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1', 'H4', 'D1'] as const
const DEFAULT_PAIRS = ['XAUUSD', 'EURUSD', 'GBPJPY', 'USDCHF', 'USDCAD', 'USDJPY']

export default function ChartPage() {
  const activeTrades = usePortfolioStore((s) => s.portfolio.active_trades)
  const [pair, setPair] = useState('XAUUSD')
  const [timeframe, setTimeframe] = useState('H1')
  const [candles, setCandles] = useState<CandlestickData<Time>[]>([])
  const [loading, setLoading] = useState(false)

  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // Fetch candles
  const fetchCandles = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.get(`/api/market/candles/${pair}`, {
        params: { timeframe, count: 200 },
      })
      const raw = Array.isArray(data) ? data : (data?.candles ?? [])
      if (raw.length > 0) {
        setCandles(raw.map((c: { time: number | string; open: number; high: number; low: number; close: number }) => ({
          time: (typeof c.time === 'number' ? c.time : parseInt(c.time)) as Time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        })))
      }
    } catch {
      // API might not be available yet — show empty chart
      setCandles([])
    } finally {
      setLoading(false)
    }
  }, [pair, timeframe])

  useEffect(() => { fetchCandles() }, [fetchCandles])

  // Init chart
  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0e17' },
        textColor: '#6b7280',
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(55, 65, 81, 0.3)' },
        horzLines: { color: 'rgba(55, 65, 81, 0.3)' },
      },
      crosshair: {
        vertLine: { color: 'rgba(14, 165, 233, 0.3)', width: 1, style: 2 },
        horzLine: { color: 'rgba(14, 165, 233, 0.3)', width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: '#374151',
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        secondsVisible: false,
      },
    })

    const series = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })

    chartRef.current = chart
    seriesRef.current = series

    const resize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        })
      }
    }
    window.addEventListener('resize', resize)
    resize()

    return () => {
      window.removeEventListener('resize', resize)
      chart.remove()
    }
  }, [])

  // Update candles
  useEffect(() => {
    if (seriesRef.current && candles.length > 0) {
      seriesRef.current.setData(candles)
      chartRef.current?.timeScale().fitContent()

      // Add trade level lines
      const trade = activeTrades.find((t) => t.pair === pair)
      if (trade) {
        const priceLinesData = [
          { price: trade.entry_price, color: '#0ea5e9', title: 'Entry', lineStyle: 2 },
          { price: trade.stop_loss, color: '#ef4444', title: 'SL', lineStyle: 2 },
          { price: trade.take_profit_1, color: '#10b981', title: 'TP1', lineStyle: 2 },
        ]
        if (trade.take_profit_2) {
          priceLinesData.push({ price: trade.take_profit_2, color: '#34d399', title: 'TP2', lineStyle: 2 })
        }
        priceLinesData.forEach((pl) => {
          seriesRef.current?.createPriceLine({
            price: pl.price,
            color: pl.color,
            lineWidth: 1,
            lineStyle: pl.lineStyle,
            axisLabelVisible: true,
            title: pl.title,
          })
        })
      }
    }
  }, [candles, activeTrades, pair])

  // Current active trade for this pair
  const activeTrade = activeTrades.find((t) => t.pair === pair)

  return (
    <div className="h-full flex flex-col gap-4 animate-fade-in">
      {/* Pair tabs + Timeframe */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        {/* Pair tabs */}
        <div className="flex items-center gap-1 overflow-x-auto pb-1">
          {DEFAULT_PAIRS.map((p) => (
            <button
              key={p}
              onClick={() => setPair(p)}
              className={clsx(
                'px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all',
                pair === p
                  ? 'bg-primary text-white'
                  : 'text-gray-500 hover:text-gray-300 bg-dark-surface'
              )}
            >
              {p}
            </button>
          ))}
        </div>

        {/* Timeframe buttons */}
        <div className="flex items-center gap-1 bg-dark-surface rounded-lg p-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={clsx(
                'px-2.5 py-1 rounded-md text-xs font-medium transition-all',
                timeframe === tf ? 'bg-primary text-white' : 'text-gray-500 hover:text-gray-300'
              )}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Chart + Right panel */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Chart */}
        <div className="flex-1 rounded-xl border border-dark-border overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-dark-bg/80 z-10">
              <span className="material-symbols-outlined animate-spin text-primary text-3xl">progress_activity</span>
            </div>
          )}
          <div ref={chartContainerRef} className="w-full h-full" />
        </div>

        {/* Right panel (desktop only) */}
        <div className="hidden xl:flex xl:flex-col xl:w-[280px] shrink-0 space-y-4">
          {/* Active trade for this pair */}
          {activeTrade ? (
            <div className="glass-card p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold text-white">Active Trade</h4>
                <span className={clsx(
                  'text-[10px] font-bold px-2 py-0.5 rounded',
                  activeTrade.direction === 'buy' ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'
                )}>
                  {activeTrade.direction.toUpperCase()}
                </span>
              </div>
              <div className={clsx(
                'text-xl font-bold',
                activeTrade.floating_dollar >= 0 ? 'text-success' : 'text-danger'
              )}>
                {activeTrade.floating_dollar >= 0 ? '+' : ''}{activeTrade.floating_dollar.toFixed(2)}
                <span className="text-xs ml-1">USD</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="bg-dark-bg/40 rounded p-1.5">
                  <span className="text-gray-500 block text-[10px]">Entry</span>
                  <span className="font-mono text-gray-200">{activeTrade.entry_price}</span>
                </div>
                <div className="bg-dark-bg/40 rounded p-1.5">
                  <span className="text-gray-500 block text-[10px]">Current</span>
                  <span className="font-mono text-gray-200">{activeTrade.current_price || '—'}</span>
                </div>
                <div className="bg-dark-bg/40 rounded p-1.5">
                  <span className="text-danger block text-[10px]">SL</span>
                  <span className="font-mono text-gray-200">{activeTrade.stop_loss}</span>
                </div>
                <div className="bg-dark-bg/40 rounded p-1.5">
                  <span className="text-success block text-[10px]">TP1</span>
                  <span className="font-mono text-gray-200">{activeTrade.take_profit_1}</span>
                </div>
              </div>
              <div className="flex items-center gap-1.5 text-[10px]">
                {activeTrade.sl_moved_to_be && <span className="px-1.5 py-0.5 rounded bg-success/10 text-success">BE</span>}
                {activeTrade.trail_active && <span className="px-1.5 py-0.5 rounded bg-warning/10 text-warning">Trail</span>}
                {activeTrade.partial_closed && <span className="px-1.5 py-0.5 rounded bg-info/10 text-info">Partial</span>}
              </div>
            </div>
          ) : (
            <div className="glass-card p-4 text-center text-gray-600 text-sm">
              <span className="material-symbols-outlined text-2xl mb-1 block">info</span>
              No active trade for {pair}
            </div>
          )}

          {/* Pair info */}
          <div className="glass-card p-4">
            <h4 className="text-xs font-semibold text-white mb-2">Pair Info</h4>
            <div className="text-xs text-gray-400 space-y-1">
              <div className="flex justify-between">
                <span>Pair</span>
                <span className="text-white">{pair}</span>
              </div>
              <div className="flex justify-between">
                <span>Timeframe</span>
                <span className="text-white">{timeframe}</span>
              </div>
              <div className="flex justify-between">
                <span>Candles</span>
                <span className="text-white">{candles.length}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
