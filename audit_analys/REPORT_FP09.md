# FP-09 Report: Technical Tools — Structure & Zones

**Phase:** FP-09  
**Status:** ✅ COMPLETE  
**Date:** 2025-07-13  
**Tests:** 33 new FP-09 tests | **882 total passed**, 8 skipped, 7 warnings  
**Files modified:** 7 prod + 3 test  

---

## Summary

FP-09 addresses 15 items across the core technical analysis tools: market structure detection (`structure.py`), swing point detection (`swing.py`), supply/demand zone detection (`supply_demand.py`), and order block detection (`orderblock.py`).

---

## Items Fixed

### H-08 🟠 HIGH — Supply zone freshness: reduce decay aggression
**File:** `tools/supply_demand.py` → `_update_freshness()`  
**Problem:** Old logic marked supply zones as mitigated when price merely **retested** the zone edge (close >= zone low). This was too aggressive — a retest is not mitigation.  
**Fix:** 
- Supply zone mitigated only when `close > zone_high` (price closed **through** the entire zone).
- Demand zone mitigated only when `close < zone_low` (price broke **below** the entire zone).
- Old tests in `test_batch3_fixes.py` updated to reflect correct mitigation threshold.

**Impact:** Many valid zones previously marked `is_fresh=False` are now correctly preserved, increasing available trade setups.

### H-09 🟠 HIGH — Order block: fix body vs wick boundary inconsistency
**File:** `tools/orderblock.py`  
**Problem:** Bullish OBs used full candle range `[prev_low, prev_high]`, but bearish OBs used `[prev_open, prev_high]`. This asymmetry caused bearish OB zones to be tighter than bullish zones, creating a directional bias.  
**Fix:** Both bullish and bearish OBs now use full candle range `[prev_low, prev_high]`.  
**Test:** `TestFP09OBBoundaryH09` — 3 tests verify symmetric boundaries.

### H-10 🟠 HIGH — Structure: add CHOCH detection from ranging state
**File:** `tools/structure.py`  
**Problem:** When `current_trend == "ranging"` (initial state), all breaks were classified as BOS. The first break AGAINST an established trend from ranging might miss CHOCH.  
**Analysis:** Code already handles this correctly — first BOS from ranging sets the trend, subsequent opposing breaks are properly classified as CHOCH.  
**Fix:** Added explicit documentation (docstring + inline comment) explaining the ranging → BOS → CHOCH flow. Added tests verifying the transition chain.  
**Test:** `TestFP09StructureRangingH10` — 3 tests cover ranging-first-break, ranging-then-opposing, and TrendState validation.

### M-13 🟡 — `detect_zones` base candle threshold: extract to config
**File:** `config/settings.py`  
**Status:** Already implemented — `SND_BASE_MIN_CANDLES`, `SND_BASE_MAX_CANDLES`, `SND_BASE_AVG_RANGE_ATR`, `SND_DISPLACEMENT_ATR`, `SND_DISPLACEMENT_BODY_RATIO` all in config. Added `SND_MAX_ZONES` (env var `SND_MAX_ZONES`, default 10).  
**Test:** `TestFP09ConfigThresholdsM13` — 2 tests verify all thresholds accessible.

### M-15 🟡 — Swing detection boundary effect: handle first/last N candles
**File:** `tools/swing.py`  
**Problem:** Core fractal loop `range(lookback, n-lookback)` skips first/last `k` candles, potentially missing significant swings at data boundaries.  
**Fix:** Added `handle_boundary: bool = False` parameter. When True, boundary candles are checked with a reduced adaptive window `k = min(lookback, i, n-1-i)`. Results are deduplicated by index and re-sorted. Default False for backward compatibility.  
**Test:** `TestFP09SwingBoundaryM15` — 3 tests cover default exclusion, boundary detection, and backward compatibility.

### M-16 🟡 — OB scoring: add freshness/age factor
**File:** `tools/orderblock.py`  
**Problem:** OB score was unbounded (`displacement / ATR`) with no age/freshness consideration. No mitigation check existed (all OBs were always `is_mitigated=False`).  
**Fix:**
- Score now includes age factor: `score = (displacement / ATR) × age_factor` where `age_factor = max(0.3, 1.0 - 0.5 × (candle_index / (n-1)))`.
- Added `_update_ob_freshness()` function — marks OBs as mitigated when price later closes through the zone.
- OBs sorted by score descending (best first).
- Added `is_fresh` field (inverse of `is_mitigated`) per CON-02.  
**Test:** `TestFP09OBScoringM16` — 3 tests for age scoring, is_fresh field, and mitigation detection.

### M-17 🟡 — BOS detection threshold: make configurable
**File:** `tools/structure.py`, `config/settings.py`  
**Problem:** `_BOS_ATR_BUFFER = 0.05` was a hardcoded module-level constant.  
**Fix:** Added `BOS_ATR_BUFFER` to config (env var `BOS_ATR_BUFFER`, default 0.05). `detect_bos_choch()` now imports and uses `BOS_ATR_BUFFER` from config.  
**Test:** `TestFP09ConfigThresholdsM13::test_bos_buffer_in_config`

