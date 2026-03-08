# MASTER DETAIL 05 - CONSISTENCY GAPS & INCONSISTENCIES (Evidence-Only)

Tanggal: 2026-03-01 (Asia/Bangkok)

Dokumen ini hanya memuat gap yang bisa dibuktikan langsung dari file saat ini.

## 1. Dokumentasi Lama Menyebut "No manual close API" (Tidak Sinkron)

Temuan:
- `PROJECT_ANALYSIS.md` menyatakan keterbatasan: "No manual close API".  
  Bukti: `PROJECT_ANALYSIS.md:683-687`.
- Kode runtime saat ini sudah punya endpoint manual close + lifecycle handler.  
  Bukti: `dashboard/backend/main.py:578-605`, `agent/production_lifecycle.py:629-661`.

Dampak:
- Pembaca dokumen lama bisa salah memahami kemampuan sistem.

## 2. Dokumen Lama Masih Menyebut Finnhub sebagai Backend Aktif

Temuan:
- `PROJECT_ANATOMY_REPORT.md` masih menulis prioritas backend termasuk `FinnhubBackend`.  
  Bukti: `PROJECT_ANATOMY_REPORT.md:593-599`, `PROJECT_ANATOMY_REPORT.md:606-609`.
- Runtime fetcher saat ini tidak memiliki `FinnhubBackend`, hanya OANDA -> MT5 -> Demo (demo mode saja).  
  Bukti: `data/fetcher.py:565-632`.

Dampak:
- Arahan operasional/data-source dari dokumen lama jadi keliru.

## 3. Klaim "No Gemini cost tracking" Sudah Tidak Berlaku

Temuan:
- `PROJECT_ANATOMY_REPORT.md` menyebut tidak ada tracking budget/token.  
  Bukti: `PROJECT_ANATOMY_REPORT.md:666-673`.
- `GeminiClient` saat ini punya akumulasi token/cost, summary, budget flag, reset harian.  
  Bukti: `agent/gemini_client.py:160-257`.

Dampak:
- Audit cost berdasarkan dokumen lama bisa salah.

## 4. README/QUICKSTART Masih Memuat Catatan Finnhub

Temuan:
- README ada section `Finnhub Free Tier Limits`.  
  Bukti: `README.md:182`.
- QUICKSTART troubleshooting juga menyebut Finnhub rate limit.  
  Bukti: `QUICKSTART.md:177`.
- Runtime fetcher tidak lagi memakai Finnhub backend.  
  Bukti: `data/fetcher.py:565-632` + tidak ada simbol `FinnhubBackend` di file fetcher.

Dampak:
- Operator bisa menghabiskan waktu mengonfigurasi komponen yang tidak dipakai runtime.

## 5. System Prompt dan Registry DXY Tidak Sepenuhnya Selaras

Temuan:
- Prompt menyebut `index_correlation` disabled kecuali DXY explicit, dengan referensi Finnhub.  
  Bukti: `agent/system_prompt.py:108-110`.
- Registry memang men-disable `dxy_relevance_score`.  
  Bukti: `agent/tool_registry.py:55-57`, `agent/tool_registry.py:78-79`.
- Namun enum `StrategyMode` masih tetap memuat `index_correlation`.  
  Bukti: `schemas/market_data.py:63-67`.

Dampak:
- Secara kontrak schema mode ini valid, tetapi tool gate DXY tidak aktif; kebijakan bergantung prompt, bukan hard-validation.

## 6. Klaim "Async multi-TF concurrent" vs Implementasi

Temuan:
- Docstring menyatakan koleksi multi-TF berjalan concurrent.  
  Bukti: `agent/context_builder.py:385`.
- Implementasi membuat dict coroutine lalu `await` satu per satu (tanpa `create_task/gather`).  
  Bukti: `agent/context_builder.py:387-393`.

Dampak:
- Performa bisa lebih lambat dari yang diharapkan dari deskripsi.

## 7. State Machine ACTIVE/CLOSED Tidak Terlihat Dipakai di Orchestrator Runtime

Temuan:
- Grafik transisi state machine mencakup `TRIGGERED -> ACTIVE -> CLOSED`.  
  Bukti: `agent/state_machine.py:57-63`.
- `run_scan()` orchestrator mendorong transisi sampai `TRIGGERED`; tidak ada transisi `ACTIVE/CLOSED` di path ini.  
  Bukti: `agent/orchestrator.py:185-193`.
- Pencarian codebase tidak menemukan pemanggilan `transition_to(AnalysisState.ACTIVE/CLOSED)` di modul runtime agent.  
  Bukti: hasil pencarian kode (`rg`) pada `agent/` + `main.py`.

Dampak:
- State dashboard/analisis pair bisa merepresentasikan lifecycle analisis, bukan lifecycle posisi secara penuh.

## 8. Test Suite Mengandung Artefak Finnhub

Temuan:
- Beberapa test masih mereferensikan `FINNHUB_API_KEY`, `FINNHUB_RESOLUTION`, `_finnhub_limiter`, atau fallback Finnhub.  
  Bukti: `tests/test_batch1_fixes.py:555-563`, `tests/test_batch2_fixes.py:253`, `tests/test_fetcher.py:9`, `tests/test_oanda_backend.py:270-276`.
- `data/fetcher.py` saat ini tidak memiliki simbol Finnhub tersebut.

Dampak:
- Risiko test lama gagal/irrelevan terhadap runtime aktual OANDA-first.

## 9. VERSION.md Berisi Snapshot State Historis

Temuan:
- `VERSION.md` berisi angka balance/trade yang bersifat snapshot tanggal rilis lama.  
  Bukti: `VERSION.md:1-45`.

Dampak:
- Dokumen ini tidak bisa dipakai sebagai status runtime sekarang tanpa verifikasi ulang.

## 10. Batasan Audit Historis

Temuan:
- Folder `ai-forex-agent` saat ini bukan git repository, sehingga timeline commit/refactor tidak bisa dibuktikan dari history git lokal.
  Bukti operasional: perintah `git status` pada folder ini mengembalikan `fatal: not a git repository`.

Dampak:
- Rekonstruksi "kapan tepatnya perubahan terjadi" harus berbasis marker kode saat ini (mis. komentar `FIX ...`), bukan log commit.
