# FP-12 Report: Database, Repository, Config

**Phase:** FP-12  
**Status:** ✅ COMPLETE  
**Files Modified:** 5 production + 2 test files  
**New Tests:** 28  
**Regression:** 968 passed, 8 skipped, 7 warnings (was 940)

---

## Items Fixed (13 total)

### H-13 🟠 HIGH — `trade_stats()` win logic
- **Problem:** `trade_stats()` only counted TP1_HIT, TP2_HIT, MANUAL_CLOSE(pips>0) as wins. TRAIL_PROFIT was NOT counted as a win, deflating winrate significantly. CANCELLED trades were included in the denominator.
- **Fix:** `database/repository.py` — rewritten win criteria:
  - Always win: TP1_HIT, TP2_HIT, TRAIL_PROFIT
  - Conditional win: MANUAL_CLOSE, BE_HIT (only when pips > 0)
  - CANCELLED excluded from winrate denominator
- **Tests:** 5 tests (trail_profit, be_hit_positive, be_hit_zero, cancelled_excluded, manual_close)

### M-26 🟡 — Config precedence documented
- **Problem:** No documentation explaining which config source takes precedence.
- **Fix:** `database/repository.py` module docstring expanded with config hierarchy: `.env → settings.py → strategy_rules.py`
- **Tests:** 2 tests (docstring has precedence, mentions strategy_rules)

### M-27 🟡 — Parameterized pagination limit
- **Problem:** `list_trades(limit=...)` was applied via `.limit()` which is safe, but no bounds checking existed.
- **Fix:** `database/repository.py` — limit clamped to `[1, 10_000]` with comment explaining parameterisation safety.
- **Tests:** 2 tests (negative limit, huge limit)

### M-28 🟡 — MVP_PAIRS single source of truth
- **Problem:** `MVP_PAIRS` and `ALL_PAIRS` were identical lists in different orders — confusing.
- **Fix:** `config/settings.py` — `ALL_PAIRS = MVP_PAIRS` (alias). Comment documents D-11.
- **Tests:** 2 tests (identity check, importability)

### L-40 🔵 — DB index on `pair` column
- **Problem:** `Trade.pair` and `AnalysisSession.pair` had no database index despite being filtered frequently.
- **Fix:** `database/models.py` — Added `index=True` to both `pair` columns.
- **Tests:** 2 tests (Trade pair index, AnalysisSession pair index)

### L-41 🔵 — `save_trade()` return documented
- **Problem:** `save_trade()` returned a merged Trade but this wasn't documented.
- **Fix:** `database/repository.py` — Expanded docstring explaining `trade_id` and `id` fields available on return.
- **Tests:** 2 tests (docstring check, actual return has id)

### L-42 🔵 — CHALLENGE_CENT config documented
- **Problem:** Three `CHALLENGE_CENT_*` multipliers existed without any documentation.
- **Fix:** `config/settings.py` — Added comprehensive comment block explaining LOT/SL/TP multipliers and env override.
- **Tests:** 2 tests (source has comment, values correct)

### L-43 🔵 — SCORING_WEIGHTS keys documented
- **Problem:** Scoring weight keys were undocumented — hard to understand what each contributes.
- **Fix:** `config/strategy_rules.py` — Added full comment block documenting all 7 positive factors and 4 penalties with their values and meanings.
- **Tests:** 3 tests (positive keys, penalty keys, MAX_POSSIBLE_SCORE)

### L-44 🔵 — `list_trades` limit default reviewed
- **Problem:** FIX_PLAN mentioned limit=50 but actual default was 100. Needed review and documentation.
- **Fix:** Kept default at 100 (suitable for dashboard). Added inline L-44 comment. Limit is now clamped anyway (M-27).
- **Tests:** 2 tests (default value check, source has comment)

### L-45 🔵 — LOG_LEVEL validation
- **Problem:** No `LOG_LEVEL` configuration existed in settings.py (was only hardcoded as `"info"` in uvicorn call).
- **Fix:** `config/settings.py` — Added `LOG_LEVEL` with env override, validated against `{DEBUG, INFO, WARNING, ERROR, CRITICAL}`, falls back to INFO on invalid.
- **Tests:** 3 tests (exists in valid set, default INFO, invalid fallback)

### CON-19 🔄 — `demo_pnl` column naming
- **Problem:** `demo_pnl` and `demo_balance_after` fields named with "demo_" prefix but used in both demo and real modes.
- **Fix:** `database/models.py` — Added CON-19 documentation block explaining the naming history and that renaming requires a DB migration.
- **Tests:** 1 test (source documents CON-19 and real mode usage)

### CON-20 🔄 — MIN_CONFLUENCE_SCORE single source
- **Problem:** `MIN_SCORE_FOR_TRADE=5` in settings.py duplicated `MIN_CONFLUENCE_SCORE=5` in strategy_rules.py.
- **Fix:** `config/settings.py` — Added CON-20 comment documenting that `strategy_rules.MIN_CONFLUENCE_SCORE` is the canonical source and both must match.
- **Tests:** 2 tests (values match, settings documents canonical source)

### D-11 💀 — ALL_PAIRS removed as duplicate
- **Problem:** `ALL_PAIRS` was a duplicate list identical to `MVP_PAIRS`. Only imported in scheduler/runner.py but never used independently.
- **Fix:** `config/settings.py` — `ALL_PAIRS = MVP_PAIRS` (alias preserves backward compat).
- **Tests:** See M-28 tests (shared).

---

## Files Changed

| File | Changes |
|------|---------|
| `database/repository.py` | H-13 win logic, M-26 docstring, M-27 limit clamp, L-41 save_trade doc, L-44 limit doc |
| `database/models.py` | L-40 pair index (Trade + AnalysisSession), CON-19 naming doc |
| `config/settings.py` | D-11 ALL_PAIRS alias, L-42 CHALLENGE_CENT doc, L-45 LOG_LEVEL, CON-20 doc |
| `config/strategy_rules.py` | L-43 SCORING_WEIGHTS documentation |
| `tests/test_database.py` | Updated test_trade_stats to include TRAIL_PROFIT and BE_HIT |
| `tests/test_batch5_infra.py` | +28 new FP-12 tests (13 test classes) |

---

## Test Results

```
FP-12 specific: 28 passed, 192 deselected in 1.44s ✅
Full regression: 968 passed, 8 skipped, 7 warnings in 29.38s ✅
```
