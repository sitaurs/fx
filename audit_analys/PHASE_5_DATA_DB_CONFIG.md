# PHASE 5 AUDIT — Data Layer, Database & Configuration

**Tanggal:** 2025-06-05  
**Cakupan:** 5 file, ~1,210 baris kode  
**Auditor:** AI Audit Agent

---

## File yang Diaudit

| # | File | Lines | Fungsi Utama |
|---|------|-------|--------------|
| 1 | `data/fetcher.py` | ~600 | OHLCV data fetcher, OANDA/MT5/Demo backends |
| 2 | `database/models.py` | ~160 | SQLAlchemy ORM models |
| 3 | `database/repository.py` | ~200 | Async data-access layer |
| 4 | `config/settings.py` | ~230 | Central configuration |
| 5 | `config/strategy_rules.py` | ~120 | Scoring weights & strategy rules |

---

## Ringkasan Temuan

| Severity | Count | Keterangan |
|----------|-------|-----------|
| 🔴 CRITICAL | 1 | DNS refresh never called → stale IPs |
| 🟠 HIGH | 2 | Trade stats miss TRAIL_PROFIT wins, module-level network call at import |
| 🟡 MEDIUM | 5 | Deprecated asyncio API, sync httpx, dual config source, MVP_PAIRS duplication |
| 🔵 LOW | 8 | Field naming, hardcoded terminal states, probe startup delay |
| 💀 DEAD CODE | 1 | `ALL_PAIRS` nearly unused duplicate of `MVP_PAIRS` |
| 🔄 CONSISTENCY | 3 | demo_pnl naming, available_pairs format inconsistency |

---

## Detail Temuan per File

---

### 1. `data/fetcher.py` (~600 lines)

**Deskripsi:** Pluggable OHLCV data fetcher. Backends: OandaBackend (production), MT5ApiBackend (local bridge), DemoBackend (testing). Dengan DNS bypass untuk ISP Indonesia.

#### Arsitektur
- **Abstract base:** `DataBackend` ABC dengan `fetch_ohlcv` dan `available_pairs`
- **DNS bypass:** Monkey-patch `socket.getaddrinfo` untuk bypass ISP blocking OANDA
- **DoH resolve:** `_resolve_via_doh()` via Cloudflare/Google DNS-over-HTTPS
- **Module-level init:** `_active_backend` diinisialisasi saat import
- **Async wrapper:** `fetch_ohlcv_async()` runs sync in thread pool

#### Temuan

**🔴 C-01: `refresh_dns_overrides()` didefinisikan tapi TIDAK PERNAH dipanggil**
```python
# fetcher.py line 139:
def refresh_dns_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Verify and refresh DNS overrides using DoH (FIX §7.6)."""
    ...

# TIDAK ADA caller di seluruh codebase!
# Satu-satunya usage: definisi dan internal docstring reference.
```
- **Masalah:** DNS IP overrides (`104.18.34.254`, `172.64.148.74`) di-hardcode di `config/settings.py` dengan note "Resolved on 2026-02-22". IP Cloudflare bisa berubah kapan saja.
- Jika IP berubah → semua OANDA API calls FAIL → agent berhenti trading.
- Fungsi refresh sudah ditulis tapi tidak digunakan oleh scheduler/startup.
- **Impact:** CRITICAL — single point of failure untuk connectivity. Saat IP berubah, agent mati total tanpa auto-recovery.
- **Fix:**
  1. Panggil `refresh_dns_overrides()` di startup (setelah backend init).
  2. Jadwalkan periodic refresh (setiap 6 jam) di APScheduler.
  3. Log warning jika refresh gagal.

**🟠 H-01: Module-level `_init_default_backend()` melakukan network call saat import**
```python
# Line ~570:
_active_backend: DataBackend = _init_default_backend()
```
- Setiap `import data.fetcher` atau `from data.fetcher import fetch_ohlcv` trigger:
  1. OANDA API probe (2 candles)
  2. Jika gagal → MT5 probe
  3. Jika gagal + TRADING_MODE=real → `RuntimeError` crash
- **Impact:**
  - Startup time +1-3 detik (OANDA probe)
  - Test imports memerlukan mock atau env vars
  - Jika OANDA down saat restart → agent tidak bisa start sama sekali
- **Fix:** Lazy init — buat `get_backend()` yang init on first call, bukan saat import.
  ```python
  _active_backend: DataBackend | None = None
  def get_backend() -> DataBackend:
      global _active_backend
      if _active_backend is None:
          _active_backend = _init_default_backend()
      return _active_backend
  ```

