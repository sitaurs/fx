# Phase 2 Audit: AI & Analysis Layer

**Scope:** `agent/gemini_client.py` (357 lines), `agent/context_builder.py` (398 lines), `agent/system_prompt.py` (211 lines), `agent/voting.py` (263 lines), `agent/state_machine.py` (~210 lines), `agent/tool_registry.py` (93 lines)

**Total Lines Reviewed:** 1,532

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 1 |
| 🟠 HIGH | 2 |
| 🟡 MEDIUM | 4 |
| 🔵 LOW | 9 |
| ⚪ Dead Code | 3 |
| 📝 Consistency | 4 |

---

## 🔴 CRITICAL Issues

### C-01: `collect_multi_tf_async` is sequential, not concurrent
**File:** `agent/context_builder.py` lines 374-388
```python
tasks = {
    tf: analyze_timeframe_async(pair, tf, candle_count)
    for tf in timeframes
}
for tf, coro in tasks.items():
    try:
        results[tf] = await coro       # ← sequential await!
    except Exception as exc:
        ...
```
**Problem:** Python coroutines are NOT started until awaited. The dict comprehension creates unawaited coroutines, then the `for` loop awaits them one by one. With 3 timeframes (H4, H1, M15), each taking ~2-5s for OANDA API + tools, this is **6-15 seconds sequential** instead of ~5 seconds parallel.
**Impact:** Each scan pair takes 2-3× longer than necessary. In scan_batch with 6 pairs, this adds 30-60+ seconds total latency.
**Fix:**
```python
import asyncio

async def collect_multi_tf_async(pair, timeframes, candle_count=CANDLE_COUNT):
    tasks = [analyze_timeframe_async(pair, tf, candle_count) for tf in timeframes]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results = {}
    for tf, result in zip(timeframes, gathered):
        if isinstance(result, Exception):
            results[tf] = {"error": str(result)}
        else:
            results[tf] = result
    return results
```
**Note:** This was documented in MASTER_DETAIL_05_CONSISTENCY_GAPS.md as a known gap but never fixed.

---

## 🟠 HIGH Issues

### H-01: Daily budget enforcement is warning-only
**File:** `agent/gemini_client.py` lines 222-229
```python
if self._total_cost_usd >= self._daily_budget_usd:
    logger.warning("⚠️ DAILY BUDGET EXCEEDED...")
```
**Problem:** When budget is exceeded, the system only logs a warning. API calls continue normally. There is no actual blocking mechanism. With Gemini Pro at $5/M output tokens, a scan_batch with 6 pairs + 3 voting runs each could burn through the budget fast.
**Impact:** Unexpected Gemini billing if analysis enters a hot loop (e.g., frequent restarts during active sessions).
**Fix:** Add a guard in `agenerate_structured`:
```python
if self.budget_exceeded:
    raise RuntimeError("Gemini daily budget exceeded — call blocked")
```

### H-02: `reset_daily_cost()` is never called
**File:** `agent/gemini_client.py` line 243
**Problem:** The method exists but is never invoked from `daily_wrapup()`, `reset_daily()`, or anywhere else. Cost counters accumulate from first boot indefinitely. The `budget_exceeded` check becomes permanently True after enough calls, even across multiple days.
**Impact:** If budget enforcement is ever made blocking (H-01 fix), the bot would stop after one day.
**Fix:** Call `_lifecycle._gemini.reset_daily_cost()` in `ProductionLifecycle.reset_daily()`.

---

## 🟡 MEDIUM Issues

### M-01: `GeminiClient.close()` never called
**File:** `agent/gemini_client.py` line 357
**Problem:** The `close()` method releases HTTP connection pools but is never called during `on_shutdown()`. The underlying `genai.Client` may leak connections.
**Fix:** Add `_lifecycle._gemini.close()` in `main.py:on_shutdown()`.

