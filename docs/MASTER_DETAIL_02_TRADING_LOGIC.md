# MASTER DETAIL 02 - TRADING LOGIC (Evidence-Only)

Tanggal: 2026-03-01 (Asia/Bangkok)  
Basis: kode saat ini di `ai-forex-agent` (tanpa asumsi)

## 1. Alur Keputusan Trade (Scan -> Plan -> Open)

1. Batch scan mengumpulkan semua hasil pair, filter skor minimal, urutkan skor tertinggi, lalu apply correlation filter sebelum open trade.  
   Bukti: `main.py:128-222`.
2. Per pair scan memanggil orchestrator, push hasil ke dashboard, push state change, trigger notifikasi state-change.  
   Bukti: `main.py:225-286`.
3. Orchestrator menjalankan:
   - `_phase_analyze()` (kumpulkan data tools lokal + Gemini structured output `SetupCandidate`)
   - keputusan voting (`REJECT`/`SKIP`/`VOTE`)
   - `_phase_vote()` bila perlu
   - `_phase_output()` untuk `TradingPlan` final.  
   Bukti: `agent/orchestrator.py:113-239`, `agent/orchestrator.py:298-379`, `agent/orchestrator.py:475-570`.

## 2. Logika Scoring & Voting

1. Score awal dari Gemini di-override dengan scorer lokal deterministik bila beda.  
   Bukti: `agent/orchestrator.py:356-371`, `tools/scorer.py:27-105`.
2. Rule keputusan voting:
   - `score >= high` -> skip voting
   - `low <= score < high` -> ensemble vote
   - `score < low` -> reject  
   Bukti: `agent/voting.py:64-75`.
3. Voting meng-cluster kandidat berdasarkan arah + midpoint zona entry dalam toleransi `0.3 * ATR`, lalu majority merge.  
   Bukti: `agent/voting.py:78-107`, `agent/voting.py:111-166`, `agent/voting.py:170-236`.

## 3. Guard Sebelum Open Posisi

1. `can_open_trade()` blok trade jika halted, drawdown breach, atau max concurrent tercapai.  
   Bukti: `agent/production_lifecycle.py:809-823`.
2. Saat `on_scan_complete()`:
   - prefetch harga,
   - lock trade,
   - cek pair aktif dan cooldown,
   - cek drawdown/openability.  
   Bukti: `agent/production_lifecycle.py:844-865`.
3. Entry hanya boleh jika harga live berada di zona entry (+buffer pips).  
   Bukti: `agent/production_lifecycle.py:447-473`, `agent/production_lifecycle.py:877-894`, `config/settings.py:267-270`.
4. Eksekusi entry memakai harga market aktual (`entry_price = real_price`), bukan memaksa midpoint zona.  
   Bukti: `agent/production_lifecycle.py:908-918`, `agent/production_lifecycle.py:946-949`.

## 4. Position Sizing, Challenge Mode, Balance Runtime

1. Dua mode sizing:
   - `risk_percent`: lot dihitung dari risk amount (`balance * risk_per_trade`)
   - `fixed_lot`: lot fixed dari `fixed_lot_size`  
   Bukti: `agent/production_lifecycle.py:431-440`.
2. Challenge mode:
   - `challenge_extreme`: pakai fixed lot, drawdown guard OFF, multiplier 1.0
   - `challenge_cent`: pakai fixed lot, drawdown guard OFF, multiplier cent.  
   Bukti: `agent/production_lifecycle.py:494-513`, `config/settings.py:277`.
3. Runtime config mutable via `update_runtime_config()` termasuk mode, lot, drawdown guard, revalidation interval, balance reset/HWM reset.  
   Bukti: `agent/production_lifecycle.py:515-575`, `agent/production_lifecycle.py:576-610`.

## 5. Monitoring Posisi Aktif

1. Loop monitor jalan tiap 60 detik.  
   Bukti: `main.py:293-327`.
2. `check_active_trades()`:
   - prefetch prices,
   - prefetch revalidation,
   - evaluasi action per trade via `TradeManager`.  
   Bukti: `agent/production_lifecycle.py:1024-1061`.
3. Revalidation setup aktif:
   - default aktif,
   - interval default 90 menit,
   - bisa diubah runtime (min 15 menit),
   - basis recheck H1/M15 + trend + CHOCH micro berlawanan saat RR negatif.  
   Bukti: `config/settings.py:272-275`, `agent/production_lifecycle.py:549-559`, `agent/production_lifecycle.py:700-760`.
4. Jika revalidation invalid -> action `CLOSE_MANUAL` otomatis.  
   Bukti: `agent/production_lifecycle.py:1048-1057`.

## 6. Aturan Manajemen Trade (Setelah Open)

`TradeManager.evaluate()` urutan utama:
1. SL hit -> close.
2. Struktur invalid -> manual close.
3. News imminent -> tighten/close.
4. TP2 hit -> full close.
5. TP1 hit -> partial 50%.
6. Trail (>= 1.5R).
7. BE move (>= 1.0R).
8. HOLD.  
Bukti: `agent/trade_manager.py:215-323`.

Implementasi di lifecycle:
1. TP1 partial benar-benar realisasi 50%, update balance, lalu SL -> BE untuk sisa posisi.  
   Bukti: `agent/production_lifecycle.py:1079-1111`.
2. SL_PLUS_BE dan TRAIL mengubah SL dan dipersist.  
   Bukti: `agent/production_lifecycle.py:1113-1129`, `agent/trade_manager.py:385-397`.

## 7. Drawdown Guard & Halt Logic

1. Drawdown check memakai **effective balance** = balance + unrealized floating P/L.  
   Bukti: `agent/production_lifecycle.py:775-807`.
2. Breach daily/total drawdown -> `_halted=True`, alasan tersimpan.  
   Bukti: `agent/production_lifecycle.py:783-805`.
3. Jika drawdown guard dimatikan (manual/challenge), halt dibuka kembali.  
   Bukti: `agent/production_lifecycle.py:534-539`, `agent/production_lifecycle.py:218-220`.

## 8. Close Pipeline & Jurnal

`_close_trade()` melakukan pipeline lengkap:
1. Lepas active trade + set cooldown pair.
2. Hitung pips/RR/PnL final (termasuk reclass SL->BE bila SL sudah dipindah ke BE).
3. Simpan close_result.
4. Generate post-mortem.
5. Persist DB `Trade`.
6. Push dashboard.
7. Kirim WA.
8. Check drawdown + save state.  
Bukti: `agent/production_lifecycle.py:1141-1327`.

Manual close dashboard memakai pipeline yang sama (jadi jurnal/DB/notif konsisten).  
Bukti: `agent/production_lifecycle.py:629-661`.