**🟡 M-01: `fetch_ohlcv_async` menggunakan deprecated `asyncio.get_event_loop()`**
```python
async def fetch_ohlcv_async(...) -> dict:
    loop = asyncio.get_event_loop()  # ← Deprecated Python 3.10+
    return await loop.run_in_executor(None, fetch_ohlcv, pair, timeframe, count)
```
- Python 3.12 (VPS) akan emit DeprecationWarning.
- **Fix:** Ganti ke `asyncio.get_running_loop()`.

**🟡 M-02: OandaBackend menggunakan sync `httpx.Client`, di-wrap ke async via thread pool**
```python
class OandaBackend(DataBackend):
    def _ensure_client(self) -> httpx.Client:  # SYNC client
        ...

# Di-wrap jadi async:
async def fetch_ohlcv_async(...):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_ohlcv, ...)  # occupies thread
```
- Setiap call menempati 1 thread dari default executor.
- Dengan 6 pairs × 4 TFs = 24 concurrent calls, bisa exhaust thread pool.
- **Impact:** Medium — `context_builder.py` sudah sequential (Phase 2 C-01), jadi saat ini tidak concurrent. Tapi jika Phase 2 C-01 diperbaiki jadi concurrent, ini akan menjadi bottleneck.
- **Fix:** Migrate ke `httpx.AsyncClient` untuk native async.

**🔵 L-01: `available_pairs()` format berbeda per backend**

| Backend | Return Format | Example |
|---------|--------------|---------|
| OandaBackend | OANDA instrument names | `["EUR_USD", "XAU_USD"]` |
| MT5ApiBackend | Empty list | `[]` |
| DemoBackend | Internal pair names | `["XAUUSD", "EURUSD"]` |

- Caller yang membandingkan output dari `available_pairs()` bisa mendapat inconsistent results.
- **Impact:** Low — fungsi ini jarang dipanggil di production.
- **Fix:** Normalize output ke internal format (XAUUSD, EURUSD, dll).

**🔵 L-02: OANDA error logging mungkin expose sensitive info**
```python
except httpx.HTTPStatusError as exc:
    logger.error(
        "OANDA HTTP %d for %s %s: %s",
        exc.response.status_code, pair, timeframe,
        exc.response.text[:300],  # ← Could contain account info
    )
```
- **Impact:** Low — masuk ke log file, bukan user-facing.
- **Fix:** Sanitize response text atau hanya log status code + reason.

**✅ GOOD:**
- DNS-over-HTTPS fallback (Cloudflare + Google) — robust.
- OANDA v20 implementation dengan proper auth, granularity mapping.
- DemoBackend dengan geometric Brownian motion — realistic synthetic data.
- Safety guard: refuse DemoBackend in TRADING_MODE=real.
- `_to_instrument()` auto-conversion XAUUSD → XAU_USD.

---

### 2. `database/models.py` (~160 lines)

**Deskripsi:** SQLAlchemy ORM models. 4 tables: trades, analysis_sessions, settings_kv, equity_points.

#### Arsitektur
- `Trade`: Full trade record dengan entry/exit, result, scoring, SL management, PnL
- `AnalysisSession`: State machine history per scan
- `SettingsKV`: Dynamic key-value settings
- `EquityPoint`: Balance history for equity chart

#### Temuan

**🔵 L-03: `demo_pnl` dan `demo_balance_after` field naming mismatch**
```python
# database/models.py — Trade model:
demo_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
demo_balance_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

# Tapi di production_lifecycle.py line 1666-1667:
demo_pnl=round(pnl, 2),          # ← Used in REAL mode too!
demo_balance_after=round(self.balance, 2),
```
- Field bernama "demo_" tapi digunakan untuk real mode juga.
- **Impact:** Low — field tetap menyimpan data yang benar, hanya naming yang confusing.
- **Fix:** Rename ke `pnl` dan `balance_after` (requires migration).

**🔵 L-04: `TradeResult` enum tidak include semua edge cases**
```python
class TradeResult(str, enum.Enum):
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    SL_HIT = "SL_HIT"
    BE_HIT = "BE_HIT"
    TRAIL_PROFIT = "TRAIL_PROFIT"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    CANCELLED = "CANCELLED"
```
- Missing: `TIMEOUT` (pending setup TTL expired), `EXPIRED` (session expired without trade).
- `result` column is `String(20)`, bukan `SAEnum`, jadi arbitrary values bisa masuk tanpa validation.
- **Impact:** Low — lifecycle code handles string values, not enum references.
- **Fix:** Use `SAEnum(TradeResult)` for DB-level validation.

