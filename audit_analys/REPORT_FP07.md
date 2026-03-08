# REPORT FP-07 — Pending Manager + Orchestrator

**Phase:** FP-07  
**Status:** ✅ COMPLETE  
**Date:** 2025-01-xx  
**Regression:** 825 passed, 8 skipped, 7 warnings  
**New tests:** 20  

---

## Summary

FP-07 addresses the HIGH-priority race condition in pending setup execution (H-02), plus market-hours TTL, per-pair timing, defensive score extraction, retry config extraction, log standardization, and voting documentation.

---

## Items Fixed

### 1. H-02 — Duplicate Pending Execution Prevention [HIGH]
**Files:** `agent/pending_manager.py`, `agent/production_lifecycle.py`

**Problem:** If `_open_trade()` fails mid-execution, the pending setup remains in "pending" status and could be retried on the next scan cycle, potentially causing duplicate orders.

**Fix:**
- Added intermediate `"executing"` status to PendingSetup lifecycle: `pending → executing → executed`
- New `mark_executing()` method — sets status to "executing" before `_open_trade()` is called
- New `revert_executing()` method — reverts to "pending" on failure
- `check_zone_entries()` now skips non-"pending" setups (including "executing")
- `cleanup_old()` retains "executing" setups
- `to_persistence_list()` includes "executing" setups
- `production_lifecycle.check_pending_queue()` calls `mark_executing()` BEFORE `_open_trade()` and `revert_executing()` in the except block

### 2. M-05 — Market-Hours TTL
**File:** `agent/pending_manager.py`

**Problem:** TTL expiry used wall-clock time, so a setup created Friday evening would expire over the weekend when markets are closed.

**Fix:**
- Added `is_forex_market_open(dt)` utility — forex market open Sunday 22:00 UTC → Friday 22:00 UTC
- Added `count_market_hours(start, end)` utility — counts only market-open hours with 30-min step granularity
- `PendingSetup.is_expired` now uses `count_market_hours()` instead of `(now - created_at).total_seconds()`
- `PendingSetup.remaining_ttl_minutes` updated for market-hours awareness

### 3. M-07 — Per-Pair Gemini Timing
**File:** `agent/orchestrator.py`

**Problem:** No visibility into how long each phase takes per currency pair.

**Fix:**
- Added `self._phase_timings: dict[str, float]` initialized in `__init__`
- Added `phase_timings` property for external access
- `_phase_analyze`: Records elapsed time with `time.time()` in `finally` block, logs per pair
- `_phase_vote`: Records elapsed time and logs candidate count per pair

### 4. L-06 — Log Message Standardization
**Files:** `agent/pending_manager.py`, `agent/production_lifecycle.py`

**Problem:** Log messages mixed emoji prefixes (🚀, 📋, ⏰) and arrow characters (→) inconsistently.

**Fix:**
- Removed all emoji prefixes from log messages in pending_manager.py
- Standardized to English text with `->` arrow notation
- `restore_from_list` now logs skipped expired setups with clear message

### 5. L-07 — Defensive `_extract_score_flags`
**File:** `agent/orchestrator.py`

**Problem:** `_extract_score_flags()` had minimal error handling — a single corrupted analysis dict could crash the entire scoring pipeline.

**Fix:**
- Complete rewrite: initializes default flags dict first
- Each flag computation wrapped in individual `try/except` block
- Handles `atr=None` safely: `isinstance(atr_raw, dict)` check before `.get()`
- Missing/corrupted data returns sensible defaults instead of exceptions

**Bug found during testing:** `primary.get("atr", {}).get("current", 1.0)` crashed when ATR value was `None` (not a dict). Fixed with `isinstance` guard.

### 6. L-12 — Retry Config Extraction
**Files:** `config/settings.py`, `agent/gemini_client.py`

**Problem:** `MAX_RETRIES = 3` and `RETRY_BASE_DELAY = 1.0` were hardcoded in gemini_client.py.

**Fix:**
- Added `GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))` to config/settings.py
- Added `GEMINI_RETRY_BASE_DELAY = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "1.0"))` to config/settings.py
- `gemini_client.py` now imports and uses these config values

### 7. CON-04 — State Naming Documentation
**File:** `agent/pending_manager.py`

**Problem:** Audit flagged mismatch between `PENDING_ENTRY` state and `WATCHING` — but `PENDING_ENTRY` doesn't exist in codebase.

**Resolution:** Verified N/A for code change. Added comprehensive docstring to `PendingManager` class documenting the state naming relationship between the pending lifecycle (`pending → executing → executed | expired | cancelled | invalidated`) and the orchestrator's `AnalysisState` lifecycle.

### 8. D-04 — `remove_expired()` Dead Method
**Resolution:** Verified N/A. The method `remove_expired()` does not exist in the codebase. The actual cleanup is handled by `cleanup_expired()` which is functional and tested.

### 9. M-12 — Voting Threshold Documentation
**File:** `agent/voting.py`

**Problem:** `MIN_CONFIDENCE = 0.6` threshold lacked documented rationale for why 60%.

**Fix:**
- Expanded `vote()` method docstring with detailed rationale:
  - 0.6 represents pragmatic balance: ≈2 out of 3 agreement with default `n_runs=3`
  - Below 0.5 would let single-run hallucinations pass through
  - Above 0.7 yields excessive false negatives on legitimate setups
  - Referenced masterplan §8 for consistency

### 10. CON-10 — VotingResult Field Naming
**File:** `agent/voting.py`

**Problem:** `VotingResult` field names didn't clearly map to upstream/downstream data structures.

**Fix:**
- Expanded `VotingResult` dataclass docstring documenting field naming convention:
  - `setup: SetupCandidate | None` — merged from majority SetupCandidates
  - `reason: str` — maps to `AnalysisOutcome.error` when rejected
- `merge()` method: now uses `statistics.median(half_widths)` instead of `cluster[0]` half-width for robustness

---

## Tests Added (20 new)

| # | Test Class | Count | Description |
|---|-----------|-------|-------------|
| 1 | TestFP07MarketHoursTTL | 8 | weekday open, saturday closed, sunday before/after 22:00, friday after 22:00, weekday-only count, weekend span, pending not expired during weekend |
| 2 | TestFP07ExecutingStatus | 4 | mark_executing sets status, executing skipped in zone check, revert_executing restores pending, executing→executed flow |
| 3 | TestFP07RetryConfig | 2 | config has GEMINI_MAX_RETRIES/RETRY_BASE_DELAY, gemini_client references config |
| 4 | TestFP07DefensiveScoreFlags | 2 | empty analyses returns defaults, corrupted structure data doesn't crash |
| 5 | TestFP07PerPairTiming | 1 | phase_timings property exists on Orchestrator |
| 6 | TestFP07VotingThresholdDoc | 3 | min_confidence=0.6, docstring contains threshold keyword, merge uses median half-width |

---

## Existing Tests Updated

- `tests/test_pending_queue.py::test_expire_ttl` — `created_at` set 100h in past (market-hours compatible)
- `tests/test_production_full_cycle.py::test_pending_expiry` — `created_at` set 100h in past (market-hours compatible)

---

## Regression Results

```
825 passed, 8 skipped, 7 warnings in 23.31s
```

Previous (FP-06): 805 passed → **+20 new tests, 0 regressions**
