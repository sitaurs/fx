# FP-11 Report — Scorer, Validator, CHoCH, DXY Gate

**Date:** 2025-06-07
**Items:** 13 (1 High, 3 Medium, 4 Low, 3 Consistency, 1 Dead Code, +D-09 bonus)
**Tests added:** 31 new tests in `tests/test_batch5_infra.py`
**Regression:** 940 passed, 8 skipped, 7 warnings ✅ (was 909)

---

## Files Modified

| File | Changes |
|------|---------|
| `tools/validator.py` | H-12 enforce counter-trend, D-09 remove imports, CON-15 docstring, L-35 doc |
| `tools/scorer.py` | M-23 config-driven threshold, L-37 penalty documentation |
| `tools/dxy_gate.py` | M-18 adaptive window, M-19 feature flag, L-30 threshold doc, CON-16 naming, D-10 disabled doc |
| `tools/choch_filter.py` | L-36 coefficient rationale documentation |
| `config/settings.py` | +DXY_GATE_ENABLED, +DXY_DEFAULT_WINDOW |
| `config/strategy_rules.py` | CON-14 runtime assert, +MIN_CONFLUENCE_SCORE, +SCORER_PENALTY_FLAGS |
| `tests/test_batch3_fixes.py` | Updated F2-13 tests to match H-12 enforcement |
| `tests/test_choch_dxy.py` | +DXY_GATE_ENABLED fixture, +window_used/enabled keys |
| `tests/test_validator.py` | Updated counter-trend test to H-12 behaviour |
| `tests/test_production_full_cycle.py` | +DXY gate patch for feature flag |
| `tests/test_batch5_infra.py` | +31 new FP-11 tests |

---

## Detail per Item

### D-09 💀 Unused imports removed
**File:** `tools/validator.py`
**Problem:** `MIN_RR` and `SL_ATR_MULTIPLIER` imported from `config.settings` but never used — validator already uses `VALIDATION_RULES` from `config.strategy_rules`.
**Fix:** Removed `from config.settings import MIN_RR, SL_ATR_MULTIPLIER`. Added comment explaining the single source of truth.

### H-12 🟠 Counter-trend enforcement
**File:** `tools/validator.py`
**Problem:** `VALIDATION_RULES["must_not_counter_htf"]` was set to `True` but the validator only issued a warning (FIX F2-13 had intentionally demoted it). Config declared "must not" but code allowed it.
**Fix:** Now reads `VALIDATION_RULES["must_not_counter_htf"]` at runtime:
- `True` → counter-trend is a **violation** (trade rejected)
- `False` → counter-trend is a **warning** only (scorer still penalises -3)
**Impact:** With default config (True), buying against bearish H4 bias now fails validation. This aligns with the anti-rungkad philosophy: counter-trend trades are high-risk.
**Tests updated:** test_validator.py, test_batch3_fixes.py F2-13 class — all adjusted to expect violation.

### CON-15 🔄 Validator return standardisation
**File:** `tools/validator.py`
**Problem:** Return dict structure documented only as a single line.
**Fix:** Expanded docstring with full field documentation. Added note that function never raises exceptions — always returns a dict with `passed`, `violations`, `warnings`, `risk_reward`, `sl_atr_distance`.

### CON-14 🔄 Scorer weights sum verification
**File:** `config/strategy_rules.py`
**Problem:** `MAX_POSSIBLE_SCORE` computed dynamically but no verification that it matches the documented value of 14.
**Fix:** Added runtime `assert MAX_POSSIBLE_SCORE == 14` with clear error message. If weights are intentionally changed, the assert message guides developers to update documentation and thresholds.

### M-23 🟡 Scorer penalties to config
**File:** `tools/scorer.py`, `config/strategy_rules.py`
**Problem:** Tradeable threshold `score >= 5` was hardcoded. Penalty flag names existed only implicitly in scorer code.
**Fix:**
- Added `MIN_CONFLUENCE_SCORE = 5` to `config/strategy_rules.py`
- Added `SCORER_PENALTY_FLAGS` list with documentation for each flag
- Scorer now uses `MIN_CONFLUENCE_SCORE` instead of hardcoded 5
- Scorer imports `MIN_CONFLUENCE_SCORE` and `SCORER_PENALTY_FLAGS` from config

