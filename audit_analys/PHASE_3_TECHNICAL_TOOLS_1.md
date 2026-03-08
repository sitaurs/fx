# Phase 3 Audit: Technical Tools 1 (Core Analysis Tools)

**Scope:** `tools/structure.py` (191 lines), `tools/supply_demand.py` (259 lines), `tools/indicators.py` (154 lines), `tools/swing.py` (126 lines), `tools/orderblock.py` (85 lines), `tools/liquidity.py` (187 lines)

**Total Lines Reviewed:** 1,002

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 0 |
| 🟠 HIGH | 3 |
| 🟡 MEDIUM | 5 |
| 🔵 LOW | 7 |
| ⚪ Dead Code | 0 |
| 📝 Consistency | 3 |

---

## 🟠 HIGH Issues

### H-01: Supply zone freshness check is too aggressive (false mitigation)
**File:** `tools/supply_demand.py` lines 233-259 (`_update_freshness`)
```python
# Supply zone mitigated if price rises back INTO the zone
if c["close"] >= z_low and c["high"] >= z_low:
    zone["is_fresh"] = False
    break
```
**Problem:** For a supply zone, any candle whose close >= zone low is considered "mitigated". But supply zones sit ABOVE price. When price rises toward a supply zone and touches z_low, it's a *retest* not mitigation. Mitigation should be when price breaks THROUGH the zone (close > z_high).
- **Current logic:** Supply zone 1.0500-1.0520 → close at 1.0501 → marked mitigated ❌
- **Correct logic:** Supply mitigated when close > 1.0520 (cleared entire zone)

Same issue for demand zones:
```python
# Demand zone mitigated if price drops back INTO the zone  
if c["close"] <= z_high and c["low"] <= z_high:
    zone["is_fresh"] = False
```
- **Current:** Demand 1.0400-1.0420 → close at 1.0419 → marked mitigated ❌
- **Correct:** Demand mitigated when close < 1.0400 (broke through entire zone)

**Impact:** Many valid zones are incorrectly marked as `is_fresh=False`, reducing the number of trade setups. The scorer penalizes mitigated zones (`zone_mitigated` flag), so this directly lowers confluence scores.

**Fix:**
```python
# Supply mitigated when price closes ABOVE zone top (through the zone)
if zone["zone_type"] == "supply":
    if c["close"] > z_high:
        zone["is_fresh"] = False
        break
# Demand mitigated when price closes BELOW zone bottom
else:
    if c["close"] < z_low:
        zone["is_fresh"] = False
        break
```

### H-02: Order block zone boundaries are inconsistent
**File:** `tools/orderblock.py` lines 48-70
**Bullish OB:**
```python
"high": prev["high"],    # FIX F2-08: full candle
"low": prev["low"],
```
**Bearish OB:**
```python
"high": prev["high"],
"low": prev["open"],     # ← uses open, not low
```
**Problem:** Bullish OB uses full candle range [low, high], but bearish OB uses [open, high]. This is asymmetric. The ICT/SMC convention for bearish OB is the last bullish candle's body: [open, close] (since body_prev > 0 means close > open). Using `prev["open"]` as low makes sense only if `prev["open"]` < `prev["close"]`, which is guaranteed since we checked `body_prev > 0`.

However, the asymmetry means bullish OBs have wider zones (including wicks) while bearish OBs have tighter zones. This creates a directional bias in zone detection.

**Fix:** Either use full candle for both (current bullish OB style) or use body for both:
```python
# Consistent: body-based for both directions
# Bullish OB (bearish candle): body = [close, open] since close < open
bullish_ob: high = prev["open"], low = prev["close"]
# Bearish OB (bullish candle): body = [open, close] since close > open
bearish_ob: high = prev["close"], low = prev["open"]
```

### H-03: Structure detection could miss CHOCH when trend is "ranging"
**File:** `tools/structure.py` lines 115-135
```python
if close > active_sh["price"] + buffer and active_sh["index"] < i:
    if current_trend == "bearish":
        events.append(_event("choch", "bullish", ...))
        current_trend = "bullish"
    else:
        events.append(_event("bos", "bullish", ...))
        current_trend = "bullish"
```
**Problem:** When `current_trend == "ranging"` (initial state), all breaks are classified as BOS. A market that was ranging and then breaks a swing low (bearish) followed by a swing high break (bullish) would produce BOS(bearish), BOS(bullish) — never a CHOCH. The first real trend change goes undetected.

**Impact:** Medium. After the first BOS, trend is set, so subsequent real CHOCHs are detected correctly. The issue is only with the transition from ranging → trending.

