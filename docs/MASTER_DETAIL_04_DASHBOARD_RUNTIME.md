# MASTER DETAIL 04 - DASHBOARD & RUNTIME INTERFACE (Evidence-Only)

Tanggal: 2026-03-01 (Asia/Bangkok)

## 1. Arsitektur Dashboard

1. Dashboard backend adalah FastAPI app tunggal yang juga dipakai oleh `main.py`.  
   Bukti: `main.py:50-61`, `dashboard/backend/main.py:134-138`.
2. Frontend statis di-mount dari `dashboard/frontend`, root `/` melayani `index.html`.  
   Bukti: `dashboard/backend/main.py:151-163`, `dashboard/backend/main.py:170-178`.

## 2. In-Memory Store & Batasan

Store utama:
- `_analyses` (dict)
- `_trades` (list)
- `_equity_history` (list)
- `_events` (deque maxlen=200)

Batas:
- `_MAX_ANALYSES = 50`
- `_MAX_TRADES = 500`
- `_equity_history` dipangkas max 500 titik

Bukti: `dashboard/backend/main.py:70-86`, `dashboard/backend/main.py:704-705`, `dashboard/backend/main.py:709-735`.

## 3. Endpoint Runtime Penting

1. Portfolio:
- `GET /api/portfolio`
- `GET /api/portfolio/equity`

2. System:
- `GET /api/system/status`
- `GET /api/system/config`
- `PATCH /api/system/config`
- `POST /api/system/config/reset-default`
- `POST /api/system/balance/set`
- `POST /api/system/unhalt`

3. Analysis & trade:
- `GET /api/analysis/live`
- `GET /api/analysis/{pair}`
- `GET /api/trades`
- `GET /api/trades/{trade_id}`
- `POST /api/positions/{trade_id}/close`

4. Realtime:
- `WS /ws`

Bukti route: `dashboard/backend/main.py:243`, `392`, `414`, `447`, `455`, `468`, `478`, `499`, `520`, `528`, `541`, `557`, `578`, `656`.

## 4. Kalkulasi KPI Portfolio

`GET /api/portfolio` menghitung:
1. Per-trade floating pips berdasarkan `current_price` vs `entry_price` + point pair.  
2. Per-trade floating dollar via `lc.trade_floating_pnl()` (memperhitungkan remaining size/risk amount).  
3. Effective balance = `balance + total_floating`.
4. Daily drawdown dan total drawdown dihitung dari effective balance.

Bukti: `dashboard/backend/main.py:275-349`.

Output juga memuat field runtime control:
- challenge mode
- sizing mode
- fixed lot
- drawdown guard
- active revalidation settings
- runtime_config snapshot

Bukti: `dashboard/backend/main.py:368-375`.

## 5. Runtime Config dari Dashboard

Frontend form -> backend:
1. `saveSystemConfig()` kirim challenge/risk/revalidation config ke `PATCH /api/system/config`.  
   Bukti: `dashboard/frontend/app.js:180-203`.
2. `setCustomBalance()` kirim balance + reset flags ke `POST /api/system/balance/set`.  
   Bukti: `dashboard/frontend/app.js:209-233`.
3. `resetSystemDefaults()` panggil `POST /api/system/config/reset-default`.  
   Bukti: `dashboard/frontend/app.js:236-250`.

Backend memproses payload ke lifecycle `update_runtime_config()`/`reset_runtime_config()` lalu record equity point.  
Bukti: `dashboard/backend/main.py:455-491`.

## 6. Manual Close dari Dashboard

1. UI menampilkan tombol `Close` per active trade.  
   Bukti: `dashboard/frontend/index.html:267-272`.
2. Klik tombol memanggil `manualCloseTrade()` -> `POST /api/positions/{trade_id}/close`.  
   Bukti: `dashboard/frontend/app.js:302-335`.
3. Backend endpoint memanggil `lifecycle.manual_close_trade()` dan return hasil close.  
   Bukti: `dashboard/backend/main.py:578-605`.
4. Lifecycle manual close masuk pipeline `_close_trade()` (DB, post-mortem, dashboard push, WA notif, state save).  
   Bukti: `agent/production_lifecycle.py:629-661`, `agent/production_lifecycle.py:1141-1327`.

## 7. Equity Curve & Sinkronisasi Data

1. Backend `record_equity_point()` menyimpan timestamp+balance+hwm setiap trigger update penting.  
   Bukti: `dashboard/backend/main.py:692-705`.
2. Frontend `updateEquityChart()` melakukan sort by timestamp sebelum render chart (mencegah urutan titik acak).  
   Bukti: `dashboard/frontend/app.js:518-528`.
3. Canvas equity chart ada di UI kanan (`#equityChart`).  
   Bukti: `dashboard/frontend/index.html:503-509`.

## 8. WebSocket & Event Broadcast

1. WS endpoint `/ws` dengan optional token auth (`DASHBOARD_WS_TOKEN`).  
   Bukti: `dashboard/backend/main.py:656-664`, `config/settings.py:239`.
2. Broadcast event utama:
   - `ANALYSIS_UPDATE`
   - `STATE_CHANGE`
   - `TRADE_CLOSED`
   - `PORTFOLIO_UPDATE`

Bukti: `dashboard/backend/main.py:708-739`, `dashboard/backend/main.py:742-784`.

## 9. Challenge/Risk Controls di UI

UI menyediakan:
- challenge mode selector (`none/extreme/cent`)
- sizing mode
- fixed lot
- revalidation interval
- toggle drawdown guard
- toggle active revalidation
- set balance + reset flags
- tombol default reset

Bukti: `dashboard/frontend/index.html:419-499`.

