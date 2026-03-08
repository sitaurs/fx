# PHASE 4 AUDIT — Technical Tools 2 (Filters, Scoring, Validation)

**Tanggal:** 2025-06-05  
**Cakupan:** 7 file, ~810 baris kode  
**Auditor:** AI Audit Agent

---

## File yang Diaudit

| # | File | Lines | Fungsi Utama |
|---|------|-------|--------------|
| 1 | `tools/choch_filter.py` | ~80 | Micro CHOCH detection di LTF (M5/M15) |
| 2 | `tools/dxy_gate.py` | ~80 | DXY correlation gate (DISABLED) |
| 3 | `tools/snr.py` | ~100 | S/R level detection via swing clustering |
| 4 | `tools/price_action.py` | ~130 | Pin bar & engulfing detection |
| 5 | `tools/trendline.py` | ~240 | RAY-based trendline detection |
| 6 | `tools/scorer.py` | ~100 | Setup scoring engine (weighted boolean) |
| 7 | `tools/validator.py` | ~80 | Trading plan validator (hard rules) |

---

## Ringkasan Temuan

| Severity | Count | Keterangan |
|----------|-------|-----------|
| 🔴 CRITICAL | 0 | - |
| 🟠 HIGH | 2 | Aturan anti-rungkad hanya prompt, validator vs config mismatch |
| 🟡 MEDIUM | 6 | Fitur DXY incomplete, clustering suboptimal, boolean-only scoring |
| 🔵 LOW | 9 | Unused imports, hardcoded magic, minor edge cases |
| 💀 DEAD CODE | 3 | `is_touch_valid`, `MIN_RR`/`SL_ATR_MULTIPLIER` import, `dxy_gate` disabled |
| 🔄 CONSISTENCY | 4 | Config vs enforcement mismatch, tolerance inconsistency |

---

## Detail Temuan per File

---

### 1. `tools/choch_filter.py` (~80 lines)

**Deskripsi:** Deteksi micro Change-of-Character pada LTF (M5/M15) sebagai entry confirmation setelah harga masuk zone HTF.

#### Arsitektur
- Input: OHLCV list + direction + ATR
- Logic: Iterasi mundur dari bar terbaru, cari close yang break beyond prior swing + threshold (0.3 × ATR)
- Output: `{confirmed, break_index, break_price}`
- **Status Produksi:** ✅ AKTIF — imported di `context_builder.py` dan `tool_registry.py`

#### Temuan

**🟡 M-01: Kompleksitas O(n²) pada perhitungan prior_high/prior_low**
```python
# Untuk setiap bar i, recompute max dari semua bar sebelumnya
for i in range(len(segment) - 1, 0, -1):
    prior_high = max(c["high"] for c in segment[:i])  # O(i) per iterasi
```
- **Impact:** Dengan lookback=10, ini O(100) — negligible. Tapi jika lookback ditingkatkan, bisa lambat.
- **Fix:** Gunakan running max/min yang di-precompute sekali.
- **Priority:** Low (lookback kecil di production)

**🔵 L-01: ATR fallback estimation bukan True ATR**
```python
if atr is None or atr <= 0:
    ranges = [c["high"] - c["low"] for c in segment]
    atr = sum(ranges) / len(ranges)
```
- Average range ≠ True Range (tidak memperhitungkan gap dari previous close).
- **Impact:** Untuk LTF (M5/M15), gap jarang terjadi. Minimal impact.
- **Fix:** Caller should always supply ATR (context_builder.py does this).

**🔵 L-02: Tidak ada validasi key pada OHLCV dict**
- Jika dict tidak punya key `high`, `low`, `close` → `KeyError`.
- **Impact:** Low — data selalu dari fetcher yang sudah terstandar.

---

### 2. `tools/dxy_gate.py` (~80 lines)

**Deskripsi:** Gate korelasi DXY/index untuk konfirmasi tambahan. Menghitung Pearson correlation antara pair returns dan DXY returns.

