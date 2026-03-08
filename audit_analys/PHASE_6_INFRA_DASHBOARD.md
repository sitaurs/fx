# PHASE 6 AUDIT — Infrastructure & Dashboard

**Tanggal:** 2025-06-05  
**Cakupan:** 5 file, ~1,380 baris kode  
**Auditor:** AI Audit Agent

---

## File yang Diaudit

| # | File | Lines | Fungsi Utama |
|---|------|-------|--------------|
| 1 | `scheduler/runner.py` | ~160 | APScheduler cron-based scan scheduling |
| 2 | `notifier/handler.py` | ~220 | Central notification dispatcher |
| 3 | `notifier/whatsapp.py` | ~200 | WhatsApp REST client (circuit breaker + retry) |
| 4 | `notifier/templates.py` | ~200 | WhatsApp message formatting |
| 5 | `dashboard/backend/main.py` | ~600 | FastAPI dashboard backend (REST + WS) |

---

## Ringkasan Temuan

| Severity | Count | Keterangan |
|----------|-------|-----------|
| 🔴 CRITICAL | 1 | Tidak ada autentikasi pada admin REST API |
| 🟠 HIGH | 2 | Sync price fetch blocking event loop, private attribute mutation |
| 🟡 MEDIUM | 4 | Score hardcoded /15, duplicated portfolio logic, notification gaps |
| 🔵 LOW | 7 | Heavy import, template mismatch, minor improvements |
| 💀 DEAD CODE | 1 | `ALL_PAIRS` import di runner.py |
| 🔄 CONSISTENCY | 2 | Portfolio computation duplication, state handling gaps |

---

## Detail Temuan per File

---

### 1. `scheduler/runner.py` (~160 lines)

**Deskripsi:** APScheduler wrapper yang menjadwalkan scan sesuai session: Asian (06:00), London (13:30), Pre-NY (19:00), Wrapup (22:30), semua WIB.

#### Arsitektur
- `ScanScheduler` class wrapping `AsyncIOScheduler`
- Accepts `scan_fn`, `batch_fn`, `wrapup_fn` callables
- `_run_batch()` → tries batch_fn first, falls back to per-pair scan on failure

#### Temuan

**💀 DEAD-01: `ALL_PAIRS` imported tapi tidak digunakan**
```python
from config.settings import MVP_PAIRS, ALL_PAIRS  # ALL_PAIRS never used
```
- Hanya `MVP_PAIRS` yang digunakan sebagai default pairs.
- **Fix:** Hapus `ALL_PAIRS` import.

**✅ GOOD:**
- **Fault isolation:** batch_fn failure → graceful fallback ke per-pair scanning.
- **Per-pair isolation:** Satu pair gagal tidak block yang lain.
- **Mon-Fri filter:** `day_of_week="mon-fri"` mencegah scan weekend.
- **Replace existing:** `replace_existing=True` mencegah duplicate jobs.

---

### 2. `notifier/handler.py` (~220 lines)

**Deskripsi:** Central dispatch hub yang mengkonversi events ke WhatsApp messages. Receive events dari orchestrator/lifecycle, format, send.

#### Arsitektur
- `NotificationHandler` class dengan event methods
- Module-level singleton `notification_handler`
- Supports: triggered, cancelled, sl_moved, trade_closed, daily_end, error, trade_opened, pending_added/expired, drawdown_halt

#### Temuan

**🟡 M-01: `on_state_change()` hanya handle 2 dari 7 states**
```python
async def on_state_change(self, old_state, new_state, ...):
    if new_state == "TRIGGERED" and plan is not None:
        await self._send_triggered(plan, ohlcv)
    elif new_state == "CANCELLED":
        msg = format_cancelled_alert(pair, reason)
        await self._wa.send_message(msg)
    # Other transitions... extend here as needed.
```
- States WATCHING, APPROACHING, ACTIVE, CLOSED tidak mengirim notifikasi via `on_state_change`.
- `format_watching_update` exists di templates tapi TIDAK dipanggil di handler.
- **Impact:** User tidak dapat monitoring state transitions via WhatsApp (hanya TRIGGERED dan CANCELLED).
- **Fix:** Implementasi APPROACHING → alert "price mendekati zone", ACTIVE → opening confirmation.

**🔵 L-01: `pandas` import hanya untuk type hint**
```python
import pandas as pd  # Heavy import (~50MB)
# Used only in type hint: ohlcv: Optional[pd.DataFrame]
```
- **Impact:** Pandas sudah loaded elsewhere, tapi jika ini satu-satunya import point, menambah startup time.
- **Fix:** Use `from __future__ import annotations` (sudah ada) — type hint is string, pandas import bisa di-delay. Ganti ke `from typing import TYPE_CHECKING` pattern.

