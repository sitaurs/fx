# FP-06 Report — AI Client (Context Builder + Gemini Client)

**Date:** 2026-03-08  
**Phase:** FP-06  
**Files modified:** `agent/context_builder.py`, `agent/gemini_client.py`, `agent/orchestrator.py`, `agent/production_lifecycle.py`, `config/settings.py`, `tests/test_batch5_infra.py`  
**Tests:** 13 new FP-06 tests / **805 total regression** (0 failures)

---

## Summary

FP-06 addressed 12 audit items covering the AI analysis pipeline — context
builder, Gemini client, and orchestrator. The most impactful fix was **C-03
CRITICAL**: replacing sequential await with `asyncio.gather()` for true
parallel multi-timeframe data collection (2-3× faster per pair scan).

---

## Fixes Applied

### 1. C-03 CRITICAL — asyncio.gather for parallel TF collection ✅
**Problem:** `collect_multi_tf_async()` stored unawaited coroutines in a dict
then awaited them sequentially in a for-loop. With 3 TFs at ~2-5s each, total
was 6-15s sequential per pair.

**Fix:** Replaced with `asyncio.gather(*tasks, return_exceptions=True)`. All 3
timeframes now execute concurrently. Expected improvement: 2-3× faster scan per pair.

**Also fixed:** `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (Python 3.10+ deprecation).

**File:** `agent/context_builder.py` lines 370-400

### 2. H-06 HIGH — Daily budget blocking ✅
**Problem:** `GeminiClient` only logged a warning when budget exceeded. API calls
continued normally, risking unexpected billing.

**Fix:** Added `BudgetExceededError` exception class. All 4 generation methods
(`generate`, `generate_structured`, `agenerate`, `agenerate_structured`) call
`_check_budget()` before making API requests. Raises `BudgetExceededError` if
`total_cost >= daily_budget`.

**File:** `agent/gemini_client.py`

### 3. H-07 HIGH — Parse error granular handling ✅
**Problem:** Orchestrator `_phase_analyze` caught all exceptions with bare
`except Exception` and returned None silently. Programming errors like
`KeyError` were swallowed alongside transient API errors.

**Fix:** Split into:
- `BudgetExceededError` → re-raised (not swallowed)
- `json.JSONDecodeError` / `ValueError` → logged with response text preview (first 200 chars)
- Other exceptions → logged with pair name

Also applied same pattern to `_phase_voting` and `_phase_output`.

**File:** `agent/orchestrator.py`

### 4. M-09 — Fallback model warning ✅
**Problem:** `model_for_state()` silently fell back to Flash model for unknown states.

**Fix:** Added explicit check — logs warning with state name when falling back.

**File:** `agent/gemini_client.py` `model_for_state()`

### 5. M-10 — Truncation markers in format_context ✅
**Problem:** SNR levels truncated to 6, OBs to 3 per direction, without
indicating that more data exists.

**Fix:** Added `[showing top N of M]` markers when truncation occurs.

**File:** `agent/context_builder.py` `format_context()`

### 6. L-15 — Analysis timeframes from config ✅
**Problem:** Orchestrator hardcoded `["H4", "H1", "M15"]`.

**Fix:** Added `ANALYSIS_TIMEFRAMES` to `config/settings.py` (env-configurable
via `ANALYSIS_TIMEFRAMES=H4,H1,M15`). Orchestrator imports and uses it as default.

**Files:** `config/settings.py`, `agent/orchestrator.py`

### 7. L-20 — Empty analyses warning ✅
**Problem:** `format_context()` silently returned empty-ish data when analyses
dict was empty or all timeframes had errors.

**Fix:** Added warning log for empty dict and all-errors conditions. Returns
explicit "No data available" message for empty input.

**File:** `agent/context_builder.py` `format_context()`

### 8. D-05 — reset_daily_cost integration ✅
**Problem:** `GeminiClient.reset_daily_cost()` existed but was never called.
Cost counters accumulated indefinitely, making `budget_exceeded` permanently
True after enough usage.

**Fix:** Added call to `self._gemini.reset_daily_cost()` in
`ProductionLifecycle.reset_daily()` (runs at start of each trading day).

**File:** `agent/production_lifecycle.py` `reset_daily()`

### 9. L-14 — Token count documentation ✅
**Problem:** Audit mentioned a "÷4 heuristic" for token estimation that needed
documenting.

**Finding:** No such heuristic exists in code. Token counts come from
`response.usage_metadata` (exact API counts). Added clarifying comment.

**File:** `agent/gemini_client.py` `_COST_PER_1M` docstring

---

## Items Verified Already Implemented (No Change Needed)

| ID | Finding |
|----|---------|
| L-13 | Model names already in config: `GEMINI_PRO_MODEL`, `GEMINI_FLASH_MODEL` |
| CON-07 | All model references use `model_for_state()` which reads from config |
| CON-08 | `analyze_timeframe` output keys already consistent snake_case |

---

## New Tests (13)

| Test | Class | Validates |
|------|-------|-----------|
| `test_collect_uses_gather_parallel` | `TestFP06AsyncGather` | asyncio.gather parallel execution |
| `test_collect_handles_exception_in_one_tf` | `TestFP06AsyncGather` | One TF failure doesn't block others |
| `test_budget_exceeded_blocks_generate` | `TestFP06BudgetBlocking` | generate() raises BudgetExceededError |
| `test_budget_exceeded_blocks_agenerate_structured` | `TestFP06BudgetBlocking` | agenerate_structured raises on budget |
| `test_budget_not_exceeded_allows_call` | `TestFP06BudgetBlocking` | Normal calls proceed within budget |
| `test_known_state_returns_correct_model` | `TestFP06ModelFallbackWarning` | Known states return correct model |
| `test_unknown_state_falls_back_to_flash` | `TestFP06ModelFallbackWarning` | Unknown state falls back with warning |
| `test_reset_daily_calls_gemini_reset` | `TestFP06ResetDailyCostIntegration` | reset_daily() calls gemini reset |
| `test_analysis_timeframes_in_config` | `TestFP06AnalysisTimeframesConfig` | Config has ANALYSIS_TIMEFRAMES |
| `test_orchestrator_uses_config_default` | `TestFP06AnalysisTimeframesConfig` | Orchestrator uses config default |
| `test_empty_analyses_returns_no_data` | `TestFP06FormatContextEdgeCases` | Empty input handled gracefully |
| `test_all_errors_returns_error_blocks` | `TestFP06FormatContextEdgeCases` | All-error analysis generates output |
| `test_truncation_marker_snr` | `TestFP06FormatContextEdgeCases` | Truncation markers displayed |

---

## Regression

```
805 passed, 8 skipped, 7 warnings in 25.14s
```