### L-35 🔵 Document min_rr default
**File:** `tools/validator.py`
**Problem:** Default `min_rr=1.5` not explained.
**Fix:** Added Note section in docstring: "min_rr defaults to 1.5 per masterplan §5.3. Value sourced from VALIDATION_RULES['min_rr'] — the single source of truth."

### L-37 🔵 Scorer penalty docstring
**File:** `tools/scorer.py`
**Problem:** Penalty section had no documentation beyond a single `# Penalties` comment.
**Fix:** Added comprehensive block comment explaining: what penalties are, how they reduce scores, threshold synchronisation with VALIDATION_RULES, reference to `SCORER_PENALTY_FLAGS` in config.

### M-18 🟡 DXY adaptive correlation window
**File:** `tools/dxy_gate.py`
**Problem:** Fixed `window=48` regardless of market volatility. In high-volatility regimes, a 48-bar window is too slow to detect correlation shifts.
**Fix:** Added `adaptive_window=True` parameter. Computes volatility ratio (recent avg range / long-term avg range):
- vol_ratio > 1.3 (high vol) → window × 0.7 (min 24 bars)
- vol_ratio < 0.7 (low vol) → window × 1.5 (max 96 bars)
- Normal → base window unchanged
Return dict now includes `window_used` field.

### M-19 🟡 DXY enable/disable config
**File:** `tools/dxy_gate.py`, `config/settings.py`
**Problem:** DXY gate was disabled by commenting out code in tool_registry.py. No proper feature flag existed.
**Fix:** Added `DXY_GATE_ENABLED` (env-overridable, default False) to config. When False, `dxy_relevance_score()` returns neutral immediately without computation. Return dict includes `enabled` field for transparency.

### L-30 🔵 DXY threshold documentation
**File:** `tools/dxy_gate.py`
**Problem:** `min_correlation=0.2` threshold undocumented — why 0.2?
**Fix:** Added module-level `_DEFAULT_MIN_CORRELATION` constant with detailed comment: "0.2 is deliberately low — filters only truly uncorrelated pairs. At window=48 H1 bars, |r|<0.2 is statistically indistinguishable from noise at 95% CI."

### L-36 🔵 CHoCH coefficient rationale
**File:** `tools/choch_filter.py`
**Problem:** `_CHOCH_ATR_MULT=0.3` had no documentation explaining the choice.
**Fix:** Added multi-line comment with rationale: 0.1 too permissive (noise), 0.5 too conservative (misses real breaks), 0.3 calibrated on XAUUSD M15 + EURUSD M5 as balance between noise rejection and break capture.

### CON-16 🔄 DXY symbol naming
**File:** `tools/dxy_gate.py`
**Problem:** No standard for how index symbols should be referenced across the codebase.
**Fix:** Added module-level documentation block: "DXY" for US Dollar Index, "JPYX" for synthetic JPY-cross index. Callers must map broker symbols to these canonical names before passing data.

### D-10 💀 DXY gate disabled documentation
**File:** `tools/dxy_gate.py`
**Problem:** DXY disabled in tool_registry but no clear documentation of why or how to re-enable.
**Fix:** Added "Production status" section to module docstring: "DISABLED in tool_registry.py §7.10 — no reliable DXY OHLCV data source available. Function fully implemented and tested; re-enable when feed available. DXY_GATE_ENABLED controls the feature flag."

---

## Config Summary

| Constant | Default | Env Var | Purpose |
|----------|---------|---------|---------|
| `DXY_GATE_ENABLED` | False | `DXY_GATE_ENABLED` | Feature flag for DXY correlation gate |
| `DXY_DEFAULT_WINDOW` | 48 | `DXY_DEFAULT_WINDOW` | Base correlation window (bars) |
| `MIN_CONFLUENCE_SCORE` | 5 | — | Minimum score for tradeable setup |
| `SCORER_PENALTY_FLAGS` | list | — | Canonical list of penalty flag names |

---

## Test Results

```
FP-11 specific: 31 passed, 161 deselected in 0.75s
Full regression: 940 passed, 8 skipped, 7 warnings in 25.79s
```

**Net change:** +31 new tests (940 - 909 = +31)