#### Arsitektur
- Input: OHLCV pair + OHLCV index + window
- Logic: Log returns → Pearson correlation → relevance threshold
- Output: `{correlation, relevant, direction}`
- **Status Produksi:** ❌ DISABLED — commented out di `tool_registry.py`

#### Temuan

**🟡 M-02: Fitur incomplete vs dokumentasi algorithm**
```python
# Docstring/comment menyebutkan:
# "DXY at zone + rejection → active confirmation"
# "Volatility spike → reduce weight"
# TAPI keduanya TIDAK diimplementasikan.
```
- Hanya correlation computation yang ada. Zone detection dan volatility spike handling tidak ada.
- **Impact:** Sekarang disabled, jadi tidak ada impact langsung. Tapi jika di-enable, fitur tidak lengkap.
- **Fix:** Implementasi lengkap sebelum enable, atau update docstring.

**🔵 L-03: Minimum 10 returns hardcoded tanpa penjelasan**
```python
if len(pair_returns) < 10:
    return {"correlation": 0.0, "relevant": False, "direction": "neutral"}
```
- **Impact:** Low — ini safety check yang reasonable tapi angka 10 sebaiknya jadi constant.

**💀 DEAD-01: Module disabled di production**
- `tool_registry.py` baris 57: `# from tools.dxy_gate import dxy_relevance_score`
- `tool_registry.py` baris 79: `# dxy_relevance_score,`
- Code maintained tapi tidak dipanggil. Masterplan menyebutkan DXY sebagai fitur, tapi implementation incomplete.

---

### 3. `tools/snr.py` (~100 lines)

**Deskripsi:** Deteksi Support/Resistance level via clustering swing points multi-TF.

#### Arsitektur
- Input: List swing points + ATR + cluster distance multiplier
- Logic: Greedy sort-by-price clustering → TF-weighted scoring → major/minor classification
- Output: `{levels: [{price, touches, score, is_major, source_tf, ...}]}`
- **Status Produksi:** ✅ AKTIF — imported di `context_builder.py` dan `tool_registry.py`

#### Temuan

**🟡 M-03: Greedy clustering anchor-based bisa produce suboptimal clusters**
```python
anchor_price = current_cluster[0]["price"]  # FIX F2-07
if abs(sw["price"] - anchor_price) <= cluster_dist:
    current_cluster.append(sw)
```
- FIX F2-07 memperbaiki drifting mean → anchor. Tapi anchor-based masih punya masalah:
- **Contoh:** Cluster dist = 5 pips. Swings: 1.1000, 1.1004, 1.1008. Semua masuk cluster karena dekat anchor (1.1000). Tapi 1.1008 sebenarnya 8 pips dari anchor — melebihi 5 pip. Wait, 1.1008 - 1.1000 = 0.0008 = 8 pips? Depends on cluster_dist.
- Masalah: Element terakhir bisa jauh dari mean cluster. Harusnya ada post-clustering validation.
- **Impact:** Medium — bisa misclassify levels.
- **Fix:** Re-validate cluster after formation: remove outliers > 1.5 × cluster_dist from cluster centroid.

**🟡 M-04: `source_tf` menggunakan most-frequent TF, bukan highest TF**
```python
tf_counts: dict[str, int] = {}
for tf in tfs:
    tf_counts[tf] = tf_counts.get(tf, 0) + 1
source_tf = max(tf_counts, key=tf_counts.get)
```
- Level dengan 5× M15 + 1× H4 touch → source_tf = "M15", meskipun level ini seharusnya dianggap sebagai H4-confirmed.
- **Impact:** Bisa menyesatkan AI saat mempertimbangkan kekuatan level.
- **Fix:** Ganti ke highest TF yang punya touch di cluster: `source_tf = max(tfs, key=lambda tf: TF_WEIGHT.get(tf, 0))`.

