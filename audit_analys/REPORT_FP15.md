# REPORT FP-15 — Notifier, Scheduler, Charts, Main

**Status:** ✅ COMPLETE
**Date:** 2025-01-27
**Items:** 18 (3 🟡 Medium, 11 🔵 Low, 3 🔄 Consistency, 1 💀 Dead-code)
**Tests added:** 20 new | **Existing tests fixed:** 3
**Regression:** 1028 passed, 8 skipped, 7 warnings in 61.70s

---

## Changes by File

### `notifier/templates.py`

| ID | Severity | Change |
|----|----------|--------|
| M-29 / CON-21 | 🟡🔄 | Score denominator `"/15"` → dynamic `f"/{MAX_POSSIBLE_SCORE}"` via import from `config.strategy_rules` |
| L-51 | 🔵 | Added `# i18n-opportunity` comment marker on user-facing strings |

### `notifier/handler.py`

| ID | Severity | Change |
|----|----------|--------|
| M-31 | 🟡 | Documented in module docstring that pending queue events are dispatched directly via lifecycle (no separate WA alert needed) |
| L-48 | 🔵 | Documented that all except blocks already use explicit exception types (no bare `except`) |
| L-52 | 🔵 | Documented chart failure → text-only degradation path |

### `notifier/whatsapp.py`

| ID | Severity | Change |
|----|----------|--------|
| L-49 | 🔵 | Expanded module docstring with API credential rotation procedure docs |

### `scheduler/runner.py`

| ID | Severity | Change |
|----|----------|--------|
| L-46 | 🔵 | Added `_JOB_PREFIX = "fx_"` constant; all 5 job IDs prefixed (`fx_asian_scan`, `fx_london_scan`, `fx_preny_scan`, `fx_wrapup`, `fx_dns_refresh`) |
| L-47 | 🔵 | Added `misfire_grace_time: 300` (5 min) to scheduler `job_defaults` |
| D-02 | 💀 | Removed unused `ALL_PAIRS` import; now only imports `MVP_PAIRS` from `config.settings` |

### `charts/screenshot.py`

| ID | Severity | Change |
|----|----------|--------|
| M-39 | 🟡 | Added `get_chart_generator()` lazy-init singleton function with `_chart_generator` module variable |
| L-60 | 🔵 | Added empty DataFrame guard (`if ohlcv is None or ohlcv.empty: raise ValueError`) in `generate_entry_chart` and `generate_audit_chart` |
| L-61 | 🔵 | Added `to_data_uri()` method; `to_base64()` kept as backwards-compatible alias that delegates to it |
| CON-25 | 🔄 | Added `cleanup()` method to remove temporary PNG files |

### `main.py`

| ID | Severity | Change |
|----|----------|--------|
| L-01 | 🔵 | Added comment documenting `signal` import's Windows fallback behaviour |
| CON-03 | 🔄 | Added comment documenting standardised log format pattern (`%(asctime)s - %(name)s - %(levelname)s`) |

### `dashboard/backend/main.py`

| ID | Severity | Change |
|----|----------|--------|
| L-02 | 🔵 | Added `"version": app.version` field to `/health` JSON response |
| L-10 | 🔵 | Added `_pm2_restart_count` global variable, incremented on startup in `_mount_static`, returned in `/health` response |
| — | — | App version bumped `2.0.0` → `2.1.0` |

---

## Tests Added (20 new in `tests/test_batch5_infra.py`)

| # | Class | Tests | Covers |
|---|-------|-------|--------|
| 1 | TestFP15TemplateDynamicScore | 2 | M-29/CON-21 dynamic score denominator |
| 2 | TestFP15TemplateI18nMarker | 1 | L-51 i18n comment |
| 3 | TestFP15HandlerDocstrings | 1 | M-31, L-48, L-52 handler docs |
| 4 | TestFP15WhatsAppDocs | 1 | L-49 credential rotation docs |
| 5 | TestFP15SchedulerPrefix | 2 | L-46 job ID prefix |
| 6 | TestFP15MisfireGrace | 1 | L-47 misfire_grace_time |
| 7 | TestFP15RunnerImports | 1 | D-02 removed ALL_PAIRS |
| 8 | TestFP15ChartLazy | 2 | M-39 get_chart_generator singleton |
| 9 | TestFP15EmptyDataFrame | 2 | L-60 empty DataFrame guard |
| 10 | TestFP15DataUri | 1 | L-61 to_data_uri / to_base64 alias |
| 11 | TestFP15ChartCleanup | 1 | CON-25 cleanup() method |
| 12 | TestFP15MainComments | 1 | L-01, CON-03 main.py comments |
| 13 | TestFP15DashboardHealth | 2 | L-02 version, L-10 restart counter |
| 14 | TestFP15DashboardVersion | 2 | Dashboard version 2.1.0 |

## Existing Tests Fixed (3)

| File | Change | Reason |
|------|--------|--------|
| `tests/test_integration.py` | Job ID `"asian_scan"` → `"fx_asian_scan"` (2 test methods) | L-46 prefix change |
| `tests/test_notifier.py` | `"11/15"` → `"11/14"` | M-29 dynamic score (MAX_POSSIBLE_SCORE = 14) |

---

## Bug Fixes During Implementation

1. **Em-dash SyntaxError** — `screenshot.py` had Unicode em-dash (U+2014 `—`) in string literals, causing `SyntaxError: invalid character`. Replaced with ASCII `--`.
2. **Orphaned docstring** — Multi-replace left residual parameter docs after ValueError guard. Cleaned up.
3. **Scheduler test** — Test called `shutdown()` on never-started scheduler → `SchedulerNotRunningError`. Removed shutdown call.

---

## Regression

```
Before FP-15: 1008 passed, 8 skipped
After FP-15:  1028 passed, 8 skipped  (+20 new tests)
Duration:     61.70s
Failures:     0
```
