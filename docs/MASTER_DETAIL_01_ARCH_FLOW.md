# MASTER DETAIL 01 - ARCHITECTURE & FLOW (Evidence-Only)

## A. Runtime Topology

Komponen aktif saat `main.py` jalan:
- `FastAPI app` dari dashboard backend sebagai single app server.
  Bukti: `main.py:50-61`.
- `ScanScheduler` (APScheduler) untuk jadwal sesi.
  Bukti: `main.py:490-500`, `scheduler/runner.py:62-104`.
- `ProductionLifecycle` untuk open/monitor/close trade.
  Bukti: `main.py:437-443`, `agent/production_lifecycle.py:102-107`.
- `AnalysisOrchestrator` per pair (lazy init).
  Bukti: `main.py:93-106`.
- `WhatsAppNotifier` + `NotificationHandler` jika nomor WA terisi.
  Bukti: `main.py:453-469`, `main.py:462-466`.

## B. Startup Flow (Step-by-Step)

1. Startup guard cegah inisialisasi ganda.
   Bukti: `main.py:421-426`.
2. Inisialisasi DB dan inject repo ke dashboard.
   Bukti: `main.py:431-434`.
3. Inisialisasi lifecycle + restore state dari DB.
   Bukti: `main.py:437-443`, `agent/production_lifecycle.py:176-229`.
4. Setup notifier WA opsional.
   Bukti: `main.py:453-468`.
5. Wire callback lifecycle ke dashboard/WA.
   Bukti: `main.py:470-484`.
6. Start price monitor async task.
   Bukti: `main.py:486-488`.
7. Configure + start scheduler.
   Bukti: `main.py:490-499`.
8. Trigger initial batch scan setelah delay 5 detik.
   Bukti: `main.py:507-513`.

## C. Scan Flow Per Batch

`scan_batch(pairs)`:
- Jalankan scan per pair.
- Kumpulkan outcome valid.
- Filter score minimal `MIN_SCORE_FOR_TRADE`.
- Urutkan score tertinggi.
- Terapkan correlation-group guard.
- Coba open trade via lifecycle.

Bukti: `main.py:128-222`.

## D. Scan Flow Per Pair

`_scan_pair_inner(pair)`:
1. Cek halted; jika halted skip.
2. Jalankan orchestrator `run_scan()`.
3. Push hasil analisis ke dashboard (`ANALYSIS_UPDATE`).
4. Push state change jika berubah.
5. Trigger notification handler untuk perubahan state.

Bukti: `main.py:225-286`.

## E. Analisis Orchestrator Flow

`AnalysisOrchestrator.run_scan()`:
1. Cek cooldown state-machine.
2. `_phase_analyze()`:
   - kumpulkan data multi-TF lokal via tools,
   - kirim context ke Gemini structured output (schema `SetupCandidate`),
   - override score dengan scorer lokal deterministik.
3. Tentukan decision voting berdasarkan score.
4. Jika `REJECT` -> stop.
5. Jika `SKIP` -> langsung `_phase_output()`.
6. Jika `VOTE` -> `_phase_vote()` lalu `_phase_output()`.

Bukti: `agent/orchestrator.py:123-240`, `agent/orchestrator.py:299-379`, `agent/orchestrator.py:475-570`.

Catatan penting:
- Runtime path saat ini memakai `agenerate_structured()` (schema mode), bukan `tool-calling` langsung Gemini.
  Bukti: `agent/orchestrator.py:343-347`, `agent/orchestrator.py:496-505`, `agent/orchestrator.py:544-548`.

## F. Monitoring Flow

`price_monitor_loop()` tiap 60 detik:
- jika ada active trade: `check_active_trades()`,
- push portfolio update WS,
- autosave active trades + state setiap 5 siklus.

Bukti: `main.py:293-327`.

`check_active_trades()`:
- prefetch harga,
- prefetch revalidasi setup pair aktif,
- evaluasi action per trade via `TradeManager`,
- close/partial/BE/trail sesuai action.

Bukti: `agent/production_lifecycle.py:1024-1120`.

## G. Close Flow

`_close_trade()` melakukan:
1. Hitung PnL final (termasuk handling BE setelah SL moved).
2. Tambah record `_closed_today`.
3. Generate post-mortem.
4. Simpan ke DB `trades`.
5. Push ke dashboard.
6. Kirim notifikasi WA.
7. Cek drawdown ulang.
8. Save state.

Bukti: `agent/production_lifecycle.py:1141-1327`.

## H. Shutdown Flow

1. Cancel price monitor task.
2. Save active trades.
3. Save lifecycle state.
4. Close WA notifier client.
5. Shutdown scheduler.

Bukti: `main.py:587-631`.