**🔵 L-04: `min_touches` default=1 bisa produce noisy levels**
- Setiap single swing point menjadi level sendiri jika tidak ada yang dekat.
- `is_major` memerlukan `SNR_MIN_TOUCHES`, tapi minor levels bisa punya touches=1.
- **Impact:** AI menerima banyak level minor yang noise.
- **Fix:** Consider default `min_touches=2` atau filter output di caller.

---

### 4. `tools/price_action.py` (~130 lines)

**Deskripsi:** Deteksi Pin Bar dan Engulfing sebagai confirmation filter di zona kunci.

#### Arsitektur
- Input: OHLCV + optional zone levels + ATR
- Logic: Scan candles → detect pattern → optional zone proximity filter
- Output: `{pin_bars: [...]}` / `{engulfing_patterns: [...]}`
- **Status Produksi:** ✅ AKTIF

#### Temuan

**🔵 L-05: Doji candle (body ≈ 0) di-skip sebagai pin bar**
```python
if body < 1e-10 or rng < 1e-10:
    continue
```
- Hammer doji dengan body sangat kecil tapi wick panjang adalah pattern valid.
- `wick_ratio = lower_wick / body` → division by near-zero jika body sangat kecil tapi > 1e-10.
- **Impact:** Low — most legitimate pin bars have measurable body.
- **Fix:** Use `max(body, 1e-6)` for ratio calculation, atau ganti ke wick-to-range ratio.

**🔵 L-06: Engulfing tidak check minimum body significance**
- Small-bodied engulfing yang barely-engulfs previous candle bisa lolos.
- **Impact:** Low — scorer memberikan bobot hanya 2 untuk `pa_confirmed`. Weak engulfing tetap mendapat poin penuh.
- **Fix:** Add `min_body_ratio` parameter: `curr_body_abs / rng > 0.3`.

**✅ GOOD: Zone proximity filter (FIX F2-14)**
- `_near_any_zone()` menggunakan `proximity_mult × ATR` — well-implemented.
- Baik pin bar maupun engulfing menggunakan filter yang sama.

---

### 5. `tools/trendline.py` (~240 lines)

**Deskripsi:** Deteksi trendline sebagai RAY yang divalidasi dari anchor hingga candle terakhir.

#### Arsitektur
- Input: Swing highs/lows + OHLCV + pair + ATR
- Logic: O(n²) swing pairs → slope filter → full ray validation → touch counting → dedup
- Output: `{uptrend_lines: [...], downtrend_lines: [...]}`
- RAY validation: Setiap candle dari anchor A sampai bar terakhir harus respect floor/ceiling
- **Status Produksi:** ✅ AKTIF

#### Temuan

**🟡 M-05: `is_touch_valid()` standalone menggunakan STATIC tolerance, bukan ATR-adaptive**
```python
def is_touch_valid(price: float, trendline_value: float, pair: str) -> bool:
    tolerance = TRENDLINE_TOLERANCE.get(pair, 0.0010)  # STATIC only!
    return abs(price - trendline_value) <= tolerance
```
- Main `detect_trendlines()` menggunakan ATR-adaptive tolerance (FIX F2-10: `0.15 * atr`, bounded 50%-300% of static).
- `is_touch_valid()` mengabaikan ATR sepenuhnya.
- **Impact:** Saat ini DEAD CODE di production (lihat 💀 DEAD-02), tapi jika dipanggil di masa depan, akan inconsistent.
- **Fix:** Tambahkan parameter `atr_value` ke `is_touch_valid()` atau deprecate function.

**🔵 L-07: O(n² × m) complexity**
- n = jumlah swings (~10-30), m = jumlah candles (~200-500).
- Worst case: 30² × 500 = 450,000 operations.
- **Impact:** Low — masih cepat untuk ukuran data production.

**💀 DEAD-02: `is_touch_valid()` hanya digunakan di test**
```
Production usage: 0
Test usage: test_trendline.py (lines 205-218)
```
- Tidak di-import oleh `context_builder.py`, `tool_registry.py`, atau file production lain.
- **Fix:** Mark as `@deprecated` atau pindahkan ke test helper.

