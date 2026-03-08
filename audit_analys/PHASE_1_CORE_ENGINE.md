# Phase 1 Audit: Core Engine

**Scope:** `main.py` (735 lines), `agent/production_lifecycle.py` (1797 lines), `agent/trade_manager.py` (485 lines), `agent/pending_manager.py` (323 lines), `agent/orchestrator.py` (585 lines)

**Total Lines Reviewed:** 3,925

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 2 |
| 🟠 HIGH | 5 |
| 🟡 MEDIUM | 8 |
| 🔵 LOW | 12 |
| ⚪ Dead Code | 4 |
| 📝 Consistency | 6 |

---

## 🔴 CRITICAL Issues

### C-01: `_emergency_save_sync` hardcodes DB path
**File:** `main.py` line ~658
**Code:**
```python
conn = sqlite3.connect("data/forex_agent.db", timeout=10)
```
**Problem:** Path is hardcoded instead of using a configurable constant from settings. If the DB path ever changes (e.g. moved to `/opt/data/` on VPS), this emergency save silently writes to the wrong location, resulting in data loss on crash.
**Fix:** Import `DB_PATH` from `config.settings` or share a module-level constant.

### C-02: `_pip_value_per_lot` incomplete for cross pairs
**File:** `agent/production_lifecycle.py` lines 534-556
**Problem:** Only handles: XAUUSD, xxxUSD (EURUSD/GBPUSD), USDxxx (USDJPY/USDCHF), xxxJPY (GBPJPY). All other cross pairs (EURGBP, AUDCAD, NZDCHF, etc.) fall through to `return 10.0` which is incorrect. EURGBP pip value ≈ $12.70 (depends on GBP/USD rate), AUDCAD ≈ $7.20 (depends on USD/CAD).
**Impact:** With current MVP_PAIRS (EURUSD, GBPUSD, USDJPY, GBPJPY, XAUUSD) this is **not currently triggering**. But adding pairs like EURGBP or AUDNZD will produce wrong position sizing — potentially 20-30% off.
**Fix:** Add handlers for quote-currency conversion via intermediate rates:
```python
# Crosses not ending USD or JPY: convert via quote currency
quote_ccy = pair[3:]
usd_pair = f"USD{quote_ccy}"
rate = get_current_price(usd_pair)  # e.g. USDGBP for EURGBP
return (100_000 * point) / rate if rate > 0 else 10.0
```

---

## 🟠 HIGH Issues

### H-01: `check_drawdown()` called without `price_cache` in `_close_trade`
**File:** `agent/production_lifecycle.py` line ~1706
```python
# 5. Check drawdown
self.check_drawdown()   # ← no price_cache!
```
**Problem:** After closing a trade, `check_drawdown()` is called without pre-fetched prices. If other active trades exist, `_unrealised_pnl()` falls back to **sync** `get_current_price()` HTTP calls. This happens inside `_trade_lock` (the call site in `check_active_trades` holds the lock), causing potential event loop blocking.
**Fix:** Pass the already-fetched `prices` dict down to `_close_trade`, or call `check_drawdown(price_cache=prices)`.

### H-02: `_emergency_save_sync` duplicates trade serialization logic
**File:** `main.py` lines 638-670
**Problem:** The trade serialization dict is a copy-paste from `production_lifecycle.py:save_active_trades()`. If `ActiveTrade` fields change, both copies must be updated independently. This already has divergence potential — `save_active_trades` uses `getattr()` for many fields while the sync version doesn't always match.
**Fix:** Extract a `trade_to_dict(trade: ActiveTrade) -> dict` utility function used by both.

### H-03: TP hit P/L uses TP level but `exit_price` in journal is monitoring price
**File:** `agent/production_lifecycle.py` lines 1532-1555
**Problem:** For TP1_HIT/TP2_HIT results, P/L is calculated from the TP level (assumes perfect fill), but `close_result["exit_price"]` is the monitoring loop's actual price. This creates a discrepancy in the trade journal — the exit_price shown doesn't match the P/L recorded.
**Example:** TP1=1.08000, monitoring catches at 1.08050 → journal shows exit=1.08050 but P/L is based on 1.08000.
**Fix:** Set `close_result["exit_price"] = tp_price` for TP hits, or add a separate `fill_price` field.

### H-04: `update_runtime_config` has no input validation
**File:** `agent/production_lifecycle.py` lines 692-745
**Problem:** Dashboard sends config updates as JSON. Each `float()`, `int()`, `bool()` conversion can throw `ValueError`/`TypeError`. If one field fails, all subsequent updates in the same request are skipped (no try/except per field). A malicious or buggy dashboard request could crash the config update.
**Fix:** Wrap each field update in individual try/except, or validate input schema before processing.

