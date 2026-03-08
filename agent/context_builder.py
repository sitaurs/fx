"""
agent/context_builder.py — Collect all tool outputs & format as Gemini context.

Runs every Python tool LOCALLY on real market data, then formats the results
into a structured text context that Gemini can reason about.

This replaces the old approach of relying on Gemini's auto-function-calling
(which is stripped in structured-output mode) with a deterministic data pipeline.

Flow per timeframe:
    fetch_ohlcv → ATR → EMA → RSI → Swings → BOS/ChoCH → SNR →
    SnD → OB → Trendlines → EQH/EQL → Sweep → Pin Bar → Engulfing →
    ChoCH Micro

Reference: masterplan.md Phase 4 (Core Orchestration)
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import SWING_LOOKBACK

# --- Tools ---
from tools.indicators import compute_atr, compute_ema, compute_rsi
from tools.swing import detect_swing_points
from tools.structure import detect_bos_choch
from tools.supply_demand import detect_snd_zones
from tools.snr import detect_snr_levels
from tools.orderblock import detect_orderblocks
from tools.trendline import detect_trendlines
from tools.liquidity import detect_eqh_eql, detect_sweep
from tools.price_action import detect_pin_bar, detect_engulfing
from tools.choch_filter import detect_choch_micro

# --- Data ---
from data.fetcher import fetch_ohlcv

logger = logging.getLogger(__name__)

CANDLE_COUNT = 150
MIN_CANDLES = 30  # FIX F1-04: Minimum for reliable tool output


# ===================================================================
# Single timeframe analysis
# ===================================================================

def analyze_timeframe(
    pair: str,
    timeframe: str,
    candle_count: int = CANDLE_COUNT,
) -> dict[str, Any]:
    """Run every tool on *pair* / *timeframe* and return a dict of results.

    The dict is keyed by tool name and includes raw outputs.
    This is A-Z deterministic — the same data → same results.
    """
    logger.info("[%s %s] Fetching %d candles …", pair, timeframe, candle_count)
    result = fetch_ohlcv(pair, timeframe, candle_count)
    candles = result["candles"]
    if not candles:
        raise ValueError(f"No candles returned for {pair} {timeframe}")

    # FIX F1-04/F1-08: Minimum candle count guard
    if len(candles) < MIN_CANDLES:
        raise ValueError(
            f"Insufficient data for {pair} {timeframe}: "
            f"got {len(candles)} candles, need ≥{MIN_CANDLES}"
        )

    lookback = SWING_LOOKBACK.get(timeframe, 4)

    # ── 1. Core indicators ─────────────────────────────────────────
    atr_result = compute_atr(candles, period=14)
    atr = atr_result["current"]

    ema50 = compute_ema(candles, period=50)
    rsi14 = compute_rsi(candles, period=14)

    # ── 2. Swing points ────────────────────────────────────────────
    swings = detect_swing_points(candles, lookback=lookback, min_distance_atr=0.3)
    swing_highs = swings.get("swing_highs", [])
    swing_lows = swings.get("swing_lows", [])
    for s in swing_highs + swing_lows:
        s.setdefault("timeframe", timeframe)

    # ── 3. Market structure (BOS / CHoCH) ──────────────────────────
    structure = detect_bos_choch(candles, swing_highs, swing_lows, atr)

    # ── 4. Support & Resistance ────────────────────────────────────
    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s["index"])
    snr = detect_snr_levels(swings=all_swings, atr_value=atr, min_touches=1)

    # ── 5. Supply & Demand zones ───────────────────────────────────
    snd = detect_snd_zones(ohlcv=candles, atr_value=atr)

    # ── 6. Order blocks ────────────────────────────────────────────
    ob = detect_orderblocks(ohlcv=candles, atr_value=atr)

    # ── 7. Trendlines (RAY) ───────────────────────────────────────
    tl = detect_trendlines(
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        ohlcv=candles,
        pair=pair,
        min_touches=2,
        atr_value=atr,    # FIX F2-10: pass real ATR for adaptive tolerance
    )

    # ── 8. Liquidity pools & sweeps ─────────────────────────────────
    eqh_eql = detect_eqh_eql(swing_highs, swing_lows, atr)
    all_pools = eqh_eql.get("eqh_pools", []) + eqh_eql.get("eql_pools", [])
    sweep = (
        detect_sweep(candles, all_pools, atr) if all_pools
        else {"sweep_events": []}
    )

    # ── 9. Price action patterns ───────────────────────────────────
    pin_bars = detect_pin_bar(candles)
    engulfing = detect_engulfing(candles)

    # ── 10. ChoCH micro filter (both directions) ──────────────────
    choch_bull = detect_choch_micro(candles, direction="bullish", atr=atr)
    choch_bear = detect_choch_micro(candles, direction="bearish", atr=atr)

    logger.info(
        "[%s %s] Done — ATR=%.5f  trend=%s  swings=%d  zones=%d",
        pair, timeframe, atr,
        structure.get("trend", "?"),
        len(swing_highs) + len(swing_lows),
        len(snd.get("supply_zones", [])) + len(snd.get("demand_zones", [])),
    )

    return {
        "timeframe": timeframe,
        "candle_count": len(candles),
        "last_close": candles[-1]["close"],
        "last_time": candles[-1].get("time", ""),
        # Indicators
        "atr": atr_result,
        "ema50": {"current": ema50["current"], "period": ema50["period"]},
        "rsi14": {"current": rsi14["current"], "period": rsi14["period"]},
        # Swings
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        # Structure
        "structure": structure,
        # Levels & Zones
        "snr_levels": snr.get("levels", []),
        "supply_zones": snd.get("supply_zones", []),
        "demand_zones": snd.get("demand_zones", []),
        "bullish_obs": ob.get("bullish_obs", []),
        "bearish_obs": ob.get("bearish_obs", []),
        # Trendlines
        "uptrend_lines": tl.get("uptrend_lines", []),
        "downtrend_lines": tl.get("downtrend_lines", []),
        # Liquidity
        "eqh_pools": eqh_eql.get("eqh_pools", []),
        "eql_pools": eqh_eql.get("eql_pools", []),
        "sweep_events": sweep.get("sweep_events", []),
        # Price action
        "pin_bars": pin_bars.get("pin_bars", []),
        "engulfing_patterns": engulfing.get("engulfing_patterns", []),
        # ChoCH micro
        "choch_micro_bullish": choch_bull,
        "choch_micro_bearish": choch_bear,
    }


# ===================================================================
# Multi-timeframe collection
# ===================================================================

def collect_multi_tf(
    pair: str,
    timeframes: list[str],
    candle_count: int = CANDLE_COUNT,
) -> dict[str, dict]:
    """Run ``analyze_timeframe`` for each TF.  Errors are captured, not raised."""
    analyses: dict[str, dict] = {}
    for tf in timeframes:
        try:
            analyses[tf] = analyze_timeframe(pair, tf, candle_count)
        except Exception as exc:
            logger.error("[%s %s] FAILED: %s", pair, tf, exc)
            analyses[tf] = {"error": str(exc)}
    return analyses


# ===================================================================
# Context formatter — text block for Gemini prompt
# ===================================================================

def format_context(pair: str, analyses: dict[str, dict]) -> str:
    """Convert multi-TF analyses into a structured text context for Gemini.

    This is the *only* data Gemini should base its analysis on.
    Every price, zone, and level in the context comes from a verified Python tool.
    """
    if not analyses:
        logger.warning("[%s] format_context called with empty analyses dict", pair)
        return f"=== LIVE MARKET DATA: {pair} ===\nNo data available.\n=== END LIVE MARKET DATA ==="

    # FIX L-20: Warn when all timeframes failed
    all_errors = all("error" in data for data in analyses.values())
    if all_errors:
        logger.warning("[%s] All %d timeframes returned errors", pair, len(analyses))
    lines: list[str] = []
    lines.append(f"=== LIVE MARKET DATA: {pair} ===")
    lines.append(f"Timeframes analysed: {', '.join(analyses.keys())}")
    lines.append("")

    for tf, data in analyses.items():
        if "error" in data:
            lines.append(f"--- {tf}: ERROR — {data['error']} ---\n")
            continue

        lines.append(f"{'═' * 60}")
        lines.append(f"  {tf}  |  {data['candle_count']} candles  |"
                      f"  Last: {data['last_close']}  @  {data['last_time']}")
        lines.append(f"{'═' * 60}")

        # Indicators
        lines.append(f"ATR(14)  = {data['atr']['current']:.5f}")
        lines.append(f"EMA(50)  = {data['ema50']['current']:.5f}")
        # FIX F1-07: RSI NaN guard
        rsi_val = data['rsi14']['current']
        if rsi_val is not None and not (isinstance(rsi_val, float) and rsi_val != rsi_val):
            lines.append(f"RSI(14)  = {rsi_val:.2f}")
        else:
            lines.append("RSI(14)  = N/A (insufficient data)")

        # Structure
        struct = data["structure"]
        trend = struct.get("trend", "unknown")
        lines.append(f"\n[STRUCTURE]  trend = {trend}")
        hh = struct.get("last_hh")
        hl = struct.get("last_hl")
        lh = struct.get("last_lh")
        ll = struct.get("last_ll")
        if hh is not None:
            lines.append(f"  HH={hh}  HL={hl}  LH={lh}  LL={ll}")
        events = struct.get("events", [])
        recent_ev = events[-5:] if events else []
        for ev in recent_ev:
            lines.append(
                f"  {ev['event_type'].upper()} {ev['direction']}"
                f" @ bar {ev['break_index']}  price={ev['break_price']}"
            )

        # Swings (summary counts)
        sh = data["swing_highs"]
        sl_ = data["swing_lows"]
        lines.append(f"\n[SWINGS]  {len(sh)} highs + {len(sl_)} lows")

        # SNR
        snr = data["snr_levels"]
        major = [s for s in snr if s.get("is_major")]
        lines.append(f"\n[SNR]  {len(snr)} levels ({len(major)} major)")
        if len(snr) > 6:
            lines.append(f"  [showing top 6 of {len(snr)}]")
        for s in snr[:6]:
            tag = " *MAJOR*" if s.get("is_major") else ""
            lines.append(
                f"  price={s['price']:.5f}  touches={s['touches']}"
                f"  score={s['score']:.2f}{tag}"
            )

        # Supply & Demand
        supply = data["supply_zones"]
        demand = data["demand_zones"]
        lines.append(f"\n[SUPPLY ZONES]  {len(supply)}")
        for z in supply:
            fresh = "FRESH" if z.get("is_fresh") else "mitigated"
            lines.append(
                f"  {z['high']:.5f} – {z['low']:.5f}"
                f"  score={z.get('score', 0):.2f}  {fresh}"
            )
        lines.append(f"[DEMAND ZONES]  {len(demand)}")
        for z in demand:
            fresh = "FRESH" if z.get("is_fresh") else "mitigated"
            lines.append(
                f"  {z['high']:.5f} – {z['low']:.5f}"
                f"  score={z.get('score', 0):.2f}  {fresh}"
            )

        # Order Blocks
        bull_ob = data["bullish_obs"]
        bear_ob = data["bearish_obs"]
        lines.append(f"\n[ORDER BLOCKS]  {len(bull_ob)} bullish, {len(bear_ob)} bearish")
        if len(bull_ob) > 3:
            lines.append(f"  [showing top 3 of {len(bull_ob)} bullish OBs]")
        for ob in bull_ob[:3]:
            lines.append(
                f"  BULL OB  {ob['high']:.5f} – {ob['low']:.5f}"
                f"  score={ob.get('score', 0):.2f}"
            )
        if len(bear_ob) > 3:
            lines.append(f"  [showing top 3 of {len(bear_ob)} bearish OBs]")
        for ob in bear_ob[:3]:
            lines.append(
                f"  BEAR OB  {ob['high']:.5f} – {ob['low']:.5f}"
                f"  score={ob.get('score', 0):.2f}"
            )

        # Trendlines
        up_tl = data["uptrend_lines"]
        dn_tl = data["downtrend_lines"]
        lines.append(f"\n[TRENDLINES]  {len(up_tl)} up, {len(dn_tl)} down (RAY validated)")
        for tl in up_tl:
            a1, a2 = tl["anchor_1"], tl["anchor_2"]
            lines.append(
                f"  UP RAY  {a1['price']:.5f} → {a2['price']:.5f}"
                f"  touches={tl['touches']}  score={tl['score']:.2f}"
            )
        for tl in dn_tl:
            a1, a2 = tl["anchor_1"], tl["anchor_2"]
            lines.append(
                f"  DN RAY  {a1['price']:.5f} → {a2['price']:.5f}"
                f"  touches={tl['touches']}  score={tl['score']:.2f}"
            )

        # Liquidity
        eqh = data["eqh_pools"]
        eql = data["eql_pools"]
        sweeps = data["sweep_events"]
        lines.append(f"\n[LIQUIDITY]  {len(eqh)} EQH, {len(eql)} EQL, {len(sweeps)} sweeps")
        for p in eqh:
            lines.append(
                f"  EQH  price={p['price']:.5f}  count={p['swing_count']}"
                f"  swept={p.get('is_swept', False)}"
            )
        for p in eql:
            lines.append(
                f"  EQL  price={p['price']:.5f}  count={p['swing_count']}"
                f"  swept={p.get('is_swept', False)}"
            )
        for sv in sweeps:
            lines.append(
                f"  SWEEP  bar={sv.get('bar_index')}"
                f"  type={sv.get('sweep_type')}"
                f"  pool_price={sv.get('pool_price', 0):.5f}"
            )

        # Price action
        pins = data["pin_bars"]
        eng = data["engulfing_patterns"]
        lines.append(f"\n[PRICE ACTION]  {len(pins)} pin bars, {len(eng)} engulfing")
        for p in pins[-3:]:
            lines.append(
                f"  PIN  bar={p['index']}  {p['type']}"
                f"  wick_ratio={p.get('wick_ratio', 0):.1f}"
            )
        for e in eng[-3:]:
            lines.append(
                f"  ENG  bar={e['index']}  {e['type']}"
                f"  strength={e.get('strength', 0):.2f}"
            )

        # ChoCH micro
        cm_bull = data["choch_micro_bullish"]
        cm_bear = data["choch_micro_bearish"]
        lines.append(
            f"\n[CHOCH MICRO]  bullish={cm_bull.get('confirmed', False)}"
            f"  bearish={cm_bear.get('confirmed', False)}"
        )

        lines.append("")  # blank line between TFs

    lines.append("=== END LIVE MARKET DATA ===")
    return "\n".join(lines)


# ===================================================================
# Async wrappers (FIX F1-02) — avoid blocking event loop
# ===================================================================
import asyncio


async def analyze_timeframe_async(
    pair: str,
    timeframe: str,
    candle_count: int = CANDLE_COUNT,
) -> dict[str, Any]:
    """Async version — runs sync tools in executor to avoid blocking."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, analyze_timeframe, pair, timeframe, candle_count
    )


async def collect_multi_tf_async(
    pair: str,
    timeframes: list[str],
    candle_count: int = CANDLE_COUNT,
) -> dict[str, dict]:
    """Async multi-TF collection — all TFs run concurrently via asyncio.gather.

    FIX C-03: Previously awaited coroutines sequentially in a for-loop.
    Now uses asyncio.gather() for true parallel execution (2-3× faster).
    """
    tasks = [analyze_timeframe_async(pair, tf, candle_count) for tf in timeframes]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results: dict[str, dict] = {}
    for tf, result in zip(timeframes, gathered):
        if isinstance(result, Exception):
            logger.error("[%s %s] FAILED: %s", pair, tf, result)
            results[tf] = {"error": str(result)}
        else:
            results[tf] = result
    return results
