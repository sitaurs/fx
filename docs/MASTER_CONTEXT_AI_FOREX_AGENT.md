# MASTER CONTEXT AI FOREX AGENT (Evidence-Only)

Tanggal: 2026-03-01 (Asia/Bangkok)
Lokasi analisis: `d:\projct\ai-anlys\ai-forex-agent`

## 1. Aturan Dokumen Ini

- Semua poin di bawah berbasis bukti file kode saat ini.
- Tidak ada klaim berbasis asumsi, memori lama, atau dugaan.
- Referensi bukti ditulis format `path:line-line`.
- Folder ini bukan git repo (`git status` gagal), jadi riwayat commit tidak bisa dipakai sebagai bukti timeline perubahan.
  Bukti: `git status` di `ai-forex-agent` menghasilkan `fatal: not a git repository`.

## 2. Ini Proyek Apa

Ini adalah sistem agent analisis dan manajemen trade forex/metal berbasis FastAPI + Gemini + OANDA, dengan dashboard web real-time, scheduler sesi trading, lifecycle trade, jurnal post-mortem, dan notifikasi WhatsApp.

Bukti utama:
- Entry-point dan komponen runtime: `main.py:1-16`, `main.py:29-61`, `main.py:415-525`.
- Lifecycle trading: `agent/production_lifecycle.py:102-107`.
- Orchestrator analisis: `agent/orchestrator.py:66-83`.
- Dashboard API + WS: `dashboard/backend/main.py:134-138`, `dashboard/backend/main.py:656-669`.
- OANDA backend utama: `data/fetcher.py:565-632`.
- WhatsApp notifier: `notifier/whatsapp.py:95-117`, `notifier/whatsapp.py:226-264`.

## 3. Arsitektur Singkat (Runtime Aktual)

1. `python main.py` jalankan uvicorn untuk app FastAPI gabungan agent + dashboard.
   Bukti: `main.py:638-657`, `main.py:50-61`.
2. Saat startup: init DB, init lifecycle, wire dashboard callbacks, init notifier WhatsApp, start price monitor, start scheduler, jalankan initial batch scan.
   Bukti: `main.py:430-513`.
3. Scan batch:
   - Scan semua pair.
   - Filter score minimal.
   - Ranking descending.
   - Correlation filter per group.
   - Open trade lewat lifecycle.
   Bukti: `main.py:128-222`.
4. Open trade hanya jika:
   - tidak halted,
   - tidak melampaui max concurrent,
   - lolos cooldown pair,
   - harga live berada dalam entry zone (plus buffer).
   Bukti: `agent/production_lifecycle.py:809-823`, `agent/production_lifecycle.py:854-896`, `agent/production_lifecycle.py:447-473`.
5. Entry eksekusi memakai harga market real-time (bukan dipaksa titik tengah zona).
   Bukti: `agent/production_lifecycle.py:908-918`, `agent/production_lifecycle.py:946-949`.
6. Monitor posisi tiap 60 detik:
   - prefetch harga,
   - revalidasi struktur pair aktif berkala,
   - evaluasi SL/TP/partial/BE/trail/manual close reason,
   - close pipeline + jurnal + DB + WS + WA.
   Bukti: `main.py:293-327`, `agent/production_lifecycle.py:700-773`, `agent/production_lifecycle.py:1024-1120`, `agent/production_lifecycle.py:1141-1327`.

## 4. API Eksternal yang Dipakai

1. OANDA v20 REST (utama data OHLCV)
- Endpoint candles: `/v3/instruments/{instrument}/candles`.
- Endpoint instruments account: `/v3/accounts/{account_id}/instruments`.
- Bukti: `data/fetcher.py:196-203`, `data/fetcher.py:309`, `data/fetcher.py:373`.

2. Gemini via `google-genai`
- Dipakai dalam mode structured output (schema Pydantic) pada orchestrator.
- Mapping model by state: Flash untuk scan/watching/active, Pro untuk approaching/triggered.
- Bukti: `agent/gemini_client.py:89-97`, `agent/gemini_client.py:137-144`, `agent/orchestrator.py:343-347`, `agent/orchestrator.py:496-505`, `agent/orchestrator.py:544-548`.

3. go-whatsapp REST
- Endpoint text: `/send/message`.
- Endpoint image: `/send/image`.
- Bukti: `notifier/whatsapp.py:232-239`, `notifier/whatsapp.py:254-264`.