**Fix:** Consider the first break as establishing trend (BOS), but if the *second* break is against the first, flag it as CHOCH.

---

## 🟡 MEDIUM Issues

### M-01: `detect_bos_choch` BOS overwrites HH/LL tracking with close price
**File:** `tools/structure.py` lines 126-128
```python
last_hh = close
active_sh = {"index": i, "price": close, "type": "high"}
```
**Problem:** After a bullish BOS, the code sets `last_hh = close` and creates a synthetic swing high at the close price. But `close` is the candle close, not the actual swing high. This synthetic swing becomes the next active swing for future BOS detection, potentially making subsequent structure detection less accurate.

### M-02: Swing detection ATR is recomputed inside `detect_swing_points`
**File:** `tools/swing.py` line 93
```python
atr_result = compute_atr(ohlcv, period=14)
```
**Problem:** `context_builder.py` already computes ATR and passes it to other tools. But `detect_swing_points` recomputes ATR internally. This is redundant work (minor perf) and, more importantly, uses the same data so should match — but if `compute_atr` ever changes behavior, the two ATR values could diverge.
**Fix:** Accept an optional `atr_value` parameter to avoid recomputation.

### M-03: Liquidity pool clustering is greedy, not optimal
**File:** `tools/liquidity.py` lines 73-104 (`_find_pools`)
```python
sorted_sw = sorted(swings, key=lambda s: s["price"])
i = 0
while i < len(sorted_sw):
    cluster = [sorted_sw[i]]
    j = i + 1
    while j < len(sorted_sw):
        cluster_mean = sum(s["price"] for s in cluster) / len(cluster)
        if abs(sorted_sw[j]["price"] - cluster_mean) <= tolerance:
            cluster.append(sorted_sw[j])
```
**Problem:** Greedy clustering means the first swing anchors the cluster. If swings are at prices [100.5, 100.6, 100.7, 101.5] with tolerance=0.15, the algorithm may cluster [100.5, 100.6] and miss [100.6, 100.7] as a separate pool. The running mean shifts as swings are added, causing order-dependent results.
**Impact:** Some valid liquidity pools may be missed or combined incorrectly.

### M-04: Sweep detection doesn't mark pools as swept
**File:** `tools/liquidity.py` lines 139-177
**Problem:** `detect_sweep` returns sweep events but never sets `pool["is_swept"] = True` on the pool objects. The `context_builder.py` formats `is_swept` for each pool:
```python
f"  EQH  price={p['price']:.5f}  count={p['swing_count']}  swept={p.get('is_swept', False)}"
```
This always shows `swept=False` because the sweep detection doesn't update the original pool dict. Gemini sees pools marked as "not swept" even when sweeps were detected for them.
**Fix:** In `detect_sweep`, set `pool["is_swept"] = True` when a sweep event is found.

### M-05: `_check_displacement` doesn't check direction consistency
**File:** `tools/supply_demand.py` lines 140-195
**Problem:** The displacement check computes both `displacement_up` and `displacement_down` from the same set of candles. In theory, both could be valid (e.g., a volatile V-shaped move). The first check that passes wins. A candle that crashes down then rallies could register as demand even though the net move is ambiguous.
**Impact:** Rare in practice (both directions simultaneously exceeding min_displacement is unusual).

---

## 🔵 LOW Issues

### L-01: `compute_atr` fallback for insufficient data
**File:** `tools/indicators.py` lines 36-38
```python
if n < period:
    avg = sum(tr) / n
    atr_values[-1] = avg
```
**Problem:** When `n < period`, only the last element of `atr_values` is set. All others remain NaN. This is correct behavior but undocumented — callers using `atr_values[i]` for `i < n-1` will get NaN.

### L-02: RSI returns NaN-heavy array for short data
**File:** `tools/indicators.py` lines 118-120
**Problem:** If `n < period + 1`, returns all NaN values. The `current` field is also NaN. The `format_context` handler guards for this, but tool callers may not.

### L-03: `_score_zone` freshness score favors right-edge zones
**File:** `tools/supply_demand.py` lines 211-220
```python
freshness = 0.3 + 0.7 * (base_idx / (total_bars - 1))
```
**Problem:** Linear freshness weighting from 0.3 (oldest) to 1.0 (newest). This means a zone at bar 10 of 150 candles gets score × 0.35, while bar 140 gets score × 0.95. Old zones that are still fresh and have strong displacement are penalized. The SMC principle is "the older and untested, the stronger" — opposite of this weighting.
**Note:** This is a design choice, not a bug. But it contradicts typical SMC theory.

