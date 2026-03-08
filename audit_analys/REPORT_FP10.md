# FP-10 Report — Technical Tools: Indicators, Liquidity, SNR, PriceAction, Trendline

**Date:** 2025-06-07
**Items:** 12 (4 Medium, 5 Low, 1 Consistency, 1 Dead Code, 1 additional)
**Tests added:** 31 new tests in `tests/test_batch5_infra.py`
**Regression:** 909 passed, 8 skipped, 7 warnings ✅ (was 882)

---

## Files Modified

| File | Changes |
|------|---------|
| `config/settings.py` | +7 new config constants |
| `tools/indicators.py` | +EMA cache, +`detect_rsi_divergence()` |
| `tools/snr.py` | +`pair` param, pair-adaptive clustering, highest-TF source_tf |
| `tools/price_action.py` | Config extraction, +`min_body_ratio` for engulfing |
| `tools/liquidity.py` | Config extraction for tolerance |
| `tools/choch_filter.py` | O(n²)→O(n) prefix array optimization |
| `tools/scorer.py` | Comprehensive return dict documentation |
| `tools/trendline.py` | +max_ray_bars, +logging, +type field, -is_touch_valid |
| `tests/test_trendline.py` | Removed `TestIsTouchValid` class |
| `tests/test_batch5_infra.py` | +31 new FP-10 tests |

---

## Detail per Item

### M-14 🟡 RSI Divergence: ATR-scaled lookback
**File:** `tools/indicators.py`
**Problem:** No RSI divergence detection function existed. Strategy rules reference `rsi_divergence` as an anti-rungkad check but no implementation existed.
**Fix:** Added `detect_rsi_divergence()` function that:
- Detects bearish divergence (price HH + RSI LH) and bullish divergence (price LL + RSI HL)
- Uses ATR-adaptive lookback: when current ATR exceeds median range (high-volatility regime), lookback shortens by 30% to respond faster to momentum exhaustion
- Scans local peaks/troughs in the lookback window using neighbor comparison
- Returns `divergence_type`, `price_pivot_idx`, `rsi_pivot_idx`, `lookback_used`
**Config added:** `RSI_DIVERGENCE_LOOKBACK` (env-overridable, default 10)
**Tests:** 6 tests covering bearish/bullish/aligned/ATR-scaling/edge cases

### M-20 🟡 SNR Clustering: Pair-adaptive tolerance
**File:** `tools/snr.py`, `config/settings.py`
**Problem:** SNR clustering used a fixed `cluster_atr_mult=0.2` for all pairs. XAUUSD ($2000+ price) needs wider tolerance than EURUSD (~1.08) because absolute price swings are larger even at the same ATR multiple.
**Fix:**
- Added `SNR_CLUSTER_PAIR_MULT` dict in config: XAUUSD=0.30, JPY crosses=0.25, others=0.20
- Added `pair: str = ""` parameter to `detect_snr_levels()`
- When `cluster_atr_mult=None` (default), uses pair-specific value; explicit override still works
- **Bonus:** Changed `source_tf` from most-frequent to highest-hierarchical TF (a level with 5×M15 + 1×H4 now correctly shows `source_tf="H4"` instead of `"M15"`)
**Tests:** 3 tests covering pair-adaptive behavior, explicit override, source_tf fix

### M-21 🟡 Pin Bar Body Ratio: Extract to config
**File:** `tools/price_action.py`, `config/settings.py`
**Problem:** `min_wick_body_ratio=2.0` was hardcoded as parameter default. Tuning required code changes.
**Fix:** Added `PIN_BAR_MIN_WICK_RATIO` to config (env-overridable, default 2.0). Function default now reads from config.
**Tests:** 2 tests verifying config linkage and value validity

### M-22 🟡 Trendline Ray Extension: Validity bounds
**File:** `tools/trendline.py`, `config/settings.py`
**Problem:** A trendline anchored at bars 0-10 could be extended as a ray to bar 500+, making it unreliable as price has long moved past the original structure.
**Fix:** Added `max_ray_bars` parameter (default from `TRENDLINE_MAX_RAY_BARS` config, default 100). If `last_bar_idx - anchor_2.idx > max_ray_bars`, the candidate is rejected with a debug log message.
**Config added:** `TRENDLINE_MAX_RAY_BARS` (env-overridable, default 100)
**Tests:** 3 tests covering within-bounds, beyond-bounds rejection, config linkage

### L-23 🔵 EMA: Cache computed values
**File:** `tools/indicators.py`
**Problem:** EMA was recomputed from scratch every call, even for identical data+period in the same analysis cycle. The orchestrator calls EMA multiple times per pair per scan.
**Fix:** Added module-level `_ema_cache` dict with lightweight fingerprint key `(n, period, first_close, mid_close, last_close)`. On cache hit, returns the exact same dict object. Cache bounded to 64 entries, auto-clears on overflow. `clear_ema_cache()` function exposed for explicit cleanup between cycles. `use_cache=True` parameter allows opt-out.
**Tests:** 4 tests covering cache hit, no collision, bypass, clear