**✅ GOOD: Ray validation sangat thorough**
- Full candle-by-candle validation dari anchor sampai bar terakhir.
- ATR-adaptive tolerance dengan bounding (50%-300% of static).
- Deduplication berdasarkan touch overlap.

---

### 6. `tools/scorer.py` (~100 lines)

**Deskripsi:** Scoring engine yang menghitung skor setup berdasarkan weighted boolean flags.

#### Arsitektur
- Input: 11 boolean flags (7 positive, 4 penalty)
- Logic: Sum weights dari active flags → clamp [0, MAX_POSSIBLE_SCORE] → tradeable >= 5
- Output: `{score, breakdown, tradeable, max_possible}`
- **Status Produksi:** ✅ AKTIF — called di `orchestrator.py:363`

#### Temuan

**🟡 M-06: Boolean-only flags tanpa gradient scoring**
```python
# Setiap flag hanya on/off:
if active:
    breakdown[name] = weight  # Full weight
else:
    breakdown[name] = 0  # Zero — no partial credit
```
- **Masalah:** Cliff effect. Setup yang "almost near" major S/R (21 pips away saat threshold 20 pips) mendapat 0 poin, sementara yang 19 pips away mendapat 2 poin penuh.
- **Impact:** Medium — bisa menyebabkan inconsistent trade signals pada edge cases.
- **Fix:** Tambahkan gradient scoring untuk flags tertentu (contoh: `near_major_snr` bisa 0/1/2 berdasarkan proximity).

**🔵 L-08: `tradeable` threshold 5 hardcoded di function body**
```python
"tradeable": score >= 5,
```
- Tidak ada constant atau config reference untuk nilai 5.
- **Impact:** Low — masterplan menyebutkan minimum 5, tapi kalau mau tuning perlu edit source code.
- **Fix:** Pindahkan ke `VALIDATION_RULES["min_score"]` atau parameter function.

**✅ GOOD: Score clamping dan config-driven weights**
- Floor=0, cap=MAX_POSSIBLE_SCORE (14) — sensible.
- Weights dari `SCORING_WEIGHTS` config — easy to tune.

---

### 7. `tools/validator.py` (~80 lines)

**Deskripsi:** Validator hard rules untuk trading plan sebelum execution.

#### Arsitektur
- Input: setup dict `{entry, sl, tp, direction}` + ATR + htf_bias + zone_freshness
- Logic: Check R:R ≥ 1.5, SL ATR bounds [0.5, 2.5], counter-trend, zone freshness
- Output: `{passed, violations, warnings, risk_reward, sl_atr_distance}`
- **Status Produksi:** ✅ REGISTERED di tool_registry (untuk AI function calling), tidak dipanggil langsung secara programatic.

#### Temuan

**🟠 H-01: Config `must_not_counter_htf: True` tapi validator hanya warn, bukan block**
```python
# config/strategy_rules.py:
VALIDATION_RULES = {
    "must_not_counter_htf": True,  # ← Deklarasi: HARUS TIDAK counter trend
    ...
}

# tools/validator.py:
# FIX F2-13: demoted to warning (scorer already penalises -3)
if direction == "buy" and htf_bias == "bearish":
    warnings.append("Counter-trend: buying against bearish H4 bias")
    # ← WARNING saja, bukan violation!
```
- **Masalah:** Config menyatakan `must_not_counter_htf: True`, tapi enforcement-nya hanya warning. Ini mismatch antara deklarasi dan implementasi.
- FIX F2-13 mendocument bahwa ini intentional (scorer sudah penalize -3), tapi config tidak di-update.
- **Impact:** AI bisa open trade counter-trend dan validator mengatakan "passed: True".
- **Fix:** Salah satu:
  1. Update config: `must_not_counter_htf: False` (match implementation), atau
  2. Enforce sebagai violation jika config True, warning jika False.