**🔵 L-05: `AnalysisSession.state` sebagai String, bukan Enum reference**
```python
state: Mapped[str] = mapped_column(String(20))  # current AnalysisState
```
- Tidak ada constraint ke valid states.
- **Impact:** Low — lifecycle code validates state transitions programmatically.

**✅ GOOD:**
- UTC-aware timestamps dengan timezone.utc.
- `trade_id` indexed dan unique.
- `onupdate` lambda untuk automatic `updated_at`.
- `EquityPoint` model untuk persistent equity chart.

---

### 3. `database/repository.py` (~200 lines)

**Deskripsi:** Async data-access layer menggunakan SQLAlchemy async engine + aiosqlite.

#### Arsitektur
- `Repository` class dengan session factory pattern
- CRUD untuk trades, analysis sessions, settings_kv, equity
- Stats computation via in-memory Python

#### Temuan

**🟠 H-02: `trade_stats()` tidak menghitung TRAIL_PROFIT sebagai win**
```python
wins = sum(
    1
    for t in trades
    if t.result in ("TP1_HIT", "TP2_HIT", "MANUAL_CLOSE")
    and (t.pips or 0) > 0
)
losses = sum(
    1 for t in trades if t.result == "SL_HIT"
)
```
- **TRAIL_PROFIT:** Trailing stop exit in profit → TIDAK dihitung sebagai win.
- **BE_HIT:** Breakeven exit → TIDAK dihitung (debatable, tapi seharusnya neutral).
- **CANCELLED:** Cancelled trade → masih dalam `len(trades)` denominator.
- **Impact:**
  - Winrate deflated: `wins / total` dimana total includes TRAIL_PROFIT, BE_HIT, CANCELLED.
  - Contoh: 5 TP_HIT + 3 TRAIL_PROFIT + 2 SL_HIT = winrate 50% (seharusnya 80%).
- **Fix:**
  ```python
  win_results = {"TP1_HIT", "TP2_HIT", "TRAIL_PROFIT"}
  wins = sum(1 for t in trades if t.result in win_results or
             (t.result == "MANUAL_CLOSE" and (t.pips or 0) > 0))
  # Exclude CANCELLED and BE_HIT from denominator:
  relevant = [t for t in trades if t.result not in ("CANCELLED",)]
  winrate = wins / len(relevant) if relevant else 0.0
  ```

**🟡 M-03: `trade_stats()` loads semua trades ke memory untuk stats**
```python
trades = await self.list_trades(mode=mode, limit=10_000)
```
- Loads up to 10,000 Trade objects ke memory untuk hitung sum/count.
- **Impact:** Low saat ini (< 100 trades), tapi tidak scalable.
- **Fix:** Gunakan SQL aggregation (`func.count`, `func.sum`, `CASE WHEN`).

**🔵 L-06: `active_sessions()` hardcode terminal states**
```python
.where(AnalysisSession.state.notin_(["CLOSED", "CANCELLED"]))
```
- Terminal states hardcoded sebagai strings, bukan reference ke `AnalysisState` enum di `state_machine.py`.
- **Impact:** Low — jika enum berubah, harus ingat update di sini juga.
- **Fix:** Import terminal states dari state_machine: `from agent.state_machine import TERMINAL_STATES`.

**🔵 L-07: No explicit error handling di `save_trade()` / `save_session()`**
- Jika `session.commit()` gagal (constraint violation, disk full), exception propagates tanpa context.
- **Impact:** Low — caller catches exceptions, tapi error message tidak informatif.
- **Fix:** Wrap dalam try-except dengan logging.

**✅ GOOD:**
- Async pattern dengan `async_sessionmaker` dan context managers.
- `merge()` untuk upsert pattern (insert or update).
- Equity trimming prevents unbounded growth.
- `load_equity_history()` menggunakan reverse-order pattern untuk correct chronological order.

---

### 4. `config/settings.py` (~230 lines)

**Deskripsi:** Central configuration. Loads dari environment variables dengan defaults. Mencakup API keys, pair/TF config, tolerances, trading parameters.

#### Arsitektur
- Environment-based config via `os.getenv()` dan `python-dotenv`
- Constants untuk tolerances, scoring, timeframes
- Auto-detect OANDA practice vs live dari account ID prefix

#### Temuan