### H-05: `scan_batch` ignores pending queue for correlation filter
**File:** `main.py` lines 185-192
```python
if _lifecycle:
    for active_pair in _lifecycle.active_pairs:
        for group, members in CORRELATION_GROUPS.items():
            if active_pair in members:
                selected_groups.add(group)
```
**Problem:** Only checks `_lifecycle.active_pairs` (open trades) for correlation conflicts. Doesn't check `_lifecycle.pending_pairs`. This means: EURUSD is in pending queue → scan_batch opens GBPUSD (same correlation group "major_eur_gbp") → if EURUSD pending then executes, you have two correlated trades.
**Fix:** Also iterate `_lifecycle.pending_pairs` when building `selected_groups`.

---

## 🟡 MEDIUM Issues

### M-01: `save_active_trades` uses excessive `getattr()` for defined fields
**File:** `agent/production_lifecycle.py` lines 275-310
**Problem:** Many `getattr(trade, "field_name", default)` calls for fields that ARE defined on the `ActiveTrade` dataclass (e.g., `partial_closed`, `sl_moved_to_be`, `trail_active`). This pattern suggests the serialization was written before the dataclass was finalized and hasn't been cleaned up.
**Impact:** Low risk but maintenance burden. A field rename won't produce an error — it'll silently use the default.
**Fix:** Direct attribute access: `trade.partial_closed` instead of `getattr(trade, "partial_closed", False)`.

### M-02: `restore_active_trades` doesn't restore all trade fields
**File:** `agent/production_lifecycle.py` lines 320-380
**Missing fields:** `entry_zone_low`, `entry_zone_high`, `recommended_entry`, `entry_zone_type`, `original_sl`. After restart, these fields default to 0/empty. `original_sl` defaults in `__post_init__` to current `stop_loss`, which may have been moved (BE, trailing). This means PostMortem after restart calculates wrong initial risk.
**Fix:** Serialize and restore all ActiveTrade fields including `original_sl`, `entry_zone_*`.

### M-03: `_cent_sl_multiplier` and `_cent_tp_multiplier` not in `__init__`
**File:** `agent/production_lifecycle.py`
**Problem:** These attributes are only created when `_apply_challenge_mode("challenge_cent")` is called. All other code accesses them via `getattr(self, "_cent_sl_multiplier", CHALLENGE_CENT_SL_MULTIPLIER)`. If the constants aren't imported, this silently uses a NameError fallback. Fragile pattern.
**Fix:** Initialize both in `__init__`:
```python
self._cent_sl_multiplier = CHALLENGE_CENT_SL_MULTIPLIER
self._cent_tp_multiplier = CHALLENGE_CENT_TP_MULTIPLIER
```

### M-04: `evaluate()` structure_ok parameter is effectively dead
**File:** `agent/trade_manager.py` line ~202
**Problem:** The `structure_ok` parameter drives the CLOSE_MANUAL check in `evaluate()`. But the lifecycle never calls `evaluate()` with `structure_ok=False`. Instead, revalidation results are handled separately in `check_active_trades()` where invalidation creates a CLOSE_MANUAL action directly *before* calling `evaluate()`. So the `evaluate()` structure check is unreachable dead logic.
**Impact:** Not a bug (same outcome is achieved), but confusing — the docstring says this is used but it never is.

### M-05: `news_imminent` and `last_swing_against` parameters never populated
**File:** `agent/trade_manager.py` lines ~200, 290
**Problem:** `evaluate()` accepts `news_imminent` and `last_swing_against` but the lifecycle's `check_active_trades()` never passes these values. News checking and swing-point detection are not implemented in the pipeline:
- No Finnhub news endpoint is called (Finnhub is for quotes only)
- No swing detection is run during monitoring
**Impact:** Missing features documented in masterplan §13 (rules 6-7).

### M-06: Orchestrator state transitions are ceremonial
**File:** `agent/orchestrator.py` lines 170-185
**Problem:** State transitions `SCANNING → WATCHING → APPROACHING → TRIGGERED` are done in rapid succession within the same `run_scan()` call. The state machine supports these transitions but they happen instantly — there's no actual multi-phase computation that differs between states. The only real effect is that `model_for_state()` maps TRIGGERED to Gemini Pro and others to Flash.
**Impact:** The state machine is architecturally sound but currently underutilized.