### M-02: Voting `merge()` uses first candidate's zone width
**File:** `agent/voting.py` lines 168-170
```python
half_width = (cluster[0].entry_zone_high - cluster[0].entry_zone_low) / 2
entry_low = median_mid - half_width
entry_high = median_mid + half_width
```
**Problem:** After computing median entry midpoint, the zone width is taken from the first candidate only. If candidate 1 has a tight zone (10 pips) and candidates 2-3 have wider zones (20 pips), the merged result uses the tight zone — potentially missing the optimal entry.
**Fix:** Use median half-width:
```python
half_widths = [(c.entry_zone_high - c.entry_zone_low) / 2 for c in cluster]
half_width = statistics.median(half_widths)
```

### M-03: Clustering doesn't consider `strategy_mode`
**File:** `agent/voting.py` lines 97-112
**Problem:** Clusters are formed by (direction, entry_zone), not strategy_mode. Two candidates with the same direction and zone but different strategies (e.g., `sniper_confluence` vs `scalping_channel`) get merged. The merged result takes the first candidate's strategy_mode.
**Impact:** Unlikely in practice (same zone usually implies same strategy), but possible in choppy markets.

### M-04: `analyze_timeframe` has no retry on fetch failure
**File:** `agent/context_builder.py` lines 55-73
**Problem:** `fetch_ohlcv()` is called once. If OANDA API returns an error (timeout, rate limit), the entire timeframe fails. The error bubbles up as `{"error": str(exc)}` for that TF. With 3 TFs, losing H4 data means Gemini reasons from H1+M15 only — missing HTF bias.
**Impact:** Medium. OANDA is generally reliable, but network glitches can cause intermittent failures.
**Fix:** Add 1-2 retries with short delay inside `analyze_timeframe`.

---

## 🔵 LOW Issues

### L-01: `_COST_PER_1M` pricing hardcoded to "Jan 2025"
**File:** `agent/gemini_client.py` lines 166-169
**Problem:** Gemini pricing changes periodically. Hardcoded values may become inaccurate, making budget tracking unreliable.

### L-02: `_build_config` includes SYSTEM_PROMPT for all calls
**File:** `agent/gemini_client.py` line 146
**Problem:** Even short revalidation prompts include the full ~2000-token system prompt as `system_instruction`. This increases cost by ~$0.00015 per Flash call. Over 50+ calls/day, adds ~$0.01/day. Negligible but wasteful for revalidation.

