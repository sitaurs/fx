# MASTER DETAIL 03 - TOOLS, APIS, DAN DATA FLOW (Evidence-Only)

Tanggal: 2026-03-01 (Asia/Bangkok)

## 1. Pipeline Tools Lokal (Deterministik)

`context_builder` menjalankan urutan analisis berbasis OHLCV secara lokal:
1. fetch_ohlcv  
2. ATR/EMA/RSI  
3. swing  
4. BOS/CHOCH  
5. SNR  
6. SnD  
7. OB  
8. trendline  
9. EQH/EQL + sweep  
10. pin bar/engulfing  
11. CHOCH micro  

Bukti:
- deskripsi flow: `agent/context_builder.py:10-13`
- implementasi step: `agent/context_builder.py:75-127`
- output terstruktur per TF: `agent/context_builder.py:136-169`
- formatter context untuk Gemini: `agent/context_builder.py:196-359`

## 2. Registry Tools yang Terpasang

`ALL_TOOLS` berisi 16 fungsi utama (indikator, struktur, zona, liquidity, price action, scorer, validator).  
Bukti: `agent/tool_registry.py:63-90`.

Catatan faktual:
- DXY gate saat ini di-disable di registry.  
  Bukti: `agent/tool_registry.py:55-57`, `agent/tool_registry.py:78-79`.

## 3. Scoring & Validation Rule Engine

1. Bobot scoring ada di `config/strategy_rules.py` (total positif 14).  
   Bukti: `config/strategy_rules.py:13-30`.
2. Scorer menghitung weighted sum + penalty, clamp 0..max, tradeable jika score >= 5.  
   Bukti: `tools/scorer.py:27-105`.
3. Validator hard-rules:
   - min RR,
   - SL min/max ATR multiple,
   - zone freshness,
   - warning counter-trend.  
   Bukti: `tools/validator.py:22-104`.

## 4. Schema Output Agent

1. `SetupCandidate` mendefinisikan field entry zone, SL/TP, RR, TTL, confluence score, rationale.
2. `TradingPlan` memuat `primary_setup`, optional `alternative_setup`, risk warnings, confidence, expiry.

Bukti: `schemas/plan.py:18-63`, `schemas/plan.py:66-81`.

Enum strategy mode yang tersedia di schema:
- `index_correlation`
- `sniper_confluence`
- `scalping_channel`  
Bukti: `schemas/market_data.py:63-67`.

## 5. Data Backend Runtime (OANDA-First)

`data/fetcher.py` memilih backend runtime dengan prioritas:
1. OANDA (jika key+account tersedia dan probe berhasil)
2. MT5 API lokal (jika URL tersedia dan probe berhasil)
3. Demo backend (hanya jika `TRADING_MODE=demo`)  

Bukti: `data/fetcher.py:565-632`.

Guard penting:
- `TRADING_MODE=real` tanpa backend OANDA/MT5 valid -> raise RuntimeError (refuse start).  
  Bukti: `data/fetcher.py:613-622`.

## 6. OANDA Integration Details

1. OANDA candle endpoint dipakai via `/v3/instruments/{instrument}/candles`.  
   Bukti: `data/fetcher.py:9-14`, `data/fetcher.py:309-313`.
2. Mapping pair -> instrument OANDA disediakan eksplisit (XAUUSD, EURUSD, GBPJPY, USDCHF, USDCAD, USDJPY).  
   Bukti: `data/fetcher.py:66-73`.
3. Base URL practice/live auto-detect dari prefix account ID (`101-` vs `001-`).  
   Bukti: `config/settings.py:45-56`.

## 7. DNS Override untuk OANDA

Fetcher memasang override DNS `socket.getaddrinfo` untuk domain OANDA, plus refresh via DoH Cloudflare/Google.  
Bukti:
- DoH resolve: `data/fetcher.py:110-132`
- refresh overrides: `data/fetcher.py:139-163`
- patched getaddrinfo: `data/fetcher.py:164-190`
- default override map: `config/settings.py:57-64`

## 8. Gemini API Integration

1. Pemetaan state -> model:
   - Flash: SCANNING/WATCHING/ACTIVE/CLOSED/CANCELLED
   - Pro: APPROACHING/TRIGGERED  
   Bukti: `agent/gemini_client.py:89-97`.
2. Structured output mode dipakai dengan `response_schema` Pydantic dan `response_mime_type="application/json"`.  
   Bukti: `agent/gemini_client.py:137-144`.
3. Orchestrator memanggil `agenerate_structured()` untuk analyze/vote/output.  
   Bukti: `agent/orchestrator.py:343-347`, `agent/orchestrator.py:496-505`, `agent/orchestrator.py:544-548`.
4. Usage tracking Gemini tersedia:
   - token input/output,
   - estimasi cost,
   - budget summary,
   - budget exceeded warning.  
   Bukti: `agent/gemini_client.py:160-257`.

## 9. WhatsApp API Integration

1. Notifier memakai base URL + phone dari settings/env.  
   Bukti: `notifier/whatsapp.py:115-120`, `config/settings.py:230-234`.
2. Format nomor otomatis:
   - jika `08...` -> dikonversi ke `62...`
   - output ke format JID `...@s.whatsapp.net`.  
   Bukti: `notifier/whatsapp.py:155-167`.
3. Endpoint yang dipakai:
   - text: `POST {base}/send/message`
   - image: `POST {base}/send/image`  
   Bukti: `notifier/whatsapp.py:226-239`, `notifier/whatsapp.py:240-264`.

## 10. MT5 Bridge (Fallback Sekunder)

Agent menganggap endpoint MT5 lokal `/ohlcv` sebagai fallback data.  
Bukti: `data/fetcher.py:390-421`, `data/fetcher.py:596-604`.

Bridge yang ada di folder terpisah (`app_2`) berbasis Flask + MetaTrader5:
- app startup: `app_2/app/app.py:22-42`
- route OHLCV: `app_2/app/routes/data.py:243-355`

## 11. API Eksternal yang Terbukti Dipakai

1. OANDA v20 REST (`candles`, `instruments account`)  
   Bukti: `data/fetcher.py:309-313`, `data/fetcher.py:373-376`.
2. Gemini via `google-genai` structured output  
   Bukti: `agent/gemini_client.py:327-352`.
3. go-whatsapp REST (`/send/message`, `/send/image`)  
   Bukti: `notifier/whatsapp.py:232-239`, `notifier/whatsapp.py:254-264`.
4. MT5 local bridge `/ohlcv` (fallback, bukan prioritas utama)  
   Bukti: `data/fetcher.py:420`, `app_2/app/routes/data.py:243-355`.