### L-04: Swing distance filter uses greedy approach
**File:** `tools/swing.py` lines 101-116
```python
kept: list[dict] = [swings[0]]
for s in swings[1:]:
    price_ok = abs(s["price"] - kept[-1]["price"]) >= min_dist
    time_ok = abs(s["index"] - kept[-1]["index"]) >= min_bars
    if price_ok or time_ok:
        kept.append(s)
```
**Problem:** The filter always keeps the first swing and compares each subsequent swing only to the last kept one. If the first swing is an outlier, it could cause a chain of incorrect filters.

### L-05: `detect_sweep` doesn't deduplicate events
**File:** `tools/liquidity.py` lines 148-175
**Problem:** If two pools are at similar prices (e.g., EQH at 1.0500 and 1.0505), the same candle could trigger sweep events for both pools, producing near-duplicate events. Not harmful but adds noise.

### L-06: Order block score is unbounded
**File:** `tools/orderblock.py` line 61
```python
"score": round(disp_up / atr_value, 3),
```
**Problem:** Score is `displacement / ATR`. A 3×ATR displacement gives score=3.0. No upper bound means extreme moves produce extreme scores that could skew downstream scoring. SnD zones have bounded scores due to the freshness multiplier.

### L-07: Liquidity `_find_pools` score is just swing count
**File:** `tools/liquidity.py` line 101
```python
"score": len(valid),  # simple count-based score
```
**Problem:** Pool score is just the number of swings comprising the pool. A pool with 3 swings at identical prices is scored the same as 3 swings at slightly different prices (within tolerance). Additional factors like recency, ATR-normalized tightness, and time span could improve quality.

---

## 📝 Consistency Issues

### CON-01: Zone dict structure varies across tools
| Tool | Zone dict keys |
|------|---------------|
| Supply/Demand | `zone_type`, `high`, `low`, `base_start_idx`, `base_end_idx`, `displacement_strength`, `body_ratio`, `score`, `is_fresh`, `origin_time` |
| Order Block | `zone_type`, `high`, `low`, `candle_index`, `displacement_bos`, `is_mitigated`, `score`, `origin_time` |
| Liquidity | `pool_type`, `price`, `swing_count`, `indices`, `is_swept`, `score` |

No common base schema. Downstream code (`context_builder.py`, `orchestrator._extract_score_flags`) must handle each format differently.

### CON-02: Freshness terminology inconsistent
- Supply/Demand: `is_fresh` (True = untouched)
- Order Block: `is_mitigated` (but never set to True — always False)
- Liquidity: `is_swept` (but never updated — always False)

Order blocks have no freshness/mitigation check at all. A bullish OB from 100 bars ago that price has revisited 5 times is still `is_mitigated=False`.

### CON-03: ATR handling inconsistency
- `structure.py`: Receives ATR as parameter, uses `math.isnan()` guard
- `supply_demand.py`: Receives ATR as parameter, uses `math.isnan()` guard
- `swing.py`: Recomputes ATR internally (ignores externally-computed ATR)
- `orderblock.py`: Receives ATR as parameter, uses `math.isnan()` guard
- `liquidity.py`: Receives ATR as parameter, uses `math.isnan()` guard

`swing.py` is the outlier — it should accept an optional pre-computed ATR.

---

## Architecture Observations

### Strengths
1. **Pure functions**: All 6 tools are pure/deterministic — no IO, no state, no LLM calls. Given the same input, they produce the same output. Excellent for testing and reproducibility.
2. **Configurable constants**: Key thresholds (ATR multipliers, base candle counts, displacement ratios) come from `config/settings.py`, allowing tuning without code changes.
3. **Algorithmic correctness**: The core algorithms (fractal pivots, Wilder's smoothing, base+displacement detection, pool clustering) are mathematically sound.
4. **Edge case handling**: NaN guards, minimum data checks, empty input handling are consistently applied.

### Areas for Improvement
1. **Freshness/mitigation logic** (H-01): The supply/demand freshness check is the most impactful bug — it's likely causing many valid zones to be incorrectly marked as mitigated.
2. **Cross-tool referencing**: Sweep detection should update pool `is_swept` status, and order blocks should have mitigation checks similar to SnD zones.
3. **Unified zone schema**: A common dataclass/TypedDict for zones would reduce bugs and simplify downstream processing.
4. **Performance**: Swing detection recomputes ATR unnecessarily. All tools are O(n²) worst case with n=150 candles, which is fine for current usage but could matter if candle counts increase.

---

*Audit completed: 2026-03-07*
*Files reviewed: 6 | Lines reviewed: 1,002 | Issues found: 18*