### L-26 🔵 Liquidity EQH/EQL Tolerance: Extract to config
**File:** `tools/liquidity.py`, `config/settings.py`
**Problem:** `tolerance_atr_mult=0.15` was hardcoded as parameter default.
**Fix:** Added `LIQUIDITY_EQ_TOLERANCE_ATR` to config (env-overridable, default 0.15). Function default now reads from config.
**Tests:** 1 test verifying config linkage

### L-29 🔵 ChoCH Filter: O(n²)→O(n) prefix optimization
**File:** `tools/choch_filter.py`
**Problem:** `detect_choch_micro()` recomputed `max(c["high"] for c in segment[:i])` and `min(c["low"] for c in segment[:i])` for every bar iteration — O(n) per bar × O(n) bars = O(n²). While n is small (~10 default lookback), this is suboptimal.
**Fix:** Precompute `prefix_high` and `prefix_low` arrays in one O(n) pass. Each bar lookup is now O(1). numpy is NOT needed — pure Python prefix arrays are sufficient and avoid a heavy dependency for a simple algorithm.
**Tests:** 3 tests verifying bullish/bearish detection still works + no false positive

### L-32 🔵 Engulfing: Body significance check
**File:** `tools/price_action.py`, `config/settings.py`
**Problem:** Engulfing detection accepted any body that engulfed the previous, regardless of significance. A tiny-body candle with huge wicks could qualify as engulfing despite being a weak, deceptive pattern.
**Fix:** Added `min_body_ratio` parameter (body/range). Engulfing candle must have body ≥ 30% of its full range (configurable via `ENGULFING_MIN_BODY_RATIO`, env-overridable). Filters out pin-bar-like candles that technically engulf but lack conviction.
**Config added:** `ENGULFING_MIN_BODY_RATIO` (default 0.3)
**Tests:** 3 tests covering strong pass, weak reject, config linkage

### L-33 🔵 Trendline: Failure logging
**File:** `tools/trendline.py`
**Problem:** Rejected trendline candidates produced no diagnostic output, making it difficult to debug why expected trendlines weren't detected.
**Fix:** Added `logging.getLogger(__name__)` and DEBUG-level log messages for each rejection reason: span too short, wrong slope direction, exceeded max_ray_bars, broken ray. Messages include anchor indices and rejection details.
**Tests:** 1 test verifying logger.debug is called on rejection

### L-34 🔵 Scorer Return: Document dict structure
**File:** `tools/scorer.py`
**Problem:** The return dict structure was documented as "Dict with score (int), breakdown (dict), tradeable (bool)" — too terse for downstream consumers.
**Fix:** Expanded docstring Returns section with full field descriptions: score (clamped int, weight source reference), breakdown (per-flag dict, active=weight/inactive=0, penalties negative), tradeable (bool, threshold note), max_possible (int, formula).
**Tests:** 2 tests verifying all documented keys exist with correct types + all flag keys present

### CON-17 🔄 Trendline Return Format: Chart overlay alignment
**File:** `tools/trendline.py`
**Problem:** Trendline output had `direction` field ("uptrend"/"downtrend") but chart overlay code expects a `type` field with "support"/"resistance" semantics.
**Fix:** Added `"type": "support"` for uptrend lines and `"type": "resistance"` for downtrend lines. Both `direction` and `type` are now present for backward compatibility.
**Tests:** 2 tests verifying uptrend→"support" and downtrend→"resistance"

### D-08 💀 `is_touch_valid()`: Remove dead code
**File:** `tools/trendline.py`, `tests/test_trendline.py`
**Problem:** `is_touch_valid()` used static tolerance while main `detect_trendlines()` uses ATR-adaptive tolerance. Function was never called in production (only in tests). Keeping it risked future misuse with inconsistent tolerance.
**Fix:** Removed function entirely. Added a comment noting removal and that `detect_trendlines()` handles touch validation internally with ATR-adaptive tolerance. Removed `TestIsTouchValid` class (5 tests) from `test_trendline.py` and `is_touch_valid` from import.
**Tests:** 1 test verifying function is no longer importable

---

## Config Summary

| Constant | Default | Env Var | Purpose |
|----------|---------|---------|---------|
| `PIN_BAR_MIN_WICK_RATIO` | 2.0 | `PIN_BAR_MIN_WICK_RATIO` | Pin bar minimum wick-to-body ratio |
| `ENGULFING_MIN_BODY_RATIO` | 0.3 | `ENGULFING_MIN_BODY_RATIO` | Engulfing minimum body-to-range ratio |
| `LIQUIDITY_EQ_TOLERANCE_ATR` | 0.15 | `LIQUIDITY_EQ_TOLERANCE_ATR` | EQH/EQL clustering ATR multiplier |
| `RSI_DIVERGENCE_LOOKBACK` | 10 | `RSI_DIVERGENCE_LOOKBACK` | RSI divergence scan window |
| `TRENDLINE_MAX_RAY_BARS` | 100 | `TRENDLINE_MAX_RAY_BARS` | Max ray extension beyond last anchor |
| `SNR_CLUSTER_PAIR_MULT` | dict | — | Per-pair SNR clustering sensitivity |

---

## Test Results

```
FP-10 specific: 31 passed, 130 deselected in 0.29s
Full regression: 909 passed, 8 skipped, 7 warnings in 22.33s
```

**Net change:** +27 tests (31 added − 4 removed from TestIsTouchValid − 0 old regressions)