### M-07: `_phase_vote` thinking levels hardcoded and asymmetric
**File:** `agent/orchestrator.py` line ~440
```python
thinking_levels = ["high", "low", "high"]
```
**Problem:** Hardcoded to 3 levels, but `VOTING_RUNS` (default=3) could change. The loop runs `VOTING_RUNS - 1` additional calls (2 calls), indexing `thinking_levels[i % 3]`. If VOTING_RUNS is changed to 5, the pattern repeats ["high", "low", "high", "high"]. Should be a configurable constant.

### M-08: `_phase_output` fallback plan confidence
**File:** `agent/orchestrator.py` lines 505-520
**Problem:** When Gemini API fails for output generation, the fallback plan sets `confidence=MIN_CONFIDENCE` (a fixed floor value) regardless of the actual voting result's confidence. A high-confidence voted setup gets reported as low confidence.
**Fix:** Use `vr.confidence` or `initial.confluence_score / 10.0` instead.

---

## 🔵 LOW Issues

### L-01: `_scan_locks` dict grows unbounded
**File:** `main.py` line ~123
**Impact:** Negligible with 6 pairs. One Lock object per pair, never cleaned up.

### L-02: `_delayed_first_scan` has 90-minute gap (12:00-13:30 WIB)
**File:** `main.py` lines 575-595
**Problem:** If bot restarts at 12:30 WIB, no initial scan runs. This is intentional (no active session) but undocumented. The 12:00-13:30 gap between Asian and London sessions is a valid choice.

### L-03: `@app.on_event("startup")` is deprecated
**File:** `main.py` line ~430
**Problem:** FastAPI deprecated `on_event` in favor of `lifespan` context manager. Works fine in current version but will eventually be removed.

### L-04: `daily_wrapup` doesn't cancel pending setups
**File:** `main.py` lines ~365-420
**Problem:** Stale orchestrators are cancelled at wrap-up, but pending setups remain active. They'll expire via TTL (max 4 hours) but could theoretically trigger overnight if price enters zone before expiry.

### L-05: `daily_wrapup` can fire twice
**File:** `agent/production_lifecycle.py` `daily_wrapup()`
**Problem:** No guard against double execution. If triggered manually AND by scheduler, WA notification sends twice. Stats are idempotent (from DB) so no data corruption.

### L-06: `restore_from_list` silently skips expired setups
**File:** `agent/pending_manager.py` line ~296
**Problem:** No log message when expired setups are skipped during restore. Adds debugging difficulty.

### L-07: `_phase_analyze` error handling
**File:** `agent/orchestrator.py` lines ~345-380
**Problem:** If `collect_multi_tf_async` succeeds but `agenerate_structured` fails, the entire phase returns None. No intermediate state is saved (the raw tool data is stored in `_last_analyses` but never persisted). A retry of the same pair will re-run all tools.

### L-08: `SL_ATR_MULTIPLIER` imported but unused
**File:** `agent/trade_manager.py` line 33
**Import:** `from config.settings import (..., SL_ATR_MULTIPLIER, ...)`
**Usage:** None. The trailing SL logic uses hardcoded `0.5 * atr` and `1.0 * atr`.

### L-09: `atr_proxy` calculation in voting is approximate
**File:** `agent/orchestrator.py` line 454
```python
atr_proxy = abs(initial.entry_zone_high - initial.entry_zone_low) / 0.3
```
**Problem:** Assumes entry zone is ~30% of ATR. The actual ratio varies. VotingEngine uses ATR for price tolerance comparison, so inaccurate ATR affects vote agreement calculations.

### L-10: `_pip_value_per_lot` calls sync `get_current_price`
**File:** `agent/production_lifecycle.py` line ~540
**Problem:** Used for USDXXX and xxxJPY pairs when `pair_price`/`usd_jpy` not provided. Can block event loop. Currently mitigated because `_compute_lot_and_risk` calls it without async but it's called from async `_open_trade`.

### L-11: `_open_trade` TP2 defaults to TP1 when None
**File:** `agent/production_lifecycle.py` line ~1225
```python
plan_tp2 = s.take_profit_2 if s.take_profit_2 else s.take_profit_1
```
**Problem:** If TP2 is intentionally `0.0` (falsy), this defaults to TP1. Should use `is not None` check.

### L-12: `on_shutdown` doesn't await lifecycle.save_active_trades fully
**File:** `main.py` lines ~685-715
**Problem:** `save_active_trades` is awaited but if it raises, the shutdown continues without the save. The error is logged but trades could be lost on clean shutdown.

---

## ⚪ Dead Code