**💀 DEAD-03: `MIN_RR` dan `SL_ATR_MULTIPLIER` imported tapi tidak digunakan**
```python
from config.settings import MIN_RR, SL_ATR_MULTIPLIER  # ← UNUSED!
from config.strategy_rules import VALIDATION_RULES

def validate_trading_plan(
    ...
    min_rr: float = VALIDATION_RULES["min_rr"],  # ← Uses VALIDATION_RULES, not MIN_RR
    max_sl_atr_mult: float = VALIDATION_RULES["sl_max_atr_mult"],  # ← not SL_ATR_MULTIPLIER
```
- `MIN_RR` dan `SL_ATR_MULTIPLIER` dari config.settings tidak pernah direferensi di function body.
- **Impact:** Low — hanya unused import. Bisa confusing bagi developer.
- **Fix:** Hapus `from config.settings import MIN_RR, SL_ATR_MULTIPLIER`.

**🔵 L-09: Validator tidak check `VALIDATION_RULES["zone_must_be_fresh"]` config**
```python
# Config menyatakan:
"zone_must_be_fresh": True,

# Validator always checks zone freshness, regardless of config:
if zone_freshness == "mitigated":
    violations.append("Zone has been mitigated — do not trade")
```
- Meskipun behaviornya benar (always check), mismatch bahwa config flag tidak dibaca.
- **Impact:** Low — behavior sudah benar.

---

## Cross-Cutting Issues

---

### 🟠 H-02: STRATEGY_MODES dan ANTI_RUNGKAD_CHECKS hanya informasi prompt, bukan enforcement

```python
# config/strategy_rules.py mendefinisikan:
STRATEGY_MODES = {
    "index_correlation": {
        "requires": ["dxy_gate_pass", "zone_detected"],
        "sweep_required": True,
        "choch_required": True,
    },
    "sniper_confluence": { ... },
    "scalping_channel": { ... },
}

ANTI_RUNGKAD_CHECKS = [
    {"id": "liquidity_sweep", "mandatory_for": ["index_correlation", "sniper_confluence"]},
    {"id": "choch_confirmation", "mandatory_for": ["index_correlation", "sniper_confluence"]},
    {"id": "crash_cancel", "mandatory_for": ["index_correlation", "sniper_confluence", "scalping_channel"]},
]
```

**Usage di production:**
- `system_prompt.py` → ditulis ke prompt AI sebagai instruksi
- **TIDAK ADA** enforcement programmatic

**Masalah:**
- AI diinstruksikan "sweep_required: True" untuk index_correlation, tapi TIDAK ADA kode yang memverifikasi AI benar-benar melakukannya.
- `validator.py` tidak check apakah sweep detected, CHOCH confirmed, atau crash cancel.
- Satu-satunya enforcement riil hanyalah `scorer.py` yang memberikan bobot (sweep: +3, pa_confirmed: +2).
- **Impact:** AI bisa mengabaikan aturan anti-rungkad yang mandatory, dan validator tetap "passed: True".
- **Fix:** Tambahkan strategy-mode-aware validation di `validator.py`:
  ```python
  if strategy_mode in ["index_correlation", "sniper_confluence"]:
      if not setup.get("sweep_confirmed"):
          violations.append("Sweep required for this strategy mode")
      if not setup.get("choch_confirmed"):
          violations.append("CHOCH confirmation required")
  ```

---

### 🔄 CONSISTENCY-01: Tolerance inconsistency di trendline

| Context | Tolerance Method |
|---------|-----------------|
| `detect_trendlines()` | ATR-adaptive: `min(0.15*ATR, static*3)` bounded `max(_, static*0.5)` |
| `_ray_is_valid()` | Uses tolerance from `detect_trendlines()` ✅ |
| `is_touch_valid()` | STATIC only: `TRENDLINE_TOLERANCE.get(pair)` ❌ |

- **Fix:** Hapus `is_touch_valid()` (dead code) atau update dengan ATR support.

### 🔄 CONSISTENCY-02: Dual config sources untuk validation rules