**🟡 M-04: `MVP_PAIRS` dan `ALL_PAIRS` adalah daftar yang identik, urutan berbeda**
```python
MVP_PAIRS = ["XAUUSD", "EURUSD", "GBPJPY", "USDCHF", "USDCAD", "USDJPY"]
ALL_PAIRS = ["XAUUSD", "EURUSD", "USDCHF", "USDCAD", "GBPJPY", "USDJPY"]
```
- Keduanya punya 6 pair yang sama, hanya urutan berbeda.
- `MVP_PAIRS` digunakan di `main.py`, tests, scheduler.
- `ALL_PAIRS` di-import di `scheduler/runner.py` tapi hampir tidak digunakan.
- **Impact:** Confusing — developer tidak tahu mana yang "canonical".
- **Fix:** Hapus `ALL_PAIRS`, gunakan `MVP_PAIRS` saja. Atau rename `MVP_PAIRS` → `TRADING_PAIRS`.

**🟡 M-05: Dual source of truth untuk trading parameters (sudah dicatat Phase 4)**

| Parameter | settings.py | strategy_rules.py |
|-----------|------------|-------------------|
| Min R:R | `MIN_RR = 1.5` | `VALIDATION_RULES["min_rr"] = 1.5` |
| SL ATR mult | `SL_ATR_MULTIPLIER = 1.5` | `VALIDATION_RULES["sl_max_atr_mult"] = 2.5` |

- `validator.py` hanya gunakan strategy_rules.py values.
- `MIN_RR` dan `SL_ATR_MULTIPLIER` imported di validator.py tapi unused (Phase 4 DEAD-03).
- **Fix:** Consolidate — satu source of truth untuk semua trading parameters.

**🔵 L-08: DNS override IPs dengan tanggal hardcoded**
```python
# Resolved via Cloudflare DNS-over-HTTPS on 2026-02-22
OANDA_DNS_OVERRIDES = {
    "api-fxpractice.oanda.com": "104.18.34.254",
    ...
}
```
- Tanggal resolution di-hardcode di comment. Tidak ada automated freshness check.
- Terkait dengan C-01 (refresh never called).

**🔵 L-09: `TRADING_START_HOUR_WIB` / `TRADING_END_HOUR_WIB` mismatch dengan scheduler**
```python
TRADING_START_HOUR_WIB: int = 14   # 14:00 WIB
TRADING_END_HOUR_WIB: int = 2      # 02:00 WIB next day
```
- Scheduler di `main.py` menggunakan fixed cron times: 06:00, 13:30, 19:00, 22:30 WIB.
- `TRADING_START_HOUR_WIB = 14` tapi ada scan pada 06:00 WIB (untuk JPY pairs during Tokyo session) dan 13:30 WIB.
- Session check di `main.py` menggunakan `_is_within_any_session()` yang punya definisi session berbeda.
- **Impact:** Low — `TRADING_START/END_HOUR_WIB` hanya digunakan sebagai global bounds, bukan per-session check.

**✅ GOOD:**
- `_env_bool()` helper — robust env var parsing.
- Auto-detect practice vs live via account ID prefix.
- Per-pair tolerances dan point sizes — well-organized.
- `PRICE_SANITY_THRESHOLDS` per pair — good guard against stale plans.
- `ENTRY_ZONE_EXECUTION_BUFFER_PIPS` configurable via env.

---

### 5. `config/strategy_rules.py` (~120 lines)

**Deskripsi:** Scoring weights, strategy mode definitions, anti-rungkad checks, validation rules, TF weights. (Sudah di-review partial di Phase 4)

#### Temuan Tambahan (beyond Phase 4)

**🔵 L-10: `STRATEGY_MODES["scalping_channel"]` requires `channel_or_flag` tapi tidak ada tool yang mendeteksinya**
```python
"scalping_channel": {
    "requires": ["channel_or_flag"],
    "sweep_required": False,
    "choch_required": False,
},
```
- Tidak ada `tools/channel.py` atau `tools/flag.py` di codebase.
- Strategy mode ini tidak bisa activated karena missing tool.
- **Impact:** Low — mode ini adalah placeholder untuk future development.
- **Fix:** Tambahkan comment `# FUTURE: requires channel/flag detection tool` atau implementasi.

**💀 DEAD-01: `ALL_PAIRS` hampir tidak digunakan di production**
- Hanya di-import di `scheduler/runner.py` tapi `MVP_PAIRS` yang digunakan sebagai default.
- Tests hanya gunakan `MVP_PAIRS`.
- **Fix:** Hapus `ALL_PAIRS` atau alias ke `MVP_PAIRS`.

---

## Cross-Cutting Issues

---

### 🔄 CONSISTENCY-01: Data backend probe vs production robustness

```
startup flow:
  import data.fetcher
    → _init_default_backend()
      → OandaBackend.fetch_ohlcv("EURUSD", "M15", count=2)  # PROBE
        → Jika gagal → RuntimeError (TRADING_MODE=real)
        → Jika sukses → backend active

runtime flow:
  scheduler calls fetch_ohlcv
    → Jika OANDA down → return []  # empty, no crash
    → Caller gets empty candles → skips analysis
```

