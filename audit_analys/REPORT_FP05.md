# FP-05 Report — Production Lifecycle Part B (Analysis & Monitoring)

**Date:** 2025-01-XX  
**Phase:** FP-05  
**Files modified:** `agent/production_lifecycle.py`, `config/settings.py`, `tests/test_production_lifecycle.py`  
**Tests:** 40 lifecycle / **792 total regression** (0 failures, +4 new tests)

---

## Summary

FP-05 addressed 6 audit items from PHASE_1_CORE_ENGINE.md related to the
production lifecycle's analysis & monitoring pipeline. After thorough code
analysis, 3 items were verified as **already implemented**, 1 was a **code
quality fix** (save/restore fields), 1 was a **config extraction**, and 1
received a **TODO marker** for future implementation.

---

## Fixes Applied

### 1. Audit M-01 (save_active_trades) — Code Quality ✅
**Problem:** `save_active_trades()` used excessive `getattr(trade, "field", default)` for fields that are defined on the `ActiveTrade` dataclass. Additionally, 5 fields were **never serialized**: `original_sl`, `entry_zone_type`, `entry_zone_low`, `entry_zone_high`, `recommended_entry`.

**Fix:** Replaced all `getattr()` calls with direct attribute access (`trade.field`). Added serialization of the 5 missing fields.

**File:** `agent/production_lifecycle.py` lines 277-310

### 2. Audit M-02 (restore_active_trades) — Data Loss Prevention ✅
**Problem:** `restore_active_trades()` did not restore `original_sl`, `entry_zone_type`, `entry_zone_low`, `entry_zone_high`, `recommended_entry`. After a restart, these fields would be lost — `original_sl` would reset to `stop_loss` via `__post_init__`, and zone context would be gone entirely.

**Fix:** Added all 5 fields to the `ActiveTrade()` constructor call in restore, with safe `.get()` defaults for backward compatibility with old saved data.

**File:** `agent/production_lifecycle.py` lines 340-370

### 3. L-11 (ttl_hours hardcoded 4.0) — Config Extraction ✅
**Problem:** Pending setup TTL hardcoded as `4.0` in `on_scan_complete()`.

**Fix:** Added `PENDING_SETUP_DEFAULT_TTL_HOURS` to `config/settings.py` (env-configurable, default 4.0). Used in lifecycle via import.

**Files:** `config/settings.py`, `agent/production_lifecycle.py` line 1195

### 4. L-08 (news_imminent never populated) — TODO Marker ✅
**Problem:** `TradeManager.evaluate()` accepts `news_imminent` param, but `production_lifecycle.py` never passes it (no calendar service exists). `news_within_30m` in `generate_monitoring_report()` also defaults to `False`.

**Fix:** Added TODO comment at the `mgr.evaluate()` call site documenting the gap and referencing masterplan §13 rules 6-7.

**File:** `agent/production_lifecycle.py` line 1480

---

## Items Verified Already Implemented (No Change Needed)

### M-01 (SL hit → BE reclassification) ✅ N/A
`_close_trade()` already checks `sl_moved_to_be` at lines 1599-1625 and reclassifies `SL_HIT` → `BE_HIT` when appropriate.

### M-02 (Race condition in check_active_trades) ✅ N/A
`check_active_trades()` already acquires `self._trade_lock` at line 1440 before iterating active trades.

### M-04 (Startup exception handling) ✅ N/A
Both `init()` and `on_startup()` use granular per-step try/except blocks — there is no catch-all exception handler.

### CON-05 (error key inconsistency) ✅ N/A
Only `"error"` key is used throughout lifecycle error dicts. No `"err_msg"` key exists in this file.

---

## New Tests (4)

| Test | Class | Validates |
|------|-------|-----------|
| `test_round_trip_preserves_new_fields` | `TestFP05SaveRestoreRoundTrip` | Save/restore round-trip preserves original_sl, entry_zone_* fields |
| `test_round_trip_missing_new_fields_defaults` | `TestFP05SaveRestoreRoundTrip` | Old-format data (pre-FP-05) restores with safe defaults |
| `test_save_no_getattr_all_direct` | `TestFP05SaveRestoreRoundTrip` | Serialized dict contains all expected keys including new fields |
| `test_pending_uses_config_ttl` | `TestFP05TTLConfigConstant` | PENDING_SETUP_DEFAULT_TTL_HOURS config constant exists and defaults to 4.0 |

---

## Regression

```
792 passed, 8 skipped, 7 warnings in 21.71s
```