| Parameter | config.settings | config.strategy_rules | Validator Uses |
|-----------|----------------|-----------------------|----------------|
| min_rr | `MIN_RR` | `VALIDATION_RULES["min_rr"]` = 1.5 | strategy_rules ✅ |
| sl_atr_mult | `SL_ATR_MULTIPLIER` | `VALIDATION_RULES["sl_max_atr_mult"]` = 2.5 | strategy_rules ✅ |

- Kedua config source define nilai yang sama, tapi validator hanya gunakan strategy_rules.
- settings.py values jadi orphaned — bisa drift tanpa terdeteksi.
- **Fix:** Hapus duplikasi di settings.py atau redirect ke satu single source.

### 🔄 CONSISTENCY-03: DXY gate disabled tapi masih di masterplan requirements

- `masterplan.md` mendefinisikan DXY sebagai Strategy Mode 1 (index_correlation).
- `STRATEGY_MODES["index_correlation"]` requires `dxy_gate_pass`.
- Tapi `dxy_relevance_score` disabled di tool_registry.
- **Impact:** Strategy Mode 1 tidak bisa dijalankan karena tool-nya disabled.
- **Fix:** Lengkapi implementasi dxy_gate.py (zone + volatility) lalu enable, atau hapus dari STRATEGY_MODES.

### 🔄 CONSISTENCY-04: `source_tf` semantik berbeda dari `TF_WEIGHT` usage

- `snr.py`: `source_tf` = most frequent TF in cluster (quantitative).
- `TF_WEIGHT`: assigns weight by TF hierarchy (H4=4, H1=3, M30=2, M15=1).
- Scoring uses `max_tf_weight` (highest TF in cluster), tapi label uses most-frequent.
- **Impact:** AI melihat `source_tf: M15` tapi level sebenarnya H4-confirmed.

---

## Statistics

| Metric | Value |
|--------|-------|
| Total lines reviewed | ~810 |
| Functions analyzed | 15 |
| CRITICAL issues | 0 |
| HIGH issues | 2 |
| MEDIUM issues | 6 |
| LOW issues | 9 |
| Dead code items | 3 |
| Consistency issues | 4 |
| **Total issues** | **24** |

---

## Rekomendasi Prioritas

### Harus Fix (HIGH)
1. **H-02:** Tambahkan programmatic enforcement untuk STRATEGY_MODES / ANTI_RUNGKAD_CHECKS di validator, bukan hanya prompt instruction.
2. **H-01:** Sinkronkan `must_not_counter_htf` config dengan validator behavior.

### Sebaiknya Fix (MEDIUM)
3. **M-06:** Pertimbangkan gradient scoring untuk cliff-effect flags (terutama `near_major_snr` dan `fresh_zone`).
4. **M-04:** Ganti `source_tf` ke highest hierarchical TF, bukan most-frequent.
5. **M-02:** Lengkapi dxy_gate.py (zone + volatility) sebelum enable, atau update docs.
6. **M-03:** Tambahkan post-clustering validation pada SNR.
7. **M-05:** Hapus atau deprecate `is_touch_valid()`.

### Cleanup (LOW)
8. **DEAD-03:** Hapus unused imports `MIN_RR`, `SL_ATR_MULTIPLIER` di validator.py.
9. **DEAD-02:** Remove `is_touch_valid()` dari trendline.py (pindah ke test helper jika perlu).
10. **L-08:** Pindahkan tradeable threshold `5` ke config.

---

## Catatan Positif

1. **RAY trendline validation** — implementasi candle-by-candle yang sangat thorough dan benar secara konseptual.
2. **ATR-adaptive tolerance di trendline** — bounded approach (50%-300% static) mencegah over/under-fitting.
3. **Zone proximity filter (FIX F2-14)** — price_action patterns hanya dihitung dekat zona, mengurangi noise.
4. **Config-driven scoring weights** — mudah di-tune tanpa ubah code.
5. **Scorer floor/cap** — prevents negative scores dan overflow.
6. **Validator violation/warning separation** — clear distinction antara hard fail dan advisory.
7. **Pearson correlation edge case handling** — zero-variance guard di dxy_gate.