### L-22 🔵 — Supply demand: max zones 10 → configurable
**File:** `tools/supply_demand.py`, `config/settings.py`  
**Problem:** No limit on returned zones — could return dozens of zones for volatile data.  
**Fix:** Added `SND_MAX_ZONES = 10` to config (env var `SND_MAX_ZONES`). `detect_snd_zones()` now accepts `max_zones` parameter and truncates output per type.  
**Test:** `TestFP09MaxZonesL22` — 2 tests verify limit enforcement and config default.

### L-24 🔵 — Swing `lookback=5`: document rationale
**File:** `tools/swing.py`  
**Fix:** Added comprehensive docstring section explaining why `lookback=5` is the default (mid-range compromise for multi-TF), with references to per-TF values in `SWING_LOOKBACK` config.  
**Test:** `TestFP09SwingLookbackDocL24` — 2 tests verify default=5 and docstring content.

### L-25 🔵 — OB docstring: update
**File:** `tools/orderblock.py`  
**Fix:** Rewrote module docstring and `detect_orderblocks()` docstring with comprehensive Args/Returns sections including all new fields.  
**Test:** `TestFP09OBDocstringL25` — verifies Args, Returns, and new field names in docstring.

### L-27 🔵 — Structure return dict: consistent casing
**File:** `tools/structure.py`  
**Analysis:** All keys already snake_case. Verified no inconsistencies.  
**Test:** `TestFP09StructureConsistency::test_all_return_keys_snake_case`

### L-28 🔵 — Supply demand: input validation candle array
**File:** `tools/supply_demand.py`  
**Fix:** Added `_REQUIRED_CANDLE_KEYS = {"open", "high", "low", "close", "time"}` validation. `detect_snd_zones()` raises `ValueError` with descriptive message if first candle is missing required keys. Validation runs before length check (so even single-candle arrays with bad keys are caught).  
**Test:** `TestFP09InputValidationL28` — 3 tests for valid, missing keys (raises ValueError), and empty list.

### CON-11 🔄 — Zone dict keys alignment with Pydantic models
**File:** `tools/supply_demand.py`, `schemas/zones.py`  
**Problem:** SnDZone Pydantic model has `formation` field, but zone dicts omitted it.  
**Fix:** Added `_classify_formation()` helper that determines formation type (rally_base_rally, drop_base_rally, rally_base_drop, drop_base_drop) based on pre-base price direction. All zone dicts now include `formation` field.  
**Test:** `TestFP09ZoneAlignmentCON11` — 3 tests verify formation field presence and valid enum values.

### CON-12 🔄 — OB return format alignment with supply_demand
**File:** `tools/orderblock.py`  
**Problem:** OB dicts lacked `is_fresh`, `displacement_strength`, `body_ratio` fields present in SnD zones.  
**Fix:** Added all three fields to OB dicts. `is_fresh` is the inverse of `is_mitigated`. `displacement_strength` = displacement / ATR. `body_ratio` = body / range of displacement candle.  
**Test:** `TestFP09OBAlignmentCON12` — verifies all shared keys present.

### CON-13 🔄 — Structure trend: consider using Enum
**File:** `schemas/structure.py`, `tools/structure.py`  
**Fix:** Added `TrendState(str, Enum)` with values BULLISH, BEARISH, RANGING to `schemas/structure.py`. `tools/structure.py` imports and uses `TrendState.value` strings for trend field. Returned `trend` field is always a plain string matching enum values.  
**Test:** `TestFP09StructureConsistency` — 3 tests verify enum existence, structure uses enum values, and key casing.

---

## Files Modified

| File | Changes |
|------|---------|
| `config/settings.py` | +`SND_MAX_ZONES`, +`BOS_ATR_BUFFER` (env overridable) |
| `schemas/structure.py` | +`TrendState` enum class |
| `tools/structure.py` | Import BOS_ATR_BUFFER from config, use TrendState.value, ranging→CHOCH documentation |
| `tools/swing.py` | +`handle_boundary` param with edge detection, lookback=5 rationale in docstring |
| `tools/supply_demand.py` | Fixed `_update_freshness` (H-08), +`max_zones` param, input validation, +`formation` field, +`_classify_formation()` helper |
| `tools/orderblock.py` | Bearish OB full candle (H-09), age-adjusted scoring (M-16), +mitigation check, +`is_fresh`/`displacement_strength`/`body_ratio` fields, docstring rewrite |
| `tests/test_batch5_infra.py` | +33 new FP-09 tests across 13 test classes |
| `tests/test_batch3_fixes.py` | Updated 3 tests for new freshness/OB logic |
| `tests/test_orderblock.py` | Updated bearish OB boundary assertion |

---

## Test Results

```
FP-09 tests:  33 passed, 97 deselected in 0.28s
Full regression: 882 passed, 8 skipped, 7 warnings in 21.39s
```

---

## Risk Assessment

- **H-08 (freshness):** Zones that were previously false-mitigated will now remain fresh. This may increase the number of trade setups found. Net positive — more valid confluence opportunities.
- **H-09 (OB boundary):** Bearish OB zones are now wider (include wick below open). This provides more accurate institutional order flow zones.
- **M-16 (OB mitigation):** Some OBs that were always `is_mitigated=False` will now be correctly marked. Downstream scoring benefits from accurate freshness data.
