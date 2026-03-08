# FIX PLAN — Penyelesaian 168 Issues Audit

---

## Strategi Anti-Halusinasi & Context Management

### Masalah
- Context window terbatas ~120K tokens
- File terbesar: `production_lifecycle.py` (1,593 lines) = ~6K tokens sendiri
- Jika context penuh → summary → hilang detail → risiko halusinasi saat edit

### Solusi: Prinsip "Small Batch, Full Context"

1. **Max 2-3 file production + 1-2 file test per phase** (target <3,000 lines per phase)
2. **Setiap phase dimulai chat baru** — context fresh, tidak ada akumulasi
3. **Setiap phase dimulai dengan READ dulu** — baca file target + test terkait sebelum edit
4. **FIX_PLAN.md ini jadi referensi** — attach file ini di awal setiap chat baru
5. **Checklist per phase** — centang issue yang sudah selesai
6. **Test di akhir setiap phase** — `pytest tests/test_xxx.py -v` + `pytest tests/ -x --timeout=30`
7. **Deploy ke VPS setelah setiap batch** (setiap 3-4 phase)

### Workflow Per Phase
```
1. ATTACH file FIX_PLAN.md + target files
2. READ: Baca semua file yang akan diedit + test terkait
3. UNDERSTAND: Konfirmasi pemahaman sebelum edit
4. FIX: Edit file (max 2-3 file per phase)
5. TEST: Run related tests → fix jika gagal
6. VERIFY: Run full test suite → confirm no regression
7. UPDATE: Centang issues yang sudah fix di FIX_PLAN.md
```

### Estimasi Total
- **15 Fix Phases** + 1 Integration Test + 1 VPS Deploy
- ~2-3 issues per session average, grouped by file
- Perkiraan: **8-10 sesi kerja** (beberapa phase bisa digabung jika kecil)

---

## Phase Overview