**✅ GOOD:**
- Chart fallback: jika generation gagal → text-only message. Graceful degradation.
- Chart cleanup: `os.remove(path)` setelah send.
- Comprehensive event coverage (8+ event types).

---

### 3. `notifier/whatsapp.py` (~200 lines)

**Deskripsi:** Async HTTP client untuk go-whatsapp-web-multidevice API. Circuit breaker + retry + connection pooling.

#### Arsitektur
- `CircuitBreaker`: CLOSED → OPEN (5 failures) → HALF_OPEN (60s recovery) → probe → CLOSED
- `WhatsAppNotifier`: shared `httpx.AsyncClient`, retry with exponential backoff
- Module-level singleton `wa_notifier`

#### Temuan

**🔵 L-02: Circuit breaker HALF_OPEN bisa accept multiple concurrent requests**
```python
def allow_request(self) -> bool:
    s = self.state
    if s == self.HALF_OPEN:
        return True  # Allow one probe... but actually allows ALL concurrent
```
- Saat HALF_OPEN, semua concurrent requests diizinkan (bukan hanya 1 probe).
- **Impact:** Low — WhatsApp API typically gets serial requests, not concurrent bursts.
- **Fix:** Add `_half_open_probe_sent` flag to only allow one probe.

**🔵 L-03: `_phone_jid()` recalculates setiap call**
```python
def _phone_jid(self) -> str:
    raw = (self.phone or "").strip()
    # ... computation setiap call
```
- **Impact:** Negligible performance overhead.
- **Fix:** Cache in `_jid` attribute di init.

**✅ GOOD:**
- **Circuit breaker pattern** — prevents flooding unresponsive API.
- **Retry with backoff** — 1s → 2s → 4s exponential.
- **Connection pooling** — shared `httpx.AsyncClient`.
- **Indonesia phone format** — 08xx → 628xx auto-conversion.
- **Basic auth support** — for go-whatsapp-web auth.

---

### 4. `notifier/templates.py` (~200 lines)

**Deskripsi:** WhatsApp message formatting templates. Bold/italic/emoji-formatted strings.

#### Temuan

**🟡 M-02: Score hardcoded "/15" tapi MAX_POSSIBLE_SCORE = 14**
```python
f"\U0001F4CA Score: {s.confluence_score}/15\n"
```
- `config/strategy_rules.py`: `MAX_POSSIBLE_SCORE = sum(v for v in SCORING_WEIGHTS.values() if v > 0)` = 3+2+3+2+2+1+1 = **14**.
- Template menampilkan score X/15 — off by 1.
- **Impact:** User sees incorrect max score di WhatsApp alert. Bisa membuat score terlihat lebih rendah dari semestinya.
- **Fix:**
  ```python
  from config.strategy_rules import MAX_POSSIBLE_SCORE
  f"Score: {s.confluence_score}/{MAX_POSSIBLE_SCORE}"
  ```

**🔵 L-04: `format_watching_update` exists tapi TIDAK dipanggil**
- Template sudah lengkap tapi handler.py tidak punya caller untuk state WATCHING/APPROACHING.
- **Impact:** Dead template — berfungsi tapi tidak digunakan.
- **Fix:** Wire up di `handler.on_state_change()` (terkait M-01).

**✅ GOOD:**
- Clean separation of formatting dari dispatch logic.
- WhatsApp markdown format (bold, italic, emoji) correctly used.
- Error alerting with message truncation (`error[:200]`).

---

### 5. `dashboard/backend/main.py` (~600 lines)

**Deskripsi:** FastAPI dashboard backend. REST API untuk portfolio, trades, system status, config management. WebSocket untuk real-time updates.

#### Arsitektur
- FastAPI app dengan CORS middleware
- In-memory stores: `_analyses`, `_trades`, `_equity_history`, `_events` (ring buffer)
- WebSocket `ConnectionManager` untuk broadcast
- Pydantic models: `SystemConfigPatch`, `BalanceSetRequest`, `ManualCloseRequest`
- Helper functions: `set_repo()`, `set_lifecycle()`, `push_*()`, `record_equity_point()`

#### Temuan

