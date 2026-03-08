"""
tools/structure.py — Market Structure detection: BOS (Break of Structure) & CHOCH (Change of Character).

Algorithm (masterplan 6.2):
    - Track HH/HL (uptrend) or LH/LL (downtrend).
    - BOS: close breaks past the last swing level + buffer × ATR → continuation.
    - CHOCH: first break AGAINST the current trend → reversal signal.

Ranging → Trend transition (H-10):
    The first break from ``ranging`` state establishes trend direction (BOS).
    Any subsequent break in the opposite direction is classified as CHOCH.
    Example: ranging → BOS(bearish) → break above SH → CHOCH(bullish).

Reference: masterplan.md §6.2
"""

from __future__ import annotations

import math

from config.settings import BOS_ATR_BUFFER
from schemas.structure import TrendState


def detect_bos_choch(
    ohlcv: list[dict],
    swing_highs: list[dict],
    swing_lows: list[dict],
    atr_value: float,
    bos_buffer_mult: float = BOS_ATR_BUFFER,
) -> dict:
    """Detect BOS and CHOCH events from OHLCV + swing points.

    Args:
        ohlcv: List of candle dicts (open, high, low, close, volume, time).
        swing_highs: Swing high points from detect_swing_points().
        swing_lows: Swing low points from detect_swing_points().
        atr_value: Current ATR value.
        bos_buffer_mult: ATR multiplier for break confirmation (default 0.05).

    Returns:
        Dict with keys:
            trend: str — "bullish" | "bearish" | "ranging" (TrendState values)
            events: list[dict] — each has event_type, direction, break_index,
                break_price, broken_swing_index, time.
            last_hh, last_hl, last_lh, last_ll: Optional[float]
    """
    n = len(ohlcv)
    if not swing_highs or not swing_lows or n == 0:
        return _result(TrendState.RANGING.value, [])

    if atr_value <= 0 or math.isnan(atr_value):
        atr_value = 1.0  # fallback

    buffer = bos_buffer_mult * atr_value

    # Merge swings into a single timeline sorted by index
    all_swings = sorted(
        [{"idx": s["index"], "price": s["price"], "type": "high"} for s in swing_highs]
        + [{"idx": s["index"], "price": s["price"], "type": "low"} for s in swing_lows],
        key=lambda s: s["idx"],
    )

    if len(all_swings) < 2:
        return _result(TrendState.RANGING.value, [])

    # Track structure
    events: list[dict] = []
    # H-10 / CON-13: Use TrendState enum for trend tracking.
    # Initial state is RANGING.  The FIRST break from ranging establishes
    # trend direction (classified as BOS).  Any subsequent opposing break is
    # correctly classified as CHOCH because current_trend is now set.
    current_trend: str = TrendState.RANGING.value

    # We need to track the last significant swing high and swing low
    last_sh: dict | None = None  # last swing high
    last_sl: dict | None = None  # last swing low
    prev_sh: dict | None = None  # swing high before last_sh
    prev_sl: dict | None = None  # swing low before last_sl

    last_hh: float | None = None
    last_hl: float | None = None
    last_lh: float | None = None
    last_ll: float | None = None

    for sw in all_swings:
        if sw["type"] == "high":
            prev_sh = last_sh
            last_sh = sw
        else:
            prev_sl = last_sl
            last_sl = sw

    # Now scan OHLCV bars and check for breaks of swing levels
    # We process swings in order and check if price breaks them
    sh_list = sorted(swing_highs, key=lambda s: s["index"])
    sl_list = sorted(swing_lows, key=lambda s: s["index"])

    # Track the most recent unbroken swing high and swing low
    active_sh: dict | None = None
    active_sl: dict | None = None
    sh_ptr = 0
    sl_ptr = 0

    for i in range(n):
        candle = ohlcv[i]
        close = candle["close"]

        # Update active swings: any swing whose index < i becomes "active"
        while sh_ptr < len(sh_list) and sh_list[sh_ptr]["index"] < i:
            new_sh = sh_list[sh_ptr]
            if active_sh is not None and new_sh["price"] > active_sh["price"]:
                last_hh = new_sh["price"]
            elif active_sh is not None and new_sh["price"] < active_sh["price"]:
                last_lh = new_sh["price"]
            active_sh = new_sh
            sh_ptr += 1

        while sl_ptr < len(sl_list) and sl_list[sl_ptr]["index"] < i:
            new_sl = sl_list[sl_ptr]
            if active_sl is not None and new_sl["price"] > active_sl["price"]:
                last_hl = new_sl["price"]
            elif active_sl is not None and new_sl["price"] < active_sl["price"]:
                last_ll = new_sl["price"]
            active_sl = new_sl
            sl_ptr += 1

        if active_sh is None or active_sl is None:
            continue

        # --- Check bullish break: close > active_sh.price + buffer ---
        if close > active_sh["price"] + buffer and active_sh["index"] < i:
            if current_trend == TrendState.BEARISH.value:
                # First bullish break against bearish trend -> CHOCH
                events.append(_event("choch", "bullish", i, active_sh["price"], active_sh["index"], candle["time"]))
                current_trend = TrendState.BULLISH.value
            else:
                # Continuation or establishing from ranging (H-10)
                events.append(_event("bos", "bullish", i, active_sh["price"], active_sh["index"], candle["time"]))
                current_trend = TrendState.BULLISH.value
            # FIX F2-03: Invalidate broken swing to prevent re-triggering
            # FIX F2-04: Update HH tracking when bullish BOS fires
            last_hh = close
            active_sh = {"index": i, "price": close, "type": "high"}

        # --- Check bearish break: close < active_sl.price - buffer ---
        elif close < active_sl["price"] - buffer and active_sl["index"] < i:
            if current_trend == TrendState.BULLISH.value:
                # First bearish break against bullish trend -> CHOCH
                events.append(_event("choch", "bearish", i, active_sl["price"], active_sl["index"], candle["time"]))
                current_trend = TrendState.BEARISH.value
            else:
                events.append(_event("bos", "bearish", i, active_sl["price"], active_sl["index"], candle["time"]))
                current_trend = TrendState.BEARISH.value
            # FIX F2-03: Invalidate broken swing to prevent re-triggering
            # FIX F2-04: Update LL tracking when bearish BOS fires
            last_ll = close
            active_sl = {"index": i, "price": close, "type": "low"}

    # Deduplicate: keep only events with unique break_index
    seen_idx: set[int] = set()
    unique_events: list[dict] = []
    for e in events:
        if e["break_index"] not in seen_idx:
            seen_idx.add(e["break_index"])
            unique_events.append(e)

    unique_events.sort(key=lambda e: e["break_index"])

    return {
        "trend": str(current_trend),
        "events": unique_events,
        "last_hh": last_hh,
        "last_hl": last_hl,
        "last_lh": last_lh,
        "last_ll": last_ll,
    }


def _event(etype: str, direction: str, break_idx: int, break_price: float, swing_idx: int, time: str) -> dict:
    return {
        "event_type": etype,
        "direction": direction,
        "break_index": break_idx,
        "break_price": break_price,
        "broken_swing_index": swing_idx,
        "time": time,
    }


def _result(trend: str, events: list[dict]) -> dict:
    return {
        "trend": trend,
        "events": events,
        "last_hh": None,
        "last_hl": None,
        "last_lh": None,
        "last_ll": None,
    }
