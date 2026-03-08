# FP-04 Report: Production Lifecycle Part A — Emergency & Close Pipeline

**Status:** ✅ COMPLETED  
**Date:** 2025-07-12  
**Tests:** 36/36 lifecycle, **788 passed** full regression (was 782)

---

## Issues Fixed

### C-01 🔴 CRITICAL — Dynamic DB Path in Emergency Save
**File:** `main.py` line ~654, `config/settings.py`  
**Problem:** `_emergency_save_sync()` hardcoded `sqlite3.connect("data/forex_agent.db")`. On VPS deployment at `/opt/ai-forex-agent/`, this resolves relative to CWD — fragile if process starts from different directory.  
**Fix:**
1. Added `DB_FILE_PATH` constant to `config/settings.py` using `_PROJECT_ROOT / 'data' / 'forex_agent.db'`
2. `DATABASE_URL` now references `DB_FILE_PATH` (single source of truth)
3. `main.py` imports `DB_FILE_PATH` and uses it in `_emergency_save_sync()`
**Test:** `TestFP04EmergencySavePath` (2 tests)

### H-04 🟠 HIGH — Wrapup Persist Active Trades
**File:** `agent/production_lifecycle.py` `daily_wrapup()`  
**Problem:** `daily_wrapup()` called `save_state()` but NOT `save_active_trades()`. If bot crashes between wrapup and next periodic save cycle (5 min), active trade positions could be lost.  
**Fix:** Added `await self.save_active_trades()` call BEFORE `save_state()` in `daily_wrapup()`  
**Test:** `TestFP04WrapupPersistence` (1 test — verifies `save_active_trades` is called)

### H-05 🟠 HIGH — Drawdown Halt Blocks Pending Queue
**File:** `agent/production_lifecycle.py` `on_scan_complete()`  
**Problem:** When system is halted due to drawdown, valid setups were still added to pending queue. This created false hope — pending setups would repeatedly fail `can_open_trade()` check every 60s until TTL expiry.  
**Fix:**
1. In-zone path: if `self._halted`, log block message and return None (don't add to pending)
2. Out-of-zone path: same — if halted, skip pending queue entirely
3. Non-halt reasons (max concurrent trades) still correctly add to pending
**Test:** `TestFP04DrawdownBlocksPending` (2 tests — halt blocks pending, max concurrent allows pending)

### M-08 🟡 — Equity Snapshot Error Handling
**File:** `dashboard/backend/main.py` `record_equity_point()`  
**Problem:** `asyncio.ensure_future(_repo.save_equity_point(balance, hwm))` — fire-and-forget with no error handling. If DB write fails, exception is swallowed silently by asyncio, or worse, logged as "Task exception was never retrieved".  
**Fix:** Wrapped in `async def _safe_save_equity()` with try/except that logs warning on failure.

### L-04 🔵 — Null Guards in Close Trade Pipeline
**File:** `agent/production_lifecycle.py` `_close_trade()`  
**Problem:**
1. `trade.opened_at.isoformat()` crashes if `opened_at` is None (corrupt restore edge case)
2. `(datetime.now() - trade.opened_at).total_seconds()` crashes if `opened_at` is None
3. Logger format `pips=%.1f pnl=$%.2f` could show excessive precision
**Fix:**
1. `opened_at` in close_result: conditional `.isoformat()` with None fallback
2. `duration`: guarded with `if trade.opened_at:` else `0`
3. Logger: uses `round()` for consistent formatting
**Test:** `TestFP04NullGuards` (1 test — None opened_at doesn't crash)

### Pre-existing bug fixed
**File:** `tests/test_production_lifecycle.py` `test_close_loss_affects_balance`  
**Problem:** Mock for `get_current_price` only covered `on_scan_complete`, not `_close_trade`. When `_pip_value_per_lot` calls `get_current_price("USDJPY")` during close, it hit the real (uninitialized) fetcher backend.  
**Fix:** Extended mock scope to cover both open and close operations.

---

## Issues Verified as N/A

| ID | Reason |
|----|--------|
| M-06 | `_store_event` is in `dashboard/backend/main.py`, not lifecycle. Already null-safe. |
| L-03 | Magic numbers (0.7 confidence) are in `agent/post_mortem.py` line 389, not lifecycle. Deferred to FP-13. |
| CON-02 | `_close_trade` always returns `dict`. Verified — no inconsistency exists. |

---

## Files Modified

| File | Changes |
|------|---------|
| `config/settings.py` | Added `DB_FILE_PATH` constant |
| `main.py` | Import `DB_FILE_PATH`, use in `_emergency_save_sync()` |
| `agent/production_lifecycle.py` | H-04: `save_active_trades()` in wrapup; H-05: drawdown blocks pending; L-04: null guards |
| `dashboard/backend/main.py` | M-08: wrapped equity snapshot in error handler |
| `tests/test_production_lifecycle.py` | Added 6 new tests (4 classes), fixed mock scope in existing test |

---

## Test Results

```
tests/test_production_lifecycle.py: 36 passed (was 30)
Full regression: 788 passed, 8 skipped, 7 warnings (was 782)
```