**🔴 C-01: Admin API endpoints TIDAK memiliki autentikasi**
```python
@app.patch("/api/system/config")          # ← NO AUTH
async def patch_system_config(payload):
    ...  # Can change: mode, challenge_mode, position_sizing, drawdown limits

@app.post("/api/system/balance/set")      # ← NO AUTH
async def set_balance(payload):
    ...  # Can set arbitrary balance

@app.post("/api/system/unhalt")           # ← NO AUTH
async def unhalt_system():
    ...  # Can unhalt trading

@app.post("/api/positions/{id}/close")    # ← NO AUTH
async def manual_close_position():
    ...  # Can force-close any position
```
- Dashboard accessible at `fx.ecosystech.me` — publicly accessible.
- **ANYONE** can:
  1. Change trading mode (demo → real)
  2. Modify drawdown limits
  3. Reset balance to arbitrary value
  4. Unhalt halted system
  5. Force-close open positions
- Auth hanya ada di WebSocket (`DASHBOARD_WS_TOKEN`), bukan REST API.
- **Impact:** CRITICAL security — unauthorized access bisa menyebabkan kerugian financial.
- **Fix:**
  1. Add API key middleware / Bearer token auth ke semua mutable endpoints.
  2. Minimal: Shared token check pada PATCH/POST endpoints.
  ```python
  from fastapi import Depends, Header, HTTPException
  
  async def verify_admin(x_api_key: str = Header(...)):
      if x_api_key != DASHBOARD_WS_TOKEN:
          raise HTTPException(401, "Unauthorized")
  
  @app.patch("/api/system/config", dependencies=[Depends(verify_admin)])
  ```

**🟠 H-01: `get_portfolio()` memanggil sync `get_current_price()` di async handler**
```python
@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    ...
    from agent.production_lifecycle import get_current_price
    price_cache: dict[str, float] = {}
    for pair in list(getattr(lc, "_active", {}).keys()):
        try:
            price_cache[pair] = get_current_price(pair)  # ← SYNC! Blocks event loop
        except Exception:
            pass
```
- `get_current_price()` melakukan HTTP call sync ke OANDA → blocks async event loop.
- Dengan 3 active trades → 3 sequential blocking HTTP calls pada setiap portfolio refresh.
- **Impact:** Dashboard latency + blocks semua concurrent async operations selama fetch.
- **Fix:** Gunakan `get_current_price_async()` (sudah di-import di `push_portfolio_update()`).

**🟠 H-02: `/api/system/unhalt` akses langsung ke private attributes**
```python
@app.post("/api/system/unhalt")
async def unhalt_system() -> dict:
    lc._halted = False           # ← Direct private attribute mutation!
    old_reason = lc._halt_reason  # ← Direct private attribute read!
    lc._halt_reason = ""
    await lc.save_state()
```
- Langsung modify internal state tanpa melalui public API lifecycle.
- Jika lifecycle menambahkan validation di unhalt (misal: check conditions), dashboard bypass-nya.
- **Fix:** Tambahkan `lifecycle.unhalt()` method dan panggil dari dashboard:
  ```python
  result = await lc.unhalt()
  ```

**🟡 M-03: `push_portfolio_update()` menduplikasi portfolio computation logic**
```python
# get_portfolio() — lines ~340-440:
for pair, (trade, mgr) in getattr(lc, "_active", {}).items():
    cur_price = price_cache.get(pair)
    raw_pnl = (cur_price - trade.entry_price) if trade.direction == "buy" else ...
    ... # 30+ lines of floating P/L computation

# push_portfolio_update() — lines ~730-770:
for pair, (trade, _mgr) in getattr(lc, "_active", {}).items():
    cur_price = await get_current_price_async(pair)
    raw_pnl = (cur_price - trade.entry_price) if trade.direction == "buy" else ...
    ... # Same computation, duplicated
```
- DRY violation — same P/L calculation logic duplicated.
- Jika satu berubah, yang lain bisa drift.
- **Fix:** Extract ke helper: `_compute_trade_floating(trade, cur_price, lc)`.

**🟡 M-04: `record_equity_point()` fire-and-forget tanpa error handling**
```python
def record_equity_point() -> None:
    ...
    if _repo:
        asyncio.ensure_future(_repo.save_equity_point(balance, hwm))
        # ← No error handling! If save fails, silently lost.
```
- `asyncio.ensure_future()` membuat task tapi tidak handle exceptions.
- Unhandled exceptions di task akan log "Task exception was never retrieved" warning.
- **Impact:** Equity point bisa hilang tanpa notifikasi.
- **Fix:**
  ```python
  async def _safe_save_equity(balance, hwm):
      try:
          await _repo.save_equity_point(balance, hwm)
      except Exception as exc:
          logger.warning("Equity save failed: %s", exc)
  asyncio.ensure_future(_safe_save_equity(balance, hwm))
  ```

