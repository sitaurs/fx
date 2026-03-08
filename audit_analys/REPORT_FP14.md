# REPORT FP-14: Schemas

**Tanggal:** 2025-07-12 (updated 2026-03-08)  
**Status:** ✅ COMPLETE  
**Items:** 10 (3 LOW + 7 remaining added in second pass)  
**Files modified:** 4 production, 2 test  
**New tests:** 30 (16 first pass + 14 second pass)  
**Regression:** 1043 passed, 8 skipped, 7 warnings ✅

---

## Summary

FP-14 adds input validation and proper typing to Pydantic schema models — preventing invalid AI outputs from propagating through the system.

---

## Items Fixed — First Pass

| ID | File | Fix |
|----|------|-----|
| L-53 | `schemas/plan.py` | Added `_VALID_HTF_BIAS` set (`{"bullish", "bearish", "range", "ranging"}`) + `@model_validator` on `TradingPlan` that rejects invalid values. Uses set+validator pattern (not Enum) to stay compatible with google-genai Structured Output SDK. |
| L-54 | `schemas/plan.py` | Added `0.0 <= confidence <= 1.0` check in the same `_check_plan_bounds` model_validator on `TradingPlan`. |
| L-55 | `schemas/market_data.py` | Changed `MarketStructure.events` from `list[dict]` to `list[StructureEvent]`. Direct import from `schemas.structure` (no circular dependency). Pydantic auto-coerces dicts to StructureEvent objects. |

## Items Fixed — Second Pass

| ID | File | Fix |
|----|------|-----|
| L-56 | `schemas/market_data.py` | Documented `Candle.volume` default=0.0 — forex tick volume; 0.0 means unavailable, not zero activity. |
| L-57 | `schemas/zones.py` | `SnDZone.zone_type` → `ZoneType` enum; added `OBType` enum for `OrderBlock`; added `PoolType` enum for `LiquidityPool`. All three now use typed enums instead of plain `str`. |
| CON-23 | `schemas/plan.py` | `confluence_score` validator changed from hardcoded `0-15` → `0-MAX_POSSIBLE_SCORE` (currently 14). Description and error message are dynamic. Imported `MAX_POSSIBLE_SCORE` from `config.strategy_rules`. |
| CON-24 | `schemas/zones.py` | Module docstring expanded to document dual-Zone relationship: `market_data.Zone` (generic/lightweight) vs `zones.SnDZone` (detailed/canonical schema for validation). |
| CON-27 | — | Redundant with L-55 — `MarketStructure.events` already typed as `list[StructureEvent]`. Marked done. |
| D-13 | `schemas/structure.py` | Module docstring updated — now states "Actively used" since `StructureEvent` is imported by `market_data.py` (L-55). |
| D-14 | `schemas/zones.py` | Module docstring expanded with usage intent: canonical schema docs, optional runtime validation, future typed-return migration path. |

---

## Test Coverage

### First Pass — 16 tests:

| Test Class | Tests | Validates |
|-----------|-------|-----------|
| TestL53HtfBiasValidator | 7 | bullish/bearish/range/ranging accepted; neutral/empty/mixed rejected |
| TestL54ConfidenceBounds | 5 | 0.0, 1.0, 0.75 accepted; -0.1 and 1.5 rejected |
| TestL55MarketStructureEvents | 4 | Empty ok, dict→StructureEvent coercion, object accepted, invalid rejected |

### Second Pass — 14 tests:

| Test Class | Tests | Validates |
|-----------|-------|-----------|
| TestL56CandleVolume | 2 | Default 0.0, docstring present |
| TestL57ZoneTypeEnum | 4 | SnDZone→ZoneType, invalid rejected, OB→OBType, LP→PoolType |
| TestCON23ConfluenceScore | 3 | Max accepted, above max rejected, description matches |
| TestCON24DualZone | 1 | Module docstring documents relationship |
| TestCON27EventsTyped | 1 | Dict coerced to StructureEvent |
| TestD13StructureUsed | 2 | Docstring + import chain verified |
| TestD14ZonesUsageIntent | 1 | Docstring has usage intent |

### Existing Test Fixed:

| File | Change | Reason |
|------|--------|--------|
| `tests/test_batch2_fixes.py` | `test_edge_score_fifteen_accepted` → `test_edge_score_max_accepted` + `test_edge_score_above_max_rejected` | CON-23: max is now 14, not 15 |

---

## Regression

```
After first pass:   1008 passed, 8 skipped
After second pass:  1043 passed, 8 skipped  (+35 from FP-15 + FP-14 second pass)
```

Zero regressions.
