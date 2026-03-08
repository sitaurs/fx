# Dashboard V3 — Analysis & Implementation Plan

> **Date**: 2026-03-08
> **Scope**: Complete dashboard overhaul — Web + Electron desktop app
> **Design Reference**: `design/` folder (16 HTML mockups: 8 desktop + 8 mobile)

---

## PART A: GAP ANALYSIS — Current System vs Design

### A1. Current State Summary

| Aspect | Current |
|---|---|
| **Frontend** | Single-file SPA (`index.html` + `app.js`, 838 + 713 lines) |
| **Framework** | Alpine.js 3.x (CDN), Tailwind CDN, Chart.js 4 CDN |
| **Pages** | 1 page with tab-based sections (positions, pending, radar, events) |
| **Theme** | Light mode only (cyan #ECFEFF glass morphism) |
| **Navigation** | None — everything in one scrollable page |
| **Auth** | API key header only (X-API-Key), no login page |
| **Mobile** | Partially responsive but not mobile-first, no bottom nav |
| **Electron** | Not implemented |
| **Backend API** | 17 endpoints in `dashboard/backend/main.py` + mounted in `main.py` |
| **WebSocket** | ✅ Implemented — real-time portfolio/trade/analysis updates |
| **Charts** | 1 equity curve (Chart.js line) |

### A2. Design Requirements (from 16 mockups)

| Page | Desktop Design | Mobile Design | Status |
|---|---|---|---|
| **Login** | `desktop_login.html` | `mobile_login.html` | ❌ NOT EXIST |
| **Dashboard** | `desktop_dashboard.html` | `mobile_dashboard.html` | 🔄 PARTIAL — exists but different layout |
| **Chart** | `desktop_chart.html` | `mobile_chart.html` | ❌ NOT EXIST |
| **Positions** | `desktop_positions.html` | `mobile_positions.html` | 🔄 PARTIAL — tab within current SPA |
| **History** | `desktop_history.html` | `mobile_history.html` | 🔄 PARTIAL — trade journal tab exists |
| **AI Radar** | `desktop_ai_radar.html` | `mobile_ai_radar.html` | 🔄 PARTIAL — radar tab exists |
| **Analytics** | `desktop_analytics.html` | `mobile_analytics.html` | ❌ NOT EXIST |
| **Settings** | `desktop_settings.html` | `mobile_settings.html` | 🔄 PARTIAL — config panel exists |

### A3. Feature Gap Detail

#### 🔴 FULLY NEW — Must Build from Scratch

| Feature | Design | Backend Impact |
|---|---|---|
| **Login page** | Glassmorphism login with email/password, remember me, forgot password | Need auth system (JWT/session), user model, password hashing |
| **Chart page** | Live candlestick chart with TradingView-style overlays (TP/SL/Entry lines, pending orders, BOS/FVG zones), price axis, time axis, indicator strip (EMA/RSI/ATR toggle) | Need OHLCV streaming endpoint, WebSocket candle push, indicator calculation endpoint |
| **Analytics page** | Performance overview (cumulative P/L + daily returns combo chart), win/loss donut, strategy performance table, pair performance bars, stat cards (win rate ring, avg RR, P/L, profit factor), date range filtering | Need analytics aggregation endpoints (by strategy, by pair, by date range) |
| **Correlation risk matrix** | Visual matrix showing pair correlations (high/low/inverse) with lock/warning icons | Need pair correlation calculation endpoint |
| **Electron shell** | Desktop app wrapping the web dashboard | Electron main/preload/renderer setup, build pipeline |
| **Dark/Light mode toggle** | All designs use dark theme with `dark:` Tailwind classes | Theme state management + localStorage persistence |

#### 🟡 MAJOR REDESIGN — Exists but Heavily Different

| Feature | Current | Design | Delta |
|---|---|---|---|
| **Navigation** | None (single page) | Desktop: sidebar with 6 nav items + settings bottom. Mobile: 5-tab bottom nav | Full routing system needed |
| **Dashboard** | All-in-one with 5 KPI cards + tabs | 4 KPI cards (Balance, Equity, Daily DD, Total DD) + equity curve chart (with 1W/1M/YTD/ALL filter) + mini positions list + AI radar grid + events feed | Redesign layout, add equity chart time filters |
| **Positions** | Expandable cards in a tab | Full page with 3 trade cards (BUY/SELL indicators, strategy, BE/trail/partial badges, SL→TP progress bar, "View Chart" + "Close Trade" buttons) + pending setups section with cancel | Dedicated page with richer trade cards |
| **AI Radar** | Sorted list in tab | Full page with stats row (last scan, session signals, API usage), "High Probability Setups" card grid (3-col), confluence checklist per card, entry/SL/TP levels, RR badge, "View Setup" button, correlation risk matrix | Major expansion — almost entirely new |
| **History** | Last 10 trades in sidebar | Full page with 4 stat cards (total trades, win rate, total P/L, avg RR), full data table (date, pair, dir, entry/exit, pips, P/L, RR, score, reason), expandable row with AI post-mortem + chart thumbnail, pagination, search, filters (All/Win/Loss/BE), date picker, CSV export | Complete overhaul — needs new data table, search, pagination |
| **Settings** | Config form in sidebar panel | Full page with grouped sections: Trading Mode (radio cards: None/Cent/Extreme), risk slider, Risk Management toggles, AI Config, Connections status, Danger Zone (emergency stop) | Redesign into dedicated page with sections |
| **Status indicators** | Navbar dots for OANDA/Gemini | Header with "System Status: Running" pill + "Extreme Mode" pill + notification bell + schedule button | Richer status display |

#### 🟢 EXISTING — Reusable with Minor Changes

| Feature | Notes |
|---|---|
| **WebSocket infrastructure** | Keep as-is — already has broadcast, reconnect, token auth |
| **Portfolio API** | Keep `/api/portfolio` — data model mostly sufficient |
| **Trades API** | Keep `/api/trades` — add search/filter params |
| **Analysis API** | Keep `/api/analysis/live` — sufficient for radar |
| **Events API** | Keep `/api/events` — sufficient |
| **Equity API** | Keep `/api/portfolio/equity` — add date range filter |
| **Config API** | Keep `/api/system/config` (GET/PATCH) — extend with new fields |
| **Manual close** | Keep `/api/positions/{trade_id}/close` |
| **Health check** | Keep `/api/health` |

---

## PART B: BACKEND API GAPS

### B1. New Endpoints Needed

| # | Method | Endpoint | Purpose | Priority |
|---|---|---|---|---|
| 1 | `POST` | `/api/auth/login` | Authenticate user, return JWT | P0 |
| 2 | `POST` | `/api/auth/refresh` | Refresh JWT token | P0 |
| 3 | `GET` | `/api/auth/me` | Get current user profile | P0 |
| 4 | `GET` | `/api/analytics/summary` | Win rate, avg RR, total P/L, profit factor (with date range) | P1 |
| 5 | `GET` | `/api/analytics/performance` | Daily returns array for chart (cumulative + daily) | P1 |
| 6 | `GET` | `/api/analytics/by-strategy` | Strategy performance breakdown table | P1 |
| 7 | `GET` | `/api/analytics/by-pair` | Pair performance breakdown | P1 |
| 8 | `GET` | `/api/analytics/winloss` | Win/loss/BE distribution for donut chart | P1 |
| 9 | `GET` | `/api/market/candles/{pair}` | OHLCV data for chart page (proxy to OANDA) | P1 |
| 10 | `GET` | `/api/market/price/{pair}` | Current price tick for chart page | P2 |
| 11 | `GET` | `/api/correlation/matrix` | Pair correlation matrix data | P2 |
| 12 | `GET` | `/api/notifications` | User notification list | P2 |
| 13 | `POST` | `/api/scan/force` | Force immediate scan (from radar page "Force Scan Now" button) | P1 |

### B2. Existing Endpoints to Modify

| Endpoint | Change |
|---|---|
| `GET /api/trades` | Add `search`, `result_filter` (win/loss/be), `date_from`, `date_to` query params |
| `GET /api/portfolio/equity` | Add `period` query param (1w/1m/ytd/all) |
| `GET /api/system/config` | Extend schema for new settings (AI model selection, connection statuses, etc.) |

### B3. Database Schema Changes

| Table | Change |
|---|---|
| **users** (NEW) | `id`, `username`, `email`, `password_hash`, `role`, `created_at` |
| **sessions** (NEW) | `id`, `user_id`, `token`, `expires_at`, `created_at` |
| **notifications** (NEW) | `id`, `user_id`, `type`, `title`, `message`, `read`, `created_at` |
| **trades** (MODIFY) | Add index on `result`, `pair`, `strategy_mode`, `closed_at` for analytics queries |

---

## PART C: FRONTEND ARCHITECTURE DECISION

### C1. Current Tech: Alpine.js + CDN Tailwind (Zero Build)

**Pros**: Simple, no build step, fast prototype  
**Cons**: Not scalable for 8 pages, no routing, no component reuse, no code splitting, CDN Tailwind is large

### C2. Recommended Tech Stack for V3

| Layer | Technology | Justification |
|---|---|---|
| **Framework** | **React 18 + TypeScript** | Component model needed for 8 pages, type safety, huge ecosystem, Electron-compatible |
| **Routing** | **React Router v6** | Client-side routing for 8 pages, nested layouts |
| **State** | **Zustand** | Lightweight (0.3kb), no boilerplate, perfect for WebSocket state |
| **CSS** | **Tailwind CSS 3** (build-time) | Already in designs, but proper build instead of CDN |
| **Charts** | **Lightweight Charts** (TradingView) + **Recharts** | LW Charts for candlestick/chart page, Recharts for analytics/equity |
| **Icons** | **Material Symbols Outlined** | Already in all 16 designs |
| **Fonts** | **Inter** | Already in all designs |
| **HTTP** | **Axios** or **fetch** with interceptors | JWT auto-refresh, error handling |
| **WebSocket** | Native WebSocket with reconnect | Keep existing protocol |
| **Build** | **Vite** | Fast HMR, React plugin, Electron plugin available |
| **Electron** | **electron-vite** or **Vite + Electron Forge** | Same Vite config for web + desktop |

### C3. Page & Component Map

```
src/
├── main.tsx                    # React entry point
├── App.tsx                     # Router wrapper + auth guard
├── stores/
│   ├── authStore.ts            # JWT token, user, login/logout
│   ├── portfolioStore.ts       # Balance, equity, drawdown, active trades
│   ├── systemStore.ts          # System status, config
│   ├── tradeStore.ts           # Closed trades, pagination, filters
│   ├── analysisStore.ts        # Live analyses (radar data)
│   ├── analyticsStore.ts       # Analytics aggregations
│   └── wsStore.ts              # WebSocket connection, reconnect
├── hooks/
│   ├── useWebSocket.ts         # WS hook with auto-reconnect
│   ├── useAuth.ts              # Auth guard hook
│   └── usePolling.ts           # REST polling fallback
├── pages/
│   ├── LoginPage.tsx           # NEW
│   ├── DashboardPage.tsx       # REDESIGN
│   ├── ChartPage.tsx           # NEW
│   ├── PositionsPage.tsx       # REDESIGN
│   ├── HistoryPage.tsx         # REDESIGN
│   ├── AIRadarPage.tsx         # REDESIGN
│   ├── AnalyticsPage.tsx       # NEW
│   └── SettingsPage.tsx        # REDESIGN
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx         # Desktop sidebar (264px)
│   │   ├── BottomNav.tsx       # Mobile bottom nav (5 tabs)
│   │   ├── Header.tsx          # Page header with status pills
│   │   └── AppLayout.tsx       # Responsive layout wrapper
│   ├── dashboard/
│   │   ├── KPICard.tsx         # Balance, Equity, Daily DD, Total DD
│   │   ├── EquityCurve.tsx     # Recharts line chart (1W/1M/YTD/ALL)
│   │   ├── MiniPositionList.tsx
│   │   ├── RadarGrid.tsx       # 2x3 pair scan grid
│   │   └── EventFeed.tsx       # Recent events timeline
│   ├── positions/
│   │   ├── TradeCard.tsx       # Active trade card with progress bar
│   │   ├── PendingSetupCard.tsx
│   │   └── FilterBar.tsx       # All/BUY/SELL filter + P/L badge
│   ├── chart/
│   │   ├── CandlestickChart.tsx  # TradingView Lightweight Charts
│   │   ├── PairSelector.tsx
│   │   ├── TimeframeBar.tsx
│   │   ├── IndicatorStrip.tsx
│   │   ├── TradeOverlay.tsx    # Entry/SL/TP lines on chart
│   │   └── RightPanel.tsx      # Active trade + AI analysis + pending
│   ├── radar/
│   │   ├── SetupCard.tsx       # Confidence score, confluence checklist
│   │   ├── CorrelationMatrix.tsx
│   │   └── StatsRow.tsx        # Last scan, signals, API usage
│   ├── history/
│   │   ├── TradeTable.tsx      # Full data table with sort/search
│   │   ├── TradeRow.tsx        # Expandable row
│   │   ├── PostMortemPanel.tsx  # AI post-mortem + chart thumbnail
│   │   ├── StatsCards.tsx      # Total trades, win rate, P/L, avg RR
│   │   └── Pagination.tsx
│   ├── analytics/
│   │   ├── MetricCard.tsx      # Win rate ring, RR, P/L, profit factor
│   │   ├── PerformanceChart.tsx  # Cumulative + daily returns combo
│   │   ├── WinLossDonut.tsx    # Donut chart
│   │   ├── StrategyTable.tsx   # Strategy breakdown table
│   │   └── PairBars.tsx        # Pair performance horizontal bars
│   ├── settings/
│   │   ├── TradingModeSection.tsx  # Radio cards + risk slider
│   │   ├── RiskSection.tsx
│   │   ├── AIConfigSection.tsx
│   │   ├── ConnectionsSection.tsx
│   │   └── DangerZone.tsx      # Emergency stop
│   └── shared/
│       ├── StatusPill.tsx      # "Running" / "Extreme Mode" pills
│       ├── ScoreBar.tsx        # Gradient progress bar with label
│       ├── DirectionBadge.tsx  # BUY (green) / SELL (red) badge
│       └── ThemeToggle.tsx     # Dark/Light mode switch
├── lib/
│   ├── api.ts                  # Axios instance with JWT interceptor
│   ├── constants.ts            # API URLs, colors, enums
│   └── formatters.ts           # Currency, pips, percentage formatters
├── styles/
│   └── globals.css             # Tailwind base + custom utilities
├── electron/                   # Electron-specific files
│   ├── main.ts                 # Electron main process
│   ├── preload.ts              # Preload script
│   └── electron-builder.config.js
├── index.html                  # Vite HTML entry
├── vite.config.ts
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```

---

## PART D: IMPLEMENTATION PLAN

### Phase 0: Foundation (2-3 days)

| # | Task | Details |
|---|---|---|
| 0.1 | **Init Vite + React + TS project** | `npm create vite@latest dashboard-v3 -- --template react-ts` |
| 0.2 | **Install dependencies** | tailwindcss, react-router, zustand, axios, recharts, lightweight-charts, @electron/vite |
| 0.3 | **Configure Tailwind** | Match design token system (colors: primary, surface-dark, border-dark, success, danger, warning, info, purple) |
| 0.4 | **Create AppLayout** | Responsive layout: sidebar (desktop ≥1024px) + bottom nav (mobile <1024px) |
| 0.5 | **Setup routing** | React Router with 8 routes + layout wrapper |
| 0.6 | **Setup Zustand stores** | Skeleton stores for portfolio, system, trades, analyses |
| 0.7 | **Setup API client** | Axios instance with base URL, JWT interceptor, error handling |
| 0.8 | **WebSocket hook** | Port existing WS logic to React hook with auto-reconnect |
| 0.9 | **Theme system** | Dark/light mode with localStorage + system preference |

### Phase 1: Auth & Login (1-2 days)

| # | Task | Details |
|---|---|---|
| 1.1 | **Backend: User model + migration** | SQLite `users` table, password hashing (bcrypt) |
| 1.2 | **Backend: Auth endpoints** | `POST /api/auth/login`, `POST /api/auth/refresh`, `GET /api/auth/me` |
| 1.3 | **Backend: JWT middleware** | Protect all `/api/*` endpoints (except `/api/auth/login` and `/api/health`) |
| 1.4 | **Frontend: LoginPage** | Match `desktop_login.html` / `mobile_login.html` design |
| 1.5 | **Frontend: Auth store + guard** | JWT storage, auto-refresh, redirect to login when expired |

### Phase 2: Dashboard Page (2-3 days)

| # | Task | Details |
|---|---|---|
| 2.1 | **KPI Cards** | Balance (+% change), Equity (floating P/L), Daily Drawdown (progress bar), Total Drawdown (progress bar) |
| 2.2 | **Equity Curve chart** | Recharts line chart with gradient fill, HWM dashed line, time filters (1W/1M/YTD/ALL) |
| 2.3 | **Active Positions mini list** | 3-item summary with pair, direction, P/L |
| 2.4 | **AI Scan Radar grid** | 2x3 grid with pair name + score percentage + direction |
| 2.5 | **Recent Events feed** | Timeline with colored dots (blue/green/yellow) |
| 2.6 | **Backend: Extend equity endpoint** | Add `period` query param for 1W/1M/YTD/ALL filtering |

### Phase 3: Positions Page (2 days)

| # | Task | Details |
|---|---|---|
| 3.1 | **Active trade cards** | Direction icon, pair, strategy, BE/Trail/Partial badges, P/L, pips |
| 3.2 | **Price detail grid** | Entry, Current, SL, TP in 4-column grid |
| 3.3 | **SL→TP progress bar** | Visual progress from SL to TP |
| 3.4 | **Action buttons** | "View Chart" (link to chart page), "Close Trade" (with confirmation) |
| 3.5 | **Pending setups section** | Buy Limit / Sell Limit cards with cancel button |
| 3.6 | **Filter bar** | All/BUY/SELL toggle + Total P/L badge |

### Phase 4: Chart Page (3-4 days)

| # | Task | Details |
|---|---|---|
| 4.1 | **TradingView Lightweight Charts** | Candlestick chart with OANDA data, responsive |
| 4.2 | **Pair selector** | Tab-based pair switcher (EURUSD/GBPUSD/XAUUSD + add) |
| 4.3 | **Timeframe bar** | M1/M5/M15/H1/H4/D1 selector |
| 4.4 | **Trade overlay lines** | Entry (blue dashed), SL (red dashed), TP1/TP2 (green dashed), pending (yellow dashed), live price (green solid) |
| 4.5 | **Indicator strip** | EMA/RSI/ATR toggle buttons at bottom |
| 4.6 | **Right panel (desktop)** | Active trade card + AI Analysis section + pending setups |
| 4.7 | **Backend: Market candles endpoint** | `GET /api/market/candles/{pair}?timeframe=M15&count=300` — proxy to OANDA |
| 4.8 | **WebSocket: Price streaming** | Push candlestick updates to chart in real-time |

### Phase 5: AI Radar Page (2 days)

| # | Task | Details |
|---|---|---|
| 5.1 | **Stats row** | Last scan time, session signals count, API usage bar |
| 5.2 | **Setup cards grid** | 3-column grid with pair, timeframe, direction badge, confidence score bar (gradient), status badge (TRIGGERED/WATCHING/DEVELOPING) |
| 5.3 | **Confluence checklist** | Per-card checklist (BOS, Liquidity Sweep, FVG Tap, etc.) |
| 5.4 | **Entry/SL/TP grid** | Per-card price levels with RR badge |
| 5.5 | **Correlation risk matrix** | Pair correlation cards (high/low/inverse) |
| 5.6 | **Force Scan button** | Trigger immediate scan via `POST /api/scan/force` |
| 5.7 | **Backend: Force scan endpoint** | `POST /api/scan/force` — queue immediate scan for next pairs |

### Phase 6: History Page (2-3 days)

| # | Task | Details |
|---|---|---|
| 6.1 | **Stats cards row** | Total trades, win rate, total P/L, avg RR — each with delta indicator |
| 6.2 | **Full data table** | Sortable columns: date, pair, dir, entry/exit, pips, P/L, RR, score, reason |
| 6.3 | **Expandable rows** | AI post-mortem panel + chart thumbnail placeholder |
| 6.4 | **Search + filters** | Search trades by pair, filter by result (All/Win/Loss/BE), date range picker |
| 6.5 | **Pagination** | Standard page navigation |
| 6.6 | **CSV export** | Download filtered trades as CSV |
| 6.7 | **Backend: Enhance /api/trades** | Add search, result_filter, date_from, date_to, sort_by queries |

### Phase 7: Analytics Page (2-3 days)

| # | Task | Details |
|---|---|---|
| 7.1 | **Metric cards** | Win rate (with SVG ring), Avg RR, Total P/L, Profit Factor |
| 7.2 | **Performance chart** | Cumulative P/L line + daily return bars combo (Recharts) |
| 7.3 | **Win/Loss donut** | Donut chart with center text (Recharts PieChart) |
| 7.4 | **Strategy table** | Strategy name, trades, win rate, net P/L — sortable |
| 7.5 | **Pair performance bars** | Horizontal bar chart per pair |
| 7.6 | **Date range filter** | 7D / This Month / All Time toggle |
| 7.7 | **Download report** | PDF or CSV analytics export |
| 7.8 | **Backend: Analytics endpoints** | 5 new endpoints (summary, performance, by-strategy, by-pair, winloss) |

### Phase 8: Settings Page (1-2 days)

| # | Task | Details |
|---|---|---|
| 8.1 | **Trading Mode section** | Radio card selector (None/Cent/Extreme), risk slider |
| 8.2 | **Risk Management** | Drawdown Guard toggle, DD limit steppers |
| 8.3 | **AI Configuration** | Model selection, auto-revalidation toggle + interval |
| 8.4 | **Connections** | OANDA/MT5/WhatsApp status with green/yellow dots |
| 8.5 | **Danger Zone** | Emergency stop button (red, full-width) |
| 8.6 | **Save Changes** | Persist via `PATCH /api/system/config` |

### Phase 9: Mobile Responsive (2-3 days)

| # | Task | Details |
|---|---|---|
| 9.1 | **Bottom navigation** | 5-tab fixed bottom nav (Dashboard, Chart, Positions, AI Radar, More) |
| 9.2 | **Mobile Dashboard** | Gradient balance card, drawdown bars, quick action circles, horizontal scroll signals |
| 9.3 | **Mobile Positions** | Swipe-to-close trade cards, compact layout |
| 9.4 | **Mobile Chart** | Full-height chart, horizontal timeframe/indicator scrollers |
| 9.5 | **Mobile Radar** | Vertical signal cards, collapsible correlation accordion |
| 9.6 | **Mobile History** | Horizontal stat chips, expandable trade cards |
| 9.7 | **Mobile Analytics** | 2x2 metric grid, responsive charts |
| 9.8 | **Mobile Settings** | iOS-style grouped lists, stepper controls, toggle switches |

### Phase 10: Electron Desktop App (2-3 days)

| # | Task | Details |
|---|---|---|
| 10.1 | **Electron main process** | Window management, menu bar, tray icon |
| 10.2 | **Electron preload** | Secure bridge for system APIs |
| 10.3 | **Vite Electron plugin** | Single build pipeline for web + desktop |
| 10.4 | **Auto-update** | electron-updater for GitHub releases |
| 10.5 | **Native notifications** | System tray notifications for trade events |
| 10.6 | **Build & package** | Windows installer (.exe), macOS (.dmg), Linux (.AppImage) |

### Phase 11: Polish & Deploy (1-2 days)

| # | Task | Details |
|---|---|---|
| 11.1 | **Integration testing** | All pages + API + WebSocket E2E |
| 11.2 | **Performance** | Code splitting, lazy routes, image optimization |
| 11.3 | **Error boundaries** | React error boundaries per page |
| 11.4 | **Deploy web** | Build → deploy to VPS2, update Nginx config |
| 11.5 | **Deploy Electron** | Build installers, publish GitHub release |

---

## PART E: TIMELINE ESTIMATE

| Phase | Duration | Dependencies |
|---|---|---|
| Phase 0: Foundation | 2-3 days | — |
| Phase 1: Auth & Login | 1-2 days | Phase 0 |
| Phase 2: Dashboard | 2-3 days | Phase 0, Phase 1 |
| Phase 3: Positions | 2 days | Phase 0 |
| Phase 4: Chart | 3-4 days | Phase 0, Backend candle endpoint |
| Phase 5: AI Radar | 2 days | Phase 0 |
| Phase 6: History | 2-3 days | Phase 0, Backend search |
| Phase 7: Analytics | 2-3 days | Phase 0, Backend analytics endpoints |
| Phase 8: Settings | 1-2 days | Phase 0 |
| Phase 9: Mobile | 2-3 days | Phase 2-8 |
| Phase 10: Electron | 2-3 days | Phase 0-8 |
| Phase 11: Polish | 1-2 days | All phases |
| **TOTAL** | **~22-30 days** | |

> **Note**: Phases 2-8 can be partially parallelized. The critical path is: Phase 0 → Phase 1 → Phase 2 + (3-8 parallel) → Phase 9 → Phase 10 → Phase 11.

---

## PART F: BACKEND WORK SUMMARY

### New Files to Create

| File | Purpose |
|---|---|
| `dashboard/backend/auth.py` | JWT helpers (create/verify token), password hashing |
| `dashboard/backend/routes/auth.py` | `/api/auth/*` endpoints |
| `dashboard/backend/routes/analytics.py` | `/api/analytics/*` endpoints |
| `dashboard/backend/routes/market.py` | `/api/market/*` endpoints (candle proxy) |
| `dashboard/backend/routes/scan.py` | `/api/scan/force` endpoint |
| `database/migrations/002_users_auth.py` | Users + sessions table migration |
| `database/migrations/003_notifications.py` | Notifications table migration |

### Existing Files to Modify

| File | Changes |
|---|---|
| `dashboard/backend/main.py` | Add JWT middleware, mount new route modules, add CORS for Electron |
| `database/models.py` | Add User, Session, Notification models |
| `database/repository.py` | Add analytics query methods (trades by strategy, by pair, by date range) |
| `main.py` | Mount new routes, add force-scan hook |

---

## PART G: RISK & CONSIDERATIONS

1. **Auth complexity**: Simple JWT + bcrypt is sufficient — no need for OAuth2 since it's a single-user system. Could use a simpler token-based approach (API key in cookie).

2. **Chart page performance**: TradingView Lightweight Charts handles large datasets well, but real-time updates need throttled WebSocket pushes (max 1 update/second).

3. **Electron anti-pattern**: Electron apps are large (~120MB). For mobile, a PWA might be better than Electron+Capacitor. The user said "APK dari Electron" — Electron doesn't make Android APKs natively. Options:
   - **Electron** → Windows/Mac/Linux desktop only
   - **Capacitor/Tauri** → Android/iOS APK from web code
   - **Electron + Capacitor** → Both desktop + mobile from same React codebase
   - **Recommendation**: Use **Capacitor** for the APK and **Electron** for desktop

4. **Migration strategy**: Old dashboard stays working during V3 development. V3 served on a different path (e.g., `/v3/`) until ready, then swap.

5. **WebSocket protocol**: Keep existing message format — V3 frontend just consumes the same protocol.