### L-03: Index correlation mode mentioned in system prompt but disabled
**File:** `agent/system_prompt.py` lines 114-117
**Problem:** System prompt mentions index_correlation mode and says it's DISABLED. This wastes prompt tokens and could confuse Gemini into referencing DXY despite the instruction.
**Fix:** Remove the mode from the prompt entirely (it's already excluded in tool_registry).

### L-04: State machine `_history` grows unbounded
**File:** `agent/state_machine.py`
**Problem:** State history list has no cap. With frequent resets (6 pairs × 4 sessions/day × resets), grows by ~24-50 entries/day. Very slow but technically unbounded.

### L-05: `should_cancel()` defined but never called
**File:** `agent/state_machine.py` line 186
**Problem:** The orchestrator doesn't use `sm.should_cancel()`. Instead, it checks scores directly against `MIN_SCORE_FOR_TRADE`. The hysteresis-based cancellation via `HYSTERESIS_CANCEL_SCORE` is unused.

### L-06: `format_context` limits output display
**File:** `agent/context_builder.py` lines 310-365
**Problem:** SNR levels show first 6 only (`snr[:6]`), order blocks show first 3 per direction (`bull_ob[:3]`, `bear_ob[:3]`), pin bars show last 3 (`pins[-3:]`). If there are 10+ relevant levels, Gemini doesn't see them all.
**Impact:** Minor — most actionable levels are in the top-N by score.

### L-07: `_extract_score_flags` in orchestrator is fragile
**File:** `agent/orchestrator.py` lines 390-475
**Problem:** Relies on specific dict keys from tool outputs (e.g., `"swing_highs"`, `"supply_zones"`, `"pin_bars"`). If any tool changes its output format, the score extraction silently returns wrong flags.

### L-08: Quick-result confidence is hardcoded 0.9
**File:** `agent/voting.py` line 245
```python
confidence=0.9,
```
**Problem:** High-score setups (≥9) bypass voting and get exactly 0.9 confidence regardless. A score of 12 and a score of 9 get the same confidence. Should scale with score.

### L-09: `_sync_retry` / `_async_retry` don't differentiate error types
**File:** `agent/gemini_client.py` lines 50-80
**Problem:** Retries on ALL exceptions including `ValueError` (bad schema) and `KeyError` (programming error). Should only retry on transient errors (network, rate limit, 500/503).

---

## ⚪ Dead Code

### D-01: `ALL_TOOLS` list is effectively unused in production
**File:** `agent/tool_registry.py` lines 63-85
**Problem:** `ALL_TOOLS` is passed to Gemini via `_build_config()` in **non-structured** mode only. But the orchestrator exclusively uses `agenerate_structured()` (which strips tools and uses `response_schema` instead). The context_builder runs all tools locally before Gemini is called.
**Impact:** The 16-function list is constructed at import time but never sent to Gemini in the current flow. It adds import overhead but no API cost.

### D-02: `GeminiClient.generate()` and `GeminiClient.agenerate()` (non-structured)
**File:** `agent/gemini_client.py` lines 259-288
**Problem:** These methods are never called by any module. The orchestrator only uses `agenerate_structured`. The non-structured variants exist for potential future function-calling mode but are currently dead.

### D-03: `should_cancel()` in StateMachine
**File:** `agent/state_machine.py` line 186
**Problem:** Never called by orchestrator or any other code. The hysteresis check is implemented but the feature is unused.

---

## 📝 Consistency Issues

### CON-01: Two scoring systems coexist
- **System prompt §4:** Tells Gemini to score using specific weights (max 15)
- **Orchestrator `_extract_score_flags`:** Locally recalculates score from tool outputs
- The local score overrides Gemini's score (FIX F3-01), making Gemini's scoring instruction partially wasteful. Gemini still uses the rules for reasoning but its numeric score is discarded.

### CON-02: State machine states vs actual usage
- 7 states defined: SCANNING, WATCHING, APPROACHING, TRIGGERED, ACTIVE, CLOSED, CANCELLED
- Orchestrator uses: SCANNING → WATCHING → APPROACHING → TRIGGERED (in burst)
- Lifecycle uses: None (manages trades independently, never calls state machine)
- ACTIVE, CLOSED are never transitioned to in practice

### CON-03: Tool-calling vs structured output
- `tool_registry.py` prepares tools for Gemini function calling
- `context_builder.py` runs tools locally and injects results as text
- `_build_config` uses tools for non-structured mode (never used)
- Result: Two parallel tool execution paths, only one is active

### CON-04: `analyze_timeframe_async` wrapper vs direct use
**File:** `agent/context_builder.py` lines 365-371
```python
async def analyze_timeframe_async(...):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, analyze_timeframe, ...)
```
Uses `get_event_loop()` which is deprecated in Python 3.10+ in favor of `asyncio.get_running_loop()`. Works fine but triggers deprecation warning.

---

## Architecture Observations

### Strengths
1. **Deterministic tool pipeline**: All market data comes from verified Python tools, not Gemini hallucination. Score override (FIX F3-01) ensures numeric scores are trustworthy.
2. **Hybrid model strategy**: Flash for cheap scans, Pro for deep analysis. Cost-effective.
3. **Voting ensemble**: 3-run clustering with majority vote reduces hallucination risk.
4. **Dynamic system prompt**: Scoring weights and rules read from config — single source of truth.

### Areas for Improvement
1. **Parallelism**: The biggest performance win is fixing `collect_multi_tf_async` to use `asyncio.gather()`. This alone could cut scan time by 40-60%.
2. **Budget enforcement**: Making budget blocking instead of advisory prevents runaway costs.
3. **Dead code cleanup**: `ALL_TOOLS` list, non-structured generate methods, `should_cancel()` — all can be removed or marked as future features.
4. **Error classification**: Retry logic should distinguish transient errors from programming errors.

---

*Audit completed: 2026-03-07*
*Files reviewed: 6 | Lines reviewed: 1,532 | Issues found: 23*