### D-01: `MonitoringReport` and `generate_monitoring_report`
**File:** `agent/trade_manager.py` lines 430-485
**Problem:** Defined but never called from any module. The monitoring logic is handled by `ProductionLifecycle.check_active_trades()` + `TradeManager.evaluate()` directly.
**Recommendation:** Remove or integrate into a dashboard endpoint for real-time monitoring display.

### D-02: `SL_ATR_MULTIPLIER` import
**File:** `agent/trade_manager.py` line 33
**Problem:** Imported from config but never referenced. Trail SL uses hardcoded multipliers.

### D-03: `STATE_INTERVALS` import
**File:** `main.py` line 37
```python
from config.settings import (..., STATE_INTERVALS, ...)
```
**Problem:** Imported but never used in main.py. Was likely intended for state-specific polling intervals.

### D-04: `_started` guard variable
**File:** `main.py` line 92
```python
_started: bool = False
```
**Problem:** Declared but never used. The `app.state._agent_started` attribute is used instead for double-startup protection.

---

## 📝 Consistency Issues

### CON-01: Two P/L calculation paradigms
- `ActiveTrade.floating_pnl()` → price difference (raw)
- `ProductionLifecycle.trade_floating_pnl()` → dollar amount (pip-based)
Both are correct for their context but naming is confusing. Suggest renaming to `floating_price_diff` and `floating_pnl_usd`.

### CON-02: `total_drawdown` halt never auto-lifts
**File:** `agent/production_lifecycle.py` `reset_daily()` line 1791
- Daily halt: auto-lifts on new trading day ✅
- Total halt: persists forever until manual dashboard intervention ❌ documented
**Observation:** This is intentional (total drawdown = serious) but undocumented. Dashboard has a way to unhalt via `update_runtime_config(drawdown_guard_enabled=False)`.

### CON-03: `AnalysisState.ACTIVE` and `CLOSED` are defined in state_machine but unused by orchestrator
**File:** `agent/orchestrator.py`
**Problem:** The state machine defines ACTIVE and CLOSED states but the orchestrator never transitions to them. Trade management is handled by ProductionLifecycle independently.

### CON-04: Trade close `reason` field varies in format
**Across files:**
- TradeManager returns: `"Stop loss hit"`, `"TP2 hit — full close"`, `"H1 structure break..."` 
- Lifecycle returns: `"Manual close from dashboard"`, `"Setup invalidated on periodic recheck..."`
- No standardized reason format/enum for analytics.

### CON-05: `challenge_mode` doesn't persist `_cent_sl/tp_multiplier`
**File:** `agent/production_lifecycle.py`
**Problem:** `save_state()` persists `challenge_mode` string but not the actual `_cent_sl_multiplier` and `_cent_tp_multiplier` values. After restart, they're recalculated from config defaults via `_apply_challenge_mode()`, losing any dashboard changes.

### CON-06: `CORRELATION_GROUPS` used in main.py but not defined in production_lifecycle.py
**Problem:** Correlation filtering is done in `main.py:scan_batch()` but lifecycle has no awareness of correlation groups. The `can_open_trade()` method doesn't check correlations — it only checks drawdown and max concurrent. This means manual force-execution or pending queue execution bypasses correlation filtering.

---

## Architecture Observations

### Strengths
1. **Clean separation**: Orchestrator handles analysis, Lifecycle handles trades, TradeManager handles SL/TP rules. Well-defined boundaries.
2. **Robust persistence**: Active trades and pending setups survive restarts via SQLite. Emergency save handles crash scenarios.
3. **Correlation filter**: scan_batch cherry-picks by score with correlation group awareness — prevents correlated exposure.
4. **Lock discipline**: `_trade_lock` prevents concurrent opens; `_scan_locks` prevent concurrent scans for same pair.
5. **Challenge modes**: Cent and extreme modes correctly modify position sizing and disable drawdown guards.

### Areas for Improvement
1. **Sync HTTP in async context**: Several code paths still call `get_current_price()` (sync) from async functions. The `_prefetch_prices` pattern is the right solution but not consistently applied.
2. **Feature gaps**: News monitoring and swing-level tracking from masterplan §13 are stubbed but not implemented.
3. **Testing surface**: The 5 core files are the most critical but also the hardest to unit-test due to tight coupling with external APIs (OANDA, Gemini, WA).
4. **Error propagation**: Many errors are logged and swallowed (`except Exception: pass`). While this prevents cascade failures, it also hides problems.

---

*Audit completed: 2026-03-07*
*Files reviewed: 5 | Lines reviewed: 3,925 | Issues found: 37*