| Phase | Priority | Files | Issues | Est. Lines to Read |
|-------|----------|-------|--------|-------------------|
| FP-01 | 🔴 CRIT | dashboard/backend/main.py | 6 | ~700 + 150 test |
| FP-02 | 🔴 CRIT | trade_manager.py | 8 | ~410 + 290 test |
| FP-03 | 🔴 CRIT | fetcher.py | 7 | ~575 + 220 test |
| FP-04 | 🔴 CRIT | production_lifecycle.py (Part A) | 8 | ~800 (half file) + 520 test |
| FP-05 | 🔴 CRIT | production_lifecycle.py (Part B) | 6 | ~800 (half file) + 520 test |
| FP-06 | 🟠 HIGH | context_builder.py + gemini_client.py | 12 | ~650 + tests |
| FP-07 | 🟠 HIGH | pending_manager.py + orchestrator.py | 10 | ~775 + tests |
| FP-08 | 🟠 HIGH | system_prompt.py + voting.py + state_machine.py + tool_registry.py | 11 | ~620 + tests |
| FP-09 | 🟠 HIGH | structure.py + swing.py + supply_demand.py + orderblock.py | 15 | ~540 + tests |
| FP-10 | 🟡 MED | indicators.py + liquidity.py + snr.py + price_action.py + trendline.py | 12 | ~680 + tests |
| FP-11 | 🟡 MED | scorer.py + validator.py + choch_filter.py + dxy_gate.py | 8 | ~310 + tests |
| FP-12 | 🟡 MED | repository.py + models.py + settings.py + strategy_rules.py | 13 | ~705 + tests |
| FP-13 | 🟡 MED | post_mortem.py + error_handler.py + demo_tracker.py | 14 | ~970 + tests |
| FP-14 | 🔵 LOW | schemas/*.py (plan, market_data, structure, zones) | 10 | ~220 + tests |
| FP-15 | 🔵 LOW | notifier/*.py + scheduler/runner.py + charts/screenshot.py + main.py | 17 | ~1,320 + tests |
| FP-16 | ✅ | — | — | Integration Test + Full Regression |
| FP-17 | 🚀 | — | — | VPS Deploy + Smoke Test |

---

## Detailed Phases

---

### FP-01: Security — Dashboard Auth [CRITICAL]
**Files:** `dashboard/backend/main.py` (692 lines)
**Tests:** `tests/test_dashboard.py` (152 lines)
**Total read:** ~850 lines ✅ fits in context

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | C-05 | 🔴 CRIT | Tambah authentication (API key/JWT) pada semua admin REST endpoints |
| ☑ | H-15 | 🟠 HIGH | Ganti sync `get_current_price()` → async di endpoint handler |
| ☑ | H-16 | 🟠 HIGH | Fix `/api/system/unhalt` — gunakan proper method, bukan direct private attr mutation |
| ☑ | CON-22 | 🔄 | Konsistenkan security: WS auth + REST auth |
| ☑ | L-50 | 🔵 | CORS: ganti `allow_origins=["*"]` → whitelist domain |
| ☑ | D-12 | 💀 | Hapus unused `/api/debug/` endpoint group — N/A, tidak ada |

**Test plan:**
```bash
pytest tests/test_dashboard.py -v
# Manual: curl endpoints tanpa token → expect 401/403
# Manual: curl dengan token → expect 200
```

---

### FP-02: Financial — Trade Manager [CRITICAL]
**Files:** `agent/trade_manager.py` (411 lines)
**Tests:** `tests/test_trade_manager.py` (289 lines), `tests/test_pnl_fixes.py` (313 lines)
**Total read:** ~1,010 lines ✅ fits in context

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | C-02 | 🔴 CRIT | Complete `_pip_value_per_lot` untuk cross pairs (EURJPY, GBPJPY, EURGBP, etc.) |
| ☑ | H-01 | 🟠 HIGH | Add ALREADY_CLOSED detection di `_close_trade_on_broker()` retry logic |
| ☑ | H-03 | 🟠 HIGH | Add metals/exotic pairs ke pip value table |
| ☑ | M-03 | 🟡 | Extract hardcoded pip sizes ke config |
| ☑ | L-05 | 🔵 | Tambah `__eq__` pada `TradeRecord` dataclass — N/A, sudah dataclass |
| ☑ | L-09 | 🔵 | Handle `_calculate_position_size` → minimum lot 0.01 floor |
| ☑ | CON-06 | 🔄 | Konsistenkan return format `_close_trade_on_broker` vs `_open_trade_on_broker` |
| ☑ | D-03 | 💀 | Hapus `_calculate_margin()` dead method — N/A, tidak ada |

**Test plan:**
```bash
pytest tests/test_trade_manager.py tests/test_pnl_fixes.py -v
# Tambah test: pip_value untuk EURJPY, GBPJPY, EURGBP
# Tambah test: close trade saat already closed → no infinite retry
```

---

### FP-03: DNS & Data Fetcher [CRITICAL]
**Files:** `data/fetcher.py` (576 lines)
**Tests:** `tests/test_fetcher.py` (218 lines)
**Total read:** ~795 lines ✅ fits in context

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | C-04 | 🔴 CRIT | Integrate `refresh_dns_overrides()` ke scheduler (periodic refresh tiap 6 jam) |
| ☑ | H-14 | 🟠 HIGH | Move module-level network call ke lazy init / startup function |
| ☑ | M-24 | 🟡 | Ganti deprecated `asyncio.get_event_loop()` → proper async pattern |
| ☑ | M-25 | 🟡 | Evaluasi sync vs async httpx — pattern run_in_executor sudah benar |
| ☑ | L-38 | 🔵 | Add guard for empty API response di `_build_candles()` — sudah ada |
| ☑ | L-39 | 🔵 | Extract connection pool limit 10 ke config |
| ☑ | CON-18 | 🔄 | Standardize candle `time` field format — sudah ISO-8601 |

**Test plan:**
```bash
pytest tests/test_fetcher.py -v
# Tambah test: refresh_dns_overrides() called periodically
# Tambah test: empty API response handling
```

---

### FP-04: Production Lifecycle Part A — Emergency & Close Pipeline [CRITICAL]
**Files:** `agent/production_lifecycle.py` (lines 1-800 only)
**Tests:** `tests/test_production_lifecycle.py` (518 lines)
**Total read:** ~1,320 lines ⚠️ large, tapi manageable

**Strategi:** File ini 1,593 lines — terlalu besar untuk satu phase. Split jadi 2:
- Part A: __init__, startup, emergency save, close pipeline, wrapup
- Part B: analysis loop, monitoring, state management

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | C-01 | 🔴 CRIT | Dynamic DB path di `_emergency_save_sync` — gunakan `settings.DB_FILE_PATH` |
| ☑ | H-04 | 🟠 HIGH | daily_wrapup: save_active_trades() dipanggil SEBELUM save_state() |
| ☑ | H-05 | 🟠 HIGH | Drawdown halt: BLOCK pending queue add (bukan warning) |
| ☑ | M-06 | 🟡 | `_store_event` — sudah ada di dashboard, null-safe |
| ☑ | M-08 | 🟡 | Equity snapshot: wrapped asyncio.ensure_future dgn try/except |
| ☑ | L-03 | 🔵 | Magic numbers — ada di post_mortem.py, bukan lifecycle (defer ke FP-13) |
| ☑ | L-04 | 🔵 | Null guard: opened_at + duration calculation |
| ☑ | CON-02 | 🔄 | `_close_trade` sudah always return dict — verified |

**Test plan:**
```bash
pytest tests/test_production_lifecycle.py -v
pytest tests/test_production_full_cycle.py -v --timeout=60
```

---

### FP-05: Production Lifecycle Part B — Analysis & Monitoring
**Files:** `agent/production_lifecycle.py` (lines 800-1593 only)
**Tests:** `tests/test_production_full_cycle.py` (1,755 lines — baca relevant sections only)
**Total read:** ~800 + ~500 selected test lines

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | M-01 | 🟡 | `_handle_sl_hit`: check if SL already moved to BE |
| ☑ | M-02 | 🟡 | Race condition guard untuk concurrent `check_active_positions` |
| ☑ | M-04 | 🟡 | Startup exception handling: lebih granular (bukan catch-all) |
| ☑ | L-08 | 🔵 | `_check_news_calendar` placeholder: implement atau hapus + add TODO |
| ☑ | L-11 | 🔵 | Session timeout 4h: extract ke config constant |
| ☑ | CON-05 | 🔄 | Error dict keys: standardize `error` vs `err_msg` |

**Test plan:**
```bash
pytest tests/test_production_lifecycle.py tests/test_production_full_cycle.py -v --timeout=60
```

---

### FP-06: AI Client — Context Builder + Gemini Client [HIGH]
**Files:** `agent/context_builder.py` (340), `agent/gemini_client.py` (306)
**Tests:** Related tests in `test_batch4_fixes.py`, `test_batch5_infra.py`
**Total read:** ~650 + ~400 selected test lines

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | C-03 | 🔴 CRIT | `collect_multi_tf_async`: ganti sequential await → `asyncio.gather()` |
| ☑ | H-06 | 🟠 HIGH | Daily budget: make blocking (reject request jika over budget) |
| ☑ | H-07 | 🟠 HIGH | AI response parse error: add proper fallback/retry |
| ☑ | M-09 | 🟡 | Fallback model switch: send notification (WA/log) |
| ☑ | M-10 | 🟡 | `_format_zones` truncation: add "[truncated N items]" marker |
| ☑ | L-13 | 🔵 | Model names: extract ke config |
| ☑ | L-14 | 🔵 | Token count estimation: document the ÷4 heuristic |
| ☑ | L-15 | 🔵 | Timeframe list hardcoded: extract ke config |
| ☑ | L-20 | 🔵 | `format_indicators` silent empty return: add warning log |
| ☑ | CON-07 | 🔄 | Model naming consistency |
| ☑ | CON-08 | 🔄 | Dict key naming: standardize snake_case |
| ☑ | D-05 | 💀 | `reset_daily_cost()`: integrate ke scheduler atau hapus |

**Test plan:**
```bash
pytest tests/test_batch4_fixes.py tests/test_batch5_infra.py -v -k "context or gemini"
# Tambah test: asyncio.gather multi-TF fetch
# Test: budget exceeded → request rejected
```

---

### FP-07: Pending Manager + Orchestrator [HIGH]
**Files:** `agent/pending_manager.py` (269), `agent/orchestrator.py` (505)
**Tests:** `tests/test_pending_queue.py` (243), `tests/test_integration.py` (246)
**Total read:** ~775 + ~490 test lines

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | H-02 | 🟠 HIGH | Fix duplicate pending entries pada order placement error |
| ☑ | M-05 | 🟡 | TTL expiry: gunakan market hours, bukan wall clock |
| ☑ | M-07 | 🟡 | Gemini timeout: per-pair tracking |
| ☑ | L-06 | 🔵 | Standardize log messages: pilih satu bahasa |
| ☑ | L-07 | 🔵 | `_parse_gemini_response`: granular exception handling |
| ☑ | L-12 | 🔵 | Extract `max_retries=3` ke config |
| ☑ | CON-04 | 🔄 | State naming: standardize `PENDING_ENTRY` vs `WATCHING` |
| ☑ | D-04 | 💀 | `remove_expired()` dead method: integrate atau hapus |
| ☑ | M-12 | 🟡 | Voting unanimous threshold ≥80%: review + document rationale |
| ☑ | CON-10 | 🔄 | Vote result field naming alignment |

**Test plan:**
```bash
pytest tests/test_pending_queue.py tests/test_integration.py -v
# Tambah test: duplicate pending entry prevention
```

---

### FP-08: Prompt, Voting, State Machine, Tool Registry [HIGH]
**Files:** `agent/system_prompt.py` (168), `agent/voting.py` (219), `agent/state_machine.py` (163), `agent/tool_registry.py` (69)
**Tests:** `tests/test_voting.py` (295), `tests/test_state_machine.py` (283)
**Total read:** ~620 + ~580 test lines

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | H-11 | 🟠 HIGH | STRATEGY_MODES: enforce di code (validator/orchestrator), bukan hanya prompt |
| ☑ | M-11 | 🟡 | System prompt: extract pair-specific rules ke config |
| ☑ | L-16 | 🔵 | Prompt language: standardize (pilih EN atau ID) |
| ☑ | L-17 | 🔵 | `_extract_score` regex: handle decimal scores |
| ☑ | L-18 | 🔵 | State transition log: include pair name |
| ☑ | L-19 | 🔵 | Tool descriptions: standardize language |
| ☑ | L-21 | 🔵 | Voter names: use meaningful names |
| ☑ | D-06 | 💀 | Tool registry: remove unused registered tools |
| ☑ | D-07 | 💀 | State machine CANCELLED: add transition path atau hapus |
| ☑ | CON-09 | 🔄 | System prompt masterplan section references: update |
| ☑ | CON-10 | 🔄 | (shared with FP-07 — vote result field naming) |

**Test plan:**
```bash
pytest tests/test_voting.py tests/test_state_machine.py -v
# Tambah test: STRATEGY_MODES enforcement
```

---

### FP-09: Technical Tools — Structure & Zones [HIGH]
**Files:** `tools/structure.py` (158), `tools/swing.py` (100), `tools/supply_demand.py` (215), `tools/orderblock.py` (68)
**Tests:** `tests/test_structure.py` (114), `tests/test_swing.py` (225), `tests/test_zones.py` (239), `tests/test_orderblock.py` (86)
**Total read:** ~540 prod + ~664 test = ~1,204 ✅

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | H-08 | 🟠 HIGH | Supply zone freshness: reduce decay aggression |
| ☑ | H-09 | 🟠 HIGH | Order block: fix body vs wick boundary inconsistency |
| ☑ | H-10 | 🟠 HIGH | Structure: add CHOCH detection dari ranging state |
| ☑ | M-13 | 🟡 | `detect_zones` base candle threshold: extract ke config |
| ☑ | M-15 | 🟡 | Swing detection boundary effect: handle first/last N candles |
| ☑ | M-16 | 🟡 | OB scoring: add freshness/age factor |
| ☑ | M-17 | 🟡 | BOS detection threshold: make configurable |
| ☑ | L-22 | 🔵 | Supply demand: max zones 10 → configurable |
| ☑ | L-24 | 🔵 | Swing `lookback=5`: document rationale |
| ☑ | L-25 | 🔵 | OB docstring: update |
| ☑ | L-27 | 🔵 | Structure return dict: consistent casing |
| ☑ | L-28 | 🔵 | Supply demand: input validation candle array |
| ☑ | CON-11 | 🔄 | Zone dict keys alignment with Pydantic models |
| ☑ | CON-12 | 🔄 | OB return format alignment with supply_demand |
| ☑ | CON-13 | 🔄 | Structure trend: consider using Enum |

**Test plan:**
```bash
pytest tests/test_structure.py tests/test_swing.py tests/test_zones.py tests/test_orderblock.py -v
```

---

### FP-10: Technical Tools — Indicators, Liquidity, SNR, PriceAction, Trendline
**Files:** `tools/indicators.py` (116), `tools/liquidity.py` (154), `tools/snr.py` (88), `tools/price_action.py` (115), `tools/trendline.py` (210)
**Tests:** `tests/test_indicators.py` (147), `tests/test_liquidity.py` (108), `tests/test_snr.py` (110), `tests/test_price_action.py` (107), `tests/test_trendline.py` (179)
**Total read:** ~683 prod + ~651 test = ~1,334 ✅

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | M-14 | 🟡 | RSI divergence: add ATR-scaled lookback |
| ☑ | M-20 | 🟡 | SNR clustering tolerance: pair-adaptive (berbeda untuk XAUUSD vs EURUSD) |
| ☑ | M-21 | 🟡 | Pin bar body ratio: extract ke config |
| ☑ | M-22 | 🟡 | Trendline ray extension: add validity bounds |
| ☑ | L-23 | 🔵 | EMA: cache computed values |
| ☑ | L-26 | 🔵 | Liquidity equal_highs tolerance: extract ke config |
| ☑ | L-29 | 🔵 | choch_filter numpy: evaluate replacing with pure math |
| ☑ | L-32 | 🔵 | Engulfing: add candle sequence check |
| ☑ | L-33 | 🔵 | Trendline: add failure logging |
| ☑ | L-34 | 🔵 | Scorer return: document dict structure |
| ☑ | CON-17 | 🔄 | Trendline return format alignment with chart overlay |
| ☑ | D-08 | 💀 | `is_touch_valid()`: hapus atau integrate |

**Test plan:**
```bash
pytest tests/test_indicators.py tests/test_liquidity.py tests/test_snr.py tests/test_price_action.py tests/test_trendline.py -v
```

---

### FP-11: Technical Tools — Scorer, Validator, CHoCH, DXY
**Files:** `tools/scorer.py` (90), `tools/validator.py` (95), `tools/choch_filter.py` (64), `tools/dxy_gate.py` (66)
**Tests:** `tests/test_validator.py` (116), `tests/test_choch_dxy.py` (143)
**Total read:** ~315 prod + ~259 test = ~574 ✅ compact

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | H-12 | 🟠 HIGH | Validator `must_not_counter_htf`: enforce (reject, bukan warning) |
| ☑ | M-18 | 🟡 | DXY correlation window: make adaptive |
| ☑ | M-19 | 🟡 | DXY gate: add proper enable/disable config path |
| ☑ | M-23 | 🟡 | Scorer penalties: move to strategy_rules config |
| ☑ | L-30 | 🔵 | DXY relevance score: document static threshold |
| ☑ | L-35 | 🔵 | Validator `_check_min_rr`: document default 1.5 |
| ☑ | L-36 | 🔵 | CHoCH correlation coefficient 0.6: document rationale |
| ☑ | L-37 | 🔵 | Scorer: add docstring for penalty section |
| ☑ | CON-14 | 🔄 | Score weights: verify they sum to MAX_POSSIBLE_SCORE |
| ☑ | CON-15 | 🔄 | Validator: standardize pass/fail return (always dict, never raise) |
| ☑ | CON-16 | 🔄 | DXY symbol naming consistency |
| ☑ | D-10 | 💀 | DXY gate: if keeping disabled, document why; if removing, clean up |

**Catatan:** D-09 (unused MIN_RR/SL_ATR_MULTIPLIER imports in validator) juga fix di sini.

**Test plan:**
```bash
pytest tests/test_validator.py tests/test_choch_dxy.py -v
```

---

### FP-12: Database, Repository, Config [MEDIUM]
**Files:** `database/models.py` (137), `database/repository.py` (218), `config/settings.py` (247), `config/strategy_rules.py` (102)
**Tests:** `tests/test_database.py` (150)
**Total read:** ~705 prod + ~150 test = ~855 ✅

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | H-13 | 🟠 HIGH | `trade_stats()`: count TRAIL_PROFIT + BE_HIT(positive) sebagai win |
| ☑ | M-26 | 🟡 | Dual config source: document precedence .env > strategy_rules |
| ☑ | M-27 | 🟡 | SQL: parameterized pagination limit |
| ☑ | M-28 | 🟡 | `MVP_PAIRS` dedup: single source of truth |
| ☑ | L-40 | 🔵 | Add DB index on `pair` column |
| ☑ | L-41 | 🔵 | `save_trade()`: return saved trade ID |
| ☑ | L-42 | 🔵 | `CHALLENGE_CENT` config: document format |
| ☑ | L-43 | 🔵 | `SCORING_WEIGHTS` keys: add documentation |
| ☑ | L-44 | 🔵 | `list_trades` limit default 50: review + document |
| ☑ | L-45 | 🔵 | `LOG_LEVEL` validation |
| ☑ | CON-19 | 🔄 | `demo_pnl` column naming: rename atau document usage in real mode |
| ☑ | CON-20 | 🔄 | `MIN_CONFLUENCE_SCORE=7` vs MAX=14 vs schema=15: single source |
| ☑ | D-11 | 💀 | `ALL_PAIRS`: hapus atau integrate usage |

**Test plan:**
```bash
pytest tests/test_database.py -v
# Tambah test: TRAIL_PROFIT counted as win in trade_stats
# Run migration test if schema changes
```

---

### FP-13: Post-Mortem, Error Handler, Demo Tracker [MEDIUM]
**Files:** `agent/post_mortem.py` (346), `agent/error_handler.py` (310), `agent/demo_tracker.py` (314)
**Tests:** `tests/test_post_mortem.py` (278), `tests/test_error_handler.py` (216), `tests/test_demo_tracker.py` (268)
**Total read:** ~970 prod + ~762 test = ~1,732 ⚠️ medium-large tapi manageable

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | H-17 | 🟠 HIGH | Error handler: fix HTTP status substring match → regex word boundary |
| ☑ | H-18 | 🟠 HIGH | `ModeManager.on_trade_closed`: catch MaxDrawdownExceeded |
| ☑ | M-33 | 🟡 | Post-mortem: add TRAIL_PROFIT ke win analysis |
| ☑ | M-34 | 🟡 | SL cause analysis: support multiple causes |
| ☑ | M-35 | 🟡 | Error handler: remove redundant timezone import |
| ☑ | M-36 | 🟡 | Error handler: add time-window stats / periodic reset |
| ☑ | M-37 | 🟡 | ModeManager: persist mode change ke DB |
| ☑ | M-38 | 🟡 | Demo tracker: count TRAIL_PROFIT as win |
| ☑ | L-58 | 🔵 | `SLCauseAnalysis.suggested_param_change`: type the dict |
| ☑ | L-59 | 🔵 | StateRecovery: use AnalysisState enum |
| ☑ | CON-26 | 🔄 | `from_dict()`: restore trades list atau document limitation |
| ☑ | D-15 | 💀 | StateRecovery: integrate ke startup atau mark as future |
| ☑ | D-16 | 💀 | DataFreshnessChecker: integrate ke fetcher atau mark as future |
| ☑ | D-17 | 💀 | DemoTracker: integrate ke lifecycle atau mark as future |

**Catatan:** D-15/D-16/D-17 → tidak langsung integrate ke production di phase ini (butuh FP-04/05 context). Cukup tambah TODO comments + improve internal code quality.

**Test plan:**
```bash
pytest tests/test_post_mortem.py tests/test_error_handler.py tests/test_demo_tracker.py -v
# Tambah test: TRAIL_PROFIT post-mortem
# Tambah test: HTTP status regex matching
# Tambah test: MaxDrawdownExceeded caught properly
```

---

### FP-14: Schemas [LOW]
**Files:** `schemas/plan.py` (73), `schemas/market_data.py` (61), `schemas/structure.py` (27), `schemas/zones.py` (61)
**Tests:** `tests/test_batch2_fixes.py` (353 — relevant sections), `tests/test_zones.py` (239)
**Total read:** ~220 prod + ~300 selected test = ~520 ✅ compact

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | L-53 | 🔵 | `htf_bias`: add validator atau Enum |
| ☑ | L-54 | 🔵 | `confidence`: add 0.0-1.0 bounds validator |
| ☑ | L-55 | 🔵 | `MarketStructure.events`: type as `list[StructureEvent]` |
| ☑ | L-56 | 🔵 | `Candle.volume`: document default=0.0 behavior |
| ☑ | L-57 | 🔵 | Zone type: use Enum consistently |
| ☑ | CON-23 | 🔄 | `confluence_score` validator: 0 to MAX_POSSIBLE_SCORE (bukan 15) |
| ☑ | CON-24 | 🔄 | Dual Zone definitions: consolidate |
| ☑ | CON-27 | 🔄 | `MarketStructure.events` → typed `StructureEvent` |
| ☑ | D-13 | 💀 | `schemas/structure.py`: integrate ke tools atau mark usage intent |
| ☑ | D-14 | 💀 | `schemas/zones.py`: integrate ke tools atau mark usage intent |

**Test plan:**
```bash
pytest tests/test_batch2_fixes.py tests/test_zones.py -v
# Tambah test: confidence bounds
# Tambah test: htf_bias validation
```

---

### FP-15: Notifier, Scheduler, Charts, Main [LOW]
**Files:** `notifier/handler.py` (192), `notifier/templates.py` (198), `notifier/whatsapp.py` (225), `scheduler/runner.py` (136), `charts/screenshot.py` (371), `main.py` (top-level ~200 relevant lines)
**Tests:** `tests/test_notifier.py` (254), `tests/test_chart.py` (154)
**Total read:** ~1,320 prod + ~408 test = ~1,728 ⚠️ borderline

**Strategi:** Bisa split jadi 15a (notifier/scheduler) + 15b (charts/main) jika context tight.

| Done | ID | Severity | Deskripsi |
|------|----|----------|-----------|
| ☑ | M-29 | 🟡 | templates.py: score "/15" → dynamic `f"/{MAX_POSSIBLE_SCORE}"` |
| ☑ | M-31 | 🟡 | handler.py: add WA alert for pending queue events |
| ☑ | M-39 | 🟡 | screenshot.py: lazy init singleton |
| ☑ | L-01 | 🔵 | main.py: remove unused `signal` import on Windows |
| ☑ | L-02 | 🔵 | main.py: `/health` endpoint add version info |
| ☑ | L-10 | 🔵 | main.py: PM2 restart counter persistence |
| ☑ | L-46 | 🔵 | runner.py: APScheduler job ID prefix |
| ☑ | L-47 | 🔵 | runner.py: misfire_grace_time review |
| ☑ | L-48 | 🔵 | handler.py: replace bare `except` with specific exceptions |
| ☑ | L-49 | 🔵 | whatsapp.py: document API key rotation |
| ☑ | L-51 | 🔵 | templates.py: mark i18n opportunity |
| ☑ | L-52 | 🔵 | handler.py: chart failure → degrade to text-only |
| ☑ | L-60 | 🔵 | screenshot.py: empty DataFrame guard |
| ☑ | L-61 | 🔵 | screenshot.py: rename `to_base64` → `to_data_uri` |
| ☑ | CON-03 | 🔄 | main.py: standardize log format |
| ☑ | CON-21 | 🔄 | (redundant with M-29 — score "/15") |
| ☑ | CON-25 | 🔄 | screenshot.py: add temp file cleanup |
| ☑ | D-02 | 💀 | main.py: clean up unused demo_run import |

**Test plan:**
```bash
pytest tests/test_notifier.py tests/test_chart.py -v
```

---

### FP-16: Integration Test & Full Regression
**No file edits — testing only**

```bash
# 1. Full test suite
pytest tests/ -v --timeout=60

# 2. Specific regression checks
pytest tests/test_production_full_cycle.py -v --timeout=120

# 3. Check for import errors
python -c "from agent.production_lifecycle import ProductionTradingLifecycle; print('OK')"
python -c "from dashboard.backend.main import app; print('OK')"

# 4. Lint check
python -m py_compile agent/production_lifecycle.py
python -m py_compile agent/trade_manager.py
python -m py_compile data/fetcher.py
python -m py_compile dashboard/backend/main.py
```

**Success criteria:** All 751+ tests pass, 0 new failures

---

### FP-17: VPS Deploy & Smoke Test

```bash
# 1. SSH ke VPS
ssh root@<vps-ip>

# 2. Pull changes
cd /opt/ai-forex-agent && git pull

# 3. Install deps jika ada perubahan
pip install -r requirements.txt

# 4. Run tests on VPS
pytest tests/ -x --timeout=60

# 5. Restart PM2
pm2 restart 3

# 6. Smoke test
curl -s http://localhost:8000/health | python -m json.tool

# 7. Check dashboard auth
curl -s http://localhost:8000/api/system/status  # should return 401

# 8. Check logs
pm2 logs 3 --lines 50
```

---

## Dependency Map

```
FP-01 (Dashboard Auth)     ─── independent, fix first
FP-02 (Trade Manager)      ─── independent
FP-03 (Fetcher/DNS)        ─── independent
FP-04 (Lifecycle Part A)   ─── depends on FP-02 (trade close format)
FP-05 (Lifecycle Part B)   ─── depends on FP-04
FP-06 (AI Client)          ─── depends on FP-03 (fetcher async)
FP-07 (Pending/Orch)       ─── depends on FP-06 (AI response)
FP-08 (Prompt/Voting)      ─── depends on FP-07
FP-09 (Structure/Zones)    ─── independent
FP-10 (Indicators etc.)    ─── independent
FP-11 (Scorer/Validator)   ─── depends on FP-09 (zone format)
FP-12 (DB/Config)          ─── depends on FP-02 (TRAIL_PROFIT def)
FP-13 (PM/Error/Demo)      ─── depends on FP-12 (TRAIL_PROFIT)
FP-14 (Schemas)            ─── depends on FP-12 (MAX_POSSIBLE_SCORE)
FP-15 (Notifier/Chart)     ─── depends on FP-14 (score display)
FP-16 (Integration Test)   ─── depends on ALL above
FP-17 (VPS Deploy)         ─── depends on FP-16
```

**Optimal execution order:**
```
Batch 1 (parallel-ready):  FP-01, FP-02, FP-03, FP-09, FP-10
Batch 2 (sequential):      FP-04 → FP-05
Batch 3 (sequential):      FP-06 → FP-07 → FP-08
Batch 4 (sequential):      FP-11 → FP-12 → FP-13 → FP-14 → FP-15
Batch 5 (final):           FP-16 → FP-17
```

---

## Context Management Rules

### Rule 1: Fresh Chat Per Phase
Mulai chat baru untuk setiap phase. Attach:
- File `FIX_PLAN.md` ini
- Target source files
- Target test files

### Rule 2: Read Before Edit
Jangan langsung edit. Langkah pertama selalu:
1. Baca file target (full)
2. Baca test terkait (relevant sections)
3. Konfirmasi: "Saya akan fix X, Y, Z di file ini. Approach: ..."
4. Baru mulai edit

### Rule 3: Test After Edit
Setelah setiap file diedit:
```bash
pytest tests/test_<related>.py -v
```
Setelah semua edits di phase selesai:
```bash
pytest tests/ -x --timeout=60
```

### Rule 4: Max File Budget Per Phase
- **Production files:** Max 3 files ATAU max ~1,500 lines total
- **Test files:** Max ~1,000 lines (read relevant sections)
- **Total per phase:** Target <3,000 lines to keep context well under limit

### Rule 5: Checkpoint di FIX_PLAN.md
Setelah setiap phase selesai, update checklist ☐ → ☑ di file ini.

### Rule 6: Jangan Gabung Phase Besar
Jika phase terasa besar (>1,200 lines prod), JANGAN gabung dengan phase lain. Lebih baik kecil tapi pasti daripada besar tapi halusinasi.

### Rule 7: Ketika Ragu, Baca Ulang
Jika tidak yakin tentang behavior suatu function, BACA file dulu. Jangan assume dari memory.

---

*Plan created. 168 issues, 15 fix phases + 2 final phases. Ready to execute.*
