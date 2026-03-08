# OVERVIEW AI FOREX AGENT

Tanggal: 2026-03-01

## 1) Proyek Ini Apa

`AI Forex Agent` adalah sistem analisis + manajemen trade forex/metal berbasis:
- backend `FastAPI`
- engine analisis `Gemini` (structured output)
- data market utama `OANDA v20`
- dashboard web real-time (REST + WebSocket)
- lifecycle trade lengkap (open, monitor, partial, BE, trail, close, jurnal)

Ringkasnya: ini bukan hanya scanner sinyal, tapi runtime agent trading end-to-end dengan kontrol risiko, scheduler sesi, notifikasi, dan pencatatan hasil.

## 2) Tujuan Sistem

1. Menghasilkan setup teknikal multi-timeframe berbasis data OHLCV.
2. Menjalankan eksekusi yang disiplin (guard entry-zone, drawdown guard, cooldown, max concurrent).
3. Mengelola posisi aktif secara otomatis (monitor berkala + revalidasi setup aktif).
4. Menyediakan kontrol runtime dari dashboard (challenge mode, lot, balance, drawdown guard, manual close).
5. Menyimpan jejak trade/jurnal post-mortem agar strategi bisa diaudit dan dikembangkan.

## 3) Fitur Utama

1. Scan batch lintas pair + ranking skor + correlation filter.
2. Orchestrator analisis dengan voting engine.
3. Entry gate: open hanya jika harga live masuk zona entry (dengan buffer).
4. Monitoring 60 detik: SL/TP, partial TP1 50%, BE, trail, manual/auto close.
5. Active revalidation berkala untuk pair aktif (default 90 menit, dapat diubah runtime).
6. Dashboard: KPI portfolio, posisi aktif, AI radar, live events, equity curve, trade history.
7. Runtime controls: challenge mode (`extreme`/`cent`), fixed lot/risk mode, set balance, reset default, toggle drawdown guard.
8. Manual close dari UI dan tercatat ke pipeline jurnal/DB/notifikasi yang sama.
9. Integrasi WhatsApp (go-whatsapp) untuk alert runtime.

## 4) Strategi yang Dipakai (Level Sistem)

Secara runtime, setup menggunakan komponen konfluensi teknikal:
- market structure (BOS/CHOCH)
- supply/demand
- support/resistance (SNR)
- order block
- liquidity sweep
- trendline
- price action (pin bar/engulfing)
- indikator pendukung (ATR/EMA/RSI)

Strategi mode di kontrak schema:
- `index_correlation`
- `sniper_confluence`
- `scalping_channel`

Catatan penting:
- Jalur runtime sekarang menekankan data lokal deterministik + structured output.
- DXY tool gate di registry saat ini non-aktif, sehingga mode yang butuh DXY harus diperlakukan hati-hati pada level implementasi.

## 5) Cara Kerja (Runtime Singkat)

1. Startup:
   - init DB
   - init lifecycle (restore state + restore active trades)
   - init notifier
   - start monitor loop
   - start scheduler
   - jalankan initial batch scan
2. Batch scan:
   - scan semua pair
   - kumpulkan outcome
   - filter minimum score
   - ranking skor
   - correlation filter
   - open trade jika lolos semua guard
3. Saat posisi aktif:
   - monitor harga tiap 60 detik
   - revalidasi setup aktif berkala
   - jalankan rules manager (partial/BE/trail/close)
4. Saat close:
   - hitung pips/RR/PnL final
   - simpan post-mortem
   - persist DB
   - push dashboard
   - kirim notifikasi
   - save state

## 6) Flow Besar Sistem

`Data (OANDA) -> Tools lokal -> Context Builder -> Gemini Structured -> Voting/Output Plan -> Lifecycle Open Guard -> Active Monitor/Revalidation -> Close Pipeline -> Journal/DB/Dashboard/Notif`

## 7) Tools & API yang Dipakai

1. **Market data utama:** OANDA v20 REST.
2. **Fallback data sekunder:** MT5 local bridge (`/ohlcv`) jika disiapkan.
3. **AI model:** Gemini (`google-genai`) structured output (`SetupCandidate`/`TradingPlan`).
4. **Dashboard API:** FastAPI REST + WebSocket.
5. **Notifikasi:** go-whatsapp API (`/send/message`, `/send/image`).
6. **Persistensi:** SQLite via SQLAlchemy async repository.

## 8) Analisis Kondisi Saat Ini (Ringkas)

1. Arsitektur inti sudah kuat untuk production loop (scan -> open -> manage -> close -> journal).
2. Fitur kontrol runtime (challenge/lot/balance/manual close/revalidation) sudah tersedia end-to-end.
3. Masih ada gap konsistensi dokumentasi lama vs runtime aktual (terutama dokumen yang masih menyebut Finnhub/no-manual-close).
4. Untuk pengembangan lanjut, baseline paling aman adalah menjadikan dokumen `MASTER_*` sebagai source of truth terbaru.

## 9) Rujukan Dokumen Master

- `MASTER_CONTEXT_AI_FOREX_AGENT.md`
- `MASTER_DETAIL_01_ARCH_FLOW.md`
- `MASTER_DETAIL_02_TRADING_LOGIC.md`
- `MASTER_DETAIL_03_TOOLS_APIS_DATA.md`
- `MASTER_DETAIL_04_DASHBOARD_RUNTIME.md`
- `MASTER_DETAIL_05_CONSISTENCY_GAPS.md`