- **Asymmetry:** Startup CRASHES on failure, runtime returns empty gracefully.
- Jika OANDA briefly down saat restart → agent tidak bisa start.
- **Fix:** Startup should retry with backoff, bukan one-shot probe.

### 🔄 CONSISTENCY-02: `demo_pnl` / `demo_balance_after` field naming

- Model uses "demo_" prefix tapi fields diisi saat TRADING_MODE=real juga.
- `production_lifecycle.py` (line 1666-1667) mengisi fields ini regardless of mode.
- Dashboard menampilkan `demo_balance_after` sebagai balance — correct behavior, misleading name.

### 🔄 CONSISTENCY-03: Strategy modes defined tapi tidak enforced (reiterated from Phase 4)

- `STRATEGY_MODES` di strategy_rules.py:
  - `index_correlation` requires `dxy_gate_pass` → tool DISABLED
  - `scalping_channel` requires `channel_or_flag` → tool NOT IMPLEMENTED
  - `sniper_confluence` requires `trendline_valid` + `zone_detected` → tools exist ✅
- Hanya 1 dari 3 strategy modes yang fully implementable.
- AI dikasih prompt tentang ketiga modes, bisa confused.

---

## Security Review

| Aspect | Status | Note |
|--------|--------|------|
| API keys storage | ✅ via env vars | Loaded from .env, not hardcoded |
| API key in memory | ⚠️ module-level | `GEMINI_API_KEY`, `OANDA_API_KEY` as module-level strings |
| DB access | ✅ local SQLite | No network exposure |
| DNS override | ⚠️ monkey-patch | Global socket override affects entire process |
| Dashboard auth | ⚠️ optional token | `DASHBOARD_WS_TOKEN` empty = no auth |
| WhatsApp auth | ✅ via env vars | Basic auth credentials from env |
| Error logging | ⚠️ response body | OANDA error logs include response text[:300] |
| SQL injection | ✅ protected | SQLAlchemy parameterized queries |

---

## Statistics

| Metric | Value |
|--------|-------|
| Total lines reviewed | ~1,210 |
| Functions/classes analyzed | 25+ |
| CRITICAL issues | 1 |
| HIGH issues | 2 |
| MEDIUM issues | 5 |
| LOW issues | 8 |
| Dead code items | 1 |
| Consistency issues | 3 |
| **Total issues** | **20** |

---

## Rekomendasi Prioritas

### Harus Fix Segera (CRITICAL/HIGH)
1. **C-01:** Jadwalkan `refresh_dns_overrides()` periodic (setiap 6 jam) + panggil saat startup.
2. **H-02:** Perbaiki `trade_stats()` — include TRAIL_PROFIT sebagai win, exclude CANCELLED dari denominator.
3. **H-01:** Ubah `_init_default_backend()` ke lazy init untuk robustness.

### Sebaiknya Fix (MEDIUM)
4. **M-01:** Ganti `asyncio.get_event_loop()` → `asyncio.get_running_loop()`.
5. **M-02:** Migrate OandaBackend ke `httpx.AsyncClient` (especially setelah Phase 2 C-01 sequential → concurrent fix).
6. **M-04:** Hapus `ALL_PAIRS`, consolidate ke `MVP_PAIRS` / `TRADING_PAIRS`.
7. **M-05:** Single source of truth untuk MIN_RR dan SL parameters.

### Cleanup (LOW)  
8. **L-03:** Rename `demo_pnl` → `pnl`, `demo_balance_after` → `balance_after` (if migration feasible).
9. **L-06:** Import terminal states dari state_machine.py instead of hardcoding.
10. **L-10:** Mark `scalping_channel` strategy mode sebagai FUTURE/placeholder.

---

## Catatan Positif

1. **DNS-over-HTTPS bypass** — creative solution untuk ISP blocking di Indonesia.
2. **Multi-backend architecture** — clean abstraction dengan fallback chain.
3. **Safety guard: DemoBackend blocked in real mode** — mencegah trading dengan data sintetis.
4. **Async SQLAlchemy** — modern pattern dengan aiosqlite.
5. **OANDA practice/live auto-detect** — smart account ID prefix check.
6. **Equity trimming** — prevents unbounded DB growth.
7. **Per-pair tolerances** — Gold ($2), EUR (10 pips), JPY (15 pips) — well-calibrated.
8. **OHLC conversion** — robust OANDA mid mapping with missing data handling.