4. MT5 API lokal (fallback sekunder jika OANDA unavailable)
- Endpoint yang diharapkan: `/ohlcv`.
- Bukti: `data/fetcher.py:390-421`.
- Implementasi bridge MT5 terpisah ada di `app_2/app` (Flask + MetaTrader5).
  Bukti: `app_2/app/app.py:11-16`, `app_2/app/routes/data.py:243-355`.

## 5. Konfigurasi Lingkungan Terbaca Saat Ini (Non-Secret)

Di `.env`, nilai berikut terdeteksi:
- `WHATSAPP_API_URL=http://13.55.23.245:3000`
- `WHATSAPP_PHONE=081358959349`
- `TRADING_MODE=real`
- `ENTRY_ZONE_EXECUTION_BUFFER_PIPS=0.0`
- `MT5_OHLCV_API_URL=http://127.0.0.1:5001`
- `OANDA_ACCOUNT_ID=101-003-38602601-001`

Sumber: `.env` (difilter key non-secret saja).

## 6. Endpoint Dashboard Utama

Daftar endpoint terdaftar:
- `GET /`
- `GET /api/health`
- `GET /api/events`
- `GET /api/portfolio`
- `GET /api/portfolio/equity`
- `GET /api/system/status`
- `GET /api/system/config`
- `PATCH /api/system/config`
- `POST /api/system/config/reset-default`
- `POST /api/system/balance/set`
- `POST /api/system/unhalt`
- `GET /api/analysis/live`
- `GET /api/analysis/{pair}`
- `GET /api/trades`
- `GET /api/trades/{trade_id}`
- `POST /api/positions/{trade_id}/close`
- `GET /api/stats/daily`
- `WS /ws`

Bukti: `dashboard/backend/main.py` anotasi route di `170`, `186`, `227`, `243`, `392`, `414`, `447`, `455`, `468`, `478`, `499`, `520`, `528`, `541`, `557`, `578`, `640`, `656`.

## 7. Fakta Kritis Menjawab Isu Lama Anda

1. Posisi aktif sekarang memang dipantau periodik
- Price monitor loop 60 detik aktif.
  Bukti: `main.py:293-327`.
- Ada active revalidation berkala berbasis struktur H1/M15 dan CHOCH micro.
  Bukti: `agent/production_lifecycle.py:700-760`.
- Interval default 90 menit.
  Bukti: `config/settings.py:272-275`.

2. Manual close sudah ada, dan masuk jurnal/pipeline close yang sama
- API manual close: `POST /api/positions/{trade_id}/close`.
  Bukti: `dashboard/backend/main.py:578-605`.
- Lifecycle `manual_close_trade()` memanggil `_close_trade()` yang sama.
  Bukti: `agent/production_lifecycle.py:629-661`.
- `_close_trade()` menyimpan post-mortem dan DB `Trade`.
  Bukti: `agent/production_lifecycle.py:1230-1294`.

3. Challenge mode sudah ada (extreme/cent)
- Runtime config: `challenge_extreme`, `challenge_cent`.
  Bukti: `agent/production_lifecycle.py:494-513`.
- Keduanya memaksa `fixed_lot` dan mematikan drawdown guard.
  Bukti: `agent/production_lifecycle.py:498-500`, `agent/production_lifecycle.py:506-508`.

4. Eksekusi entry: zone-based gate, fill di market price
- Gate: harga harus di dalam `[entry_zone_low..entry_zone_high]` plus buffer.
  Bukti: `agent/production_lifecycle.py:877-894`, `agent/production_lifecycle.py:447-473`.
- Fill: `entry_price = real_price`.
  Bukti: `agent/production_lifecycle.py:946`.

## 8. Peta Bacaan Lanjutan (Dokumen Turunan)

- [MASTER_DETAIL_01_ARCH_FLOW.md](./MASTER_DETAIL_01_ARCH_FLOW.md)
- [MASTER_DETAIL_02_TRADING_LOGIC.md](./MASTER_DETAIL_02_TRADING_LOGIC.md)
- [MASTER_DETAIL_03_TOOLS_APIS_DATA.md](./MASTER_DETAIL_03_TOOLS_APIS_DATA.md)
- [MASTER_DETAIL_04_DASHBOARD_RUNTIME.md](./MASTER_DETAIL_04_DASHBOARD_RUNTIME.md)
- [MASTER_DETAIL_05_CONSISTENCY_GAPS.md](./MASTER_DETAIL_05_CONSISTENCY_GAPS.md)
- [STRATEGI_FOREX_DARI_GEMINI_CHATGPT.md](./STRATEGI_FOREX_DARI_GEMINI_CHATGPT.md)
- [PATCH-2026-03-01.md](./PATCH-2026-03-01.md)

