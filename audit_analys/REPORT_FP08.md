# REPORT FP-08 — Prompt, Voting, State Machine, Tool Registry

**Phase:** FP-08  
**Status:** ✅ COMPLETE  
**Date:** 2025-01-xx  
**Regression:** 849 passed, 8 skipped, 7 warnings  
**New tests:** 24  

---

## Summary

FP-08 addresses the HIGH-priority strategy mode enforcement gap (H-11), extracts mode selection to config (M-11), standardizes all code references to English (L-16/L-19), adds pair name to state transition logs (L-18), introduces meaningful voter names (L-21), documents tool registry and CANCELLED state (D-06/D-07), and updates masterplan section references (CON-09).

---

## Items Fixed

### 1. H-11 — STRATEGY_MODES Code Enforcement [HIGH]
**File:** `tools/validator.py`

**Problem:** `STRATEGY_MODES` and `ANTI_RUNGKAD_CHECKS` existed in `config/strategy_rules.py` but were only communicated to Gemini via the system prompt. No programmatic enforcement — the AI could ignore mandatory requirements and the validator would still pass.

**Fix:**
- Added `strategy_mode`, `sweep_confirmed`, `choch_confirmed` parameters to `validate_trading_plan()`
- Imported `STRATEGY_MODES` and `ANTI_RUNGKAD_CHECKS` from config
- Added Rule 6: Strategy mode enforcement
  - Checks `sweep_required` and `choch_required` from STRATEGY_MODES config
  - Checks Anti-Rungkad mandatory checks (`liquidity_sweep`, `choch_confirmation`)
  - Violations are appended and block trade if mandatory checks not met
- Backward compatible: existing callers without `strategy_mode` param are unaffected

### 2. M-11 — Mode Selection Priority Extracted to Config
**Files:** `config/settings.py`, `agent/system_prompt.py`

**Problem:** Mode selection priority was hardcoded in the system prompt: "index_correlation is DISABLED", "If valid trendline → sniper_confluence", etc.

**Fix:**
- Added `MODE_SELECTION_PRIORITY` list to `config/settings.py`
  - Each entry: `{"mode": str, "enabled": bool, "note": str}`
  - `index_correlation` disabled by default via `MODE_INDEX_CORRELATION_ENABLED` env var
- Added `_mode_priority_block()` helper in `system_prompt.py`
- System prompt Section 2 now dynamically renders mode priority from config
- Toggling `MODE_INDEX_CORRELATION_ENABLED=true` enables DXY mode without code changes

### 3. L-16/L-19 — Language Standardization (English)
**Files:** 35+ files across `agent/`, `tools/`, `config/`, `schemas/`, `notifier/`, `tests/`, etc.

**Problem:** Code references mixed Indonesian "Rujukan" with English. Inconsistent language in docstrings and comments.

**Fix:**
- Replaced all "Rujukan: masterplan.md Section X" with "Reference: masterplan.md §X" across every `.py` file in the project
- Only exception: `docs/OVERVIEW_AI_FOREX_AGENT.md` retains "Rujukan" as it's a documentation file, not code
- Standardized masterplan section reference format: `§X` notation throughout

### 4. L-17 — `_extract_score` Regex [N/A]
**Resolution:** Verified N/A. The function `_extract_score` with a regex does not exist in the codebase. The scorer (`tools/scorer.py`) uses typed boolean flags with integer weights — no text parsing or regex involved. Test confirms boolean flag behavior.

### 5. L-18 — State Transition Log: Include Pair Name
**File:** `agent/state_machine.py`

**Problem:** `cancel()` and `reset()` log messages did not include the pair name. Only `transition()` had it.

**Fix:**
- `transition()`: Now also logs the previous state (`from=SCANNING`)
- `cancel()`: Extracts `pair_name` from `self._context` and includes it in log
- `reset()`: Extracts `pair_name` before clearing context, logs it
- Arrow character `→` standardized to `->` in all messages