**🔵 L-05: `push_analysis_update()` FIFO overflow logic bisa hapus data terkini**
```python
async def push_analysis_update(pair: str, data: dict) -> None:
    _analyses[pair.upper()] = data
    while len(_analyses) > _MAX_ANALYSES:
        oldest_key = next(iter(_analyses))
        del _analyses[oldest_key]
```
- Dict iteration order is insertion order (Python 3.7+). Tapi `_analyses[pair.upper()] = data` bisa update existing key (tidak pindah ke akhir di Python dict).
- **Impact:** Negligible — hanya 6 pairs aktif, _MAX_ANALYSES = 50.

**🔵 L-06: `SystemConfigPatch.fixed_lot_size` allows 0.0**
```python
fixed_lot_size: Optional[float] = Field(default=None, ge=0.0)
```
- `ge=0.0` memungkinkan lot size 0 yang tidak valid untuk trading.
- **Fix:** `ge=0.001` atau `gt=0.0`.

**🔵 L-07: `_trade_to_dict()` tidak include `post_mortem_json`**
- `get_trades()` menggunakan `_trade_to_dict()` yang tidak parse post_mortem.
- `get_single_trade()` manually adds post_mortem setelah `_trade_to_dict()`.
- **Impact:** Low — list view tidak butuh post_mortem detail.

---

## Security Summary

| Endpoint | Method | Auth | Risk |
|----------|--------|------|------|
| `/api/portfolio` | GET | ❌ None | Read-only, info exposure |
| `/api/system/status` | GET | ❌ None | Exposes API key status |
| `/api/system/config` | GET | ❌ None | Config exposure |
| `/api/system/config` | PATCH | ❌ None | **💀 Can modify trading params** |
| `/api/system/config/reset-default` | POST | ❌ None | **Can reset config** |
| `/api/system/balance/set` | POST | ❌ None | **💀 Can set arbitrary balance** |
| `/api/system/unhalt` | POST | ❌ None | **💀 Can unhalt halted system** |
| `/api/positions/{id}/close` | POST | ❌ None | **💀 Can force-close positions** |
| `/api/trades` | GET | ❌ None | Trade history exposure |
| `/ws` | WebSocket | ✅ Token | Only authenticated endpoint |

---

## Statistics

| Metric | Value |
|--------|-------|
| Total lines reviewed | ~1,380 |
| Functions/classes analyzed | 30+ |
| CRITICAL issues | 1 |
| HIGH issues | 2 |
| MEDIUM issues | 4 |
| LOW issues | 7 |
| Dead code items | 1 |
| Consistency issues | 2 |
| **Total issues** | **17** |

---

## Rekomendasi Prioritas

### Harus Fix Segera (CRITICAL/HIGH)
1. **C-01:** 🔑 Tambahkan auth middleware ke semua mutable REST endpoints (PATCH/POST). Minimal: shared API token check.
2. **H-01:** Ganti `get_current_price()` → `get_current_price_async()` di `get_portfolio()`.
3. **H-02:** Tambahkan `unhalt()` method di lifecycle, jangan akses private attributes langsung.

### Sebaiknya Fix (MEDIUM)
4. **M-02:** Ganti hardcoded `/15` → `/MAX_POSSIBLE_SCORE` (14) di templates.
5. **M-03:** Extract shared floating P/L computation helper.
6. **M-01:** Implementasi WATCHING/APPROACHING notifications di handler.
7. **M-04:** Tambahkan error handling di `record_equity_point()` async task.

### Cleanup (LOW)
8. **DEAD-01:** Hapus `ALL_PAIRS` import di runner.py.
9. **L-06:** `fixed_lot_size` validation `ge=0.001` bukan `ge=0.0`.
10. **L-01:** Use `TYPE_CHECKING` pattern untuk pandas import.

---

## Catatan Positif

1. **Circuit breaker** pada WhatsApp API — production-grade resilience.
2. **Retry with exponential backoff** — 3 attempts, 1-2-4s intervals.
3. **Event ring buffer** (`deque(maxlen=200)`) — survives page refresh, bounded memory.
4. **Fault-isolated scheduling** — batch failure → per-pair fallback.
5. **WebSocket broadcast** — real-time dashboard updates tanpa polling.
6. **Pydantic request validation** — `SystemConfigPatch`, `BalanceSetRequest` with field constraints.
7. **Equity persistence** — DB-backed with trim untuk bounded growth.
8. **Chart fallback** — generation failure → text-only alert gracefully.