### 6. L-21 — Meaningful Voter Names
**File:** `agent/orchestrator.py`

**Problem:** Voting runs logged as "Voting run 2/3", "Voting run 3/3" — no meaningful distinction between runs.

**Fix:**
- Added `voter_profiles` list with named profiles: `conservative` (high thinking), `aggressive` (low thinking), `balanced` (high thinking)
- Voting prompt now includes: "Voting run 2/3 (voter: conservative)"
- Log warnings include voter name: "Voting run 2 (conservative) blocked: budget exceeded"

### 7. D-06 — Tool Registry: Unused Tools
**File:** `agent/tool_registry.py`

**Resolution:** Verified all 16 registered tools are distinct, callable, and serve unique purposes. `dxy_relevance_score` is already properly disabled with `§7.10` comment. No other tools are dead — they're all available for Gemini's automatic function calling and serve distinct analytical functions. Updated docstring reference from "Rujukan" to "Reference".

### 8. D-07 — CANCELLED Transition Path Documentation
**File:** `agent/state_machine.py`

**Problem:** `CANCELLED` state had no outgoing transitions in `_ALLOWED_TRANSITIONS`, which could be confusing.

**Fix:**
- Added comprehensive comment block above `_ALLOWED_TRANSITIONS` explaining:
  - CANCELLED is reachable from any non-terminal state via `cancel()` (not `transition()`)
  - Returning from CANCELLED to SCANNING is done via `reset()` after cooldown expires
  - This dual-path design keeps the linear forward graph clean while supporting emergency cancellation

### 9. CON-09 — Masterplan Section References Updated
**Files:** All files with "Rujukan" (see L-16 above)

**Fix:** Updated all section references to use consistent `§X` notation matching actual masterplan section numbers. Verified section numbers against `masterplan.md`:
- §3 = Architecture, §5.3 = System Prompt, §7 = Scoring, §8 = Voting
- §11 = State Machine, §12 = Anti-Flip-Flop, §13 = Post-Open Trade
- §22 = Notification, §23 = Demo Mode, §24 = Error Handling

### 10. CON-10 — Already Done in FP-07
**Resolution:** Vote result field naming was completed in FP-07. No additional work needed.

---

## Tests Added (24 new)

| # | Test Class | Count | Description |
|---|-----------|-------|-------------|
| 1 | TestFP08StrategyModeEnforcement | 7 | sniper needs sweep, sniper needs choch, passes when both confirmed, scalping no sweep needed, index_correlation needs both, backward compatible (no mode), anti-rungkad mandatory |
| 2 | TestFP08StateMachinePairLog | 3 | cancel includes pair, transition includes pair+from, reset includes pair |
| 3 | TestFP08CancelledTransitionDoc | 2 | CANCELLED is terminal, CANCELLED -> SCANNING via reset |
| 4 | TestFP08ModeSelectionConfig | 3 | config has priority list, index_correlation disabled, system prompt uses config |
| 5 | TestFP08LanguageStandardization | 2 | no Rujukan in agent modules, no Rujukan in tool modules |
| 6 | TestFP08VoterNames | 2 | voter_profiles exist, voter name in prompt |
| 7 | TestFP08ToolRegistry | 4 | all tools callable, no duplicates, dxy not registered, tool count=16 |
| 8 | TestFP08ExtractScoreNA | 1 | scorer uses boolean flags (L-17 N/A verification) |

---

## Existing Tests Updated

- `tests/test_batch2_fixes.py::TestDXYModeGuard::test_index_correlation_disabled_in_prompt` — Updated assertion: "Do NOT select this mode" → check for "DXY" or "index_correlation" (mode selection now dynamic from config)

---

## Regression Results

```
849 passed, 8 skipped, 7 warnings in 33.00s
```

Previous (FP-07): 825 passed → **+24 new tests, 0 regressions**
