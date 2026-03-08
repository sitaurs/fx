"""
tools/swing.py — Swing point (fractal pivot) detection.

Algorithm (masterplan 6.1):
    Swing High at index *i* iff high[i] == max(high[i-k : i+k+1])  (strict)
    Swing Low  at index *i* iff low[i]  == min(low[i-k : i+k+1])   (strict)

    Filter: distance between consecutive swings of the same type ≥ min_distance_atr × ATR.

Params per TF (masterplan 6.1):
    H4  → k=3
    H1  → k=4
    M30 → k=5
    M15 → k=6

Reference: masterplan.md §6.1, §5.1
"""

from __future__ import annotations

import math

from tools.indicators import compute_atr


def detect_swing_points(
    ohlcv: list[dict],
    lookback: int = 5,
    min_distance_atr: float = 0.5,
    handle_boundary: bool = False,
) -> dict:
    """Detect swing high/low points from OHLCV data using fractal pivots.

    Args:
        ohlcv: List of candle dicts with keys: open, high, low, close, volume, time.
        lookback: Window size *k* — a candle must be the highest/lowest within
            the surrounding 2k+1 bars to qualify as a swing.
        min_distance_atr: Minimum distance (in ATR multiples) between consecutive
            swing points of the same type.  Set to 0.0 to disable filtering.
        handle_boundary: When True, detect swings in the first/last *k* candles
            using a reduced (asymmetric) window. Default False for backward
            compatibility.  (FIX M-15)

    Default ``lookback=5`` rationale (L-24):
        k=5 (11-bar window) is a mid-range compromise used when no specific
        timeframe is known.  Per-TF values in ``config/settings.py``
        (``SWING_LOOKBACK``) are:
            H4 → k=3 (7 bars ≈ 28 h)
            H1 → k=4 (9 bars ≈ 9 h)
            M30 → k=3 (7 bars ≈ 3.5 h)
            M15 → k=4 (9 bars ≈ 2.25 h)
        Callers should pass the TF-appropriate value from ``SWING_LOOKBACK``.

    Returns:
        Dict with keys:
            swing_highs: list[dict]  — each has {index, price, time, type:"high"}
            swing_lows:  list[dict]  — each has {index, price, time, type:"low"}
        Both lists are sorted by index (chronological).
    """
    n = len(ohlcv)
    window = 2 * lookback + 1

    if n < window:
        return {"swing_highs": [], "swing_lows": []}

    # ------------------------------------------------------------------
    # Step 1: detect raw fractals
    # ------------------------------------------------------------------
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]

    raw_swing_highs: list[dict] = []
    raw_swing_lows: list[dict] = []

    for i in range(lookback, n - lookback):
        window_highs = highs[i - lookback : i + lookback + 1]
        window_lows = lows[i - lookback : i + lookback + 1]

        # FIX F2-01: Fractal high — centre must be max; ties allowed (no uniqueness check)
        # Duplicates from flat markets are handled by the distance filter in Step 2.
        if highs[i] == max(window_highs):
            raw_swing_highs.append(
                {
                    "index": i,
                    "price": highs[i],
                    "time": ohlcv[i]["time"],
                    "type": "high",
                }
            )

        # FIX F2-01: Fractal low — centre must be min; ties allowed
        if lows[i] == min(window_lows):
            raw_swing_lows.append(
                {
                    "index": i,
                    "price": lows[i],
                    "time": ohlcv[i]["time"],
                    "type": "low",
                }
            )

    # ------------------------------------------------------------------
    # Step 1b (M-15): Boundary candle detection (optional)
    # The core loop skips the first and last `lookback` candles because
    # they lack a full symmetric window. When handle_boundary is True,
    # we use a reduced adaptive window at the edges.
    # ------------------------------------------------------------------
    if handle_boundary:
        for i in list(range(1, lookback)) + list(range(max(lookback, n - lookback), n - 1)):
            k = min(lookback, i, n - 1 - i)
            if k < 1:
                continue
            w_highs = highs[i - k : i + k + 1]
            w_lows = lows[i - k : i + k + 1]

            if highs[i] == max(w_highs):
                raw_swing_highs.append(
                    {"index": i, "price": highs[i], "time": ohlcv[i]["time"], "type": "high"}
                )
            if lows[i] == min(w_lows):
                raw_swing_lows.append(
                    {"index": i, "price": lows[i], "time": ohlcv[i]["time"], "type": "low"}
                )

        # Deduplicate and re-sort after boundary additions
        _dedup = lambda lst: list({s["index"]: s for s in lst}.values())
        raw_swing_highs = sorted(_dedup(raw_swing_highs), key=lambda s: s["index"])
        raw_swing_lows = sorted(_dedup(raw_swing_lows), key=lambda s: s["index"])

    # ------------------------------------------------------------------
    # Step 2: ATR distance filter
    # ------------------------------------------------------------------
    if min_distance_atr > 0.0 and n > 0:
        atr_result = compute_atr(ohlcv, period=14)
        atr_current = atr_result["current"]
        if math.isnan(atr_current):
            # Fallback: use average true range of all bars
            ranges = [c["high"] - c["low"] for c in ohlcv]
            atr_current = sum(ranges) / len(ranges) if ranges else 0.0

        min_dist = min_distance_atr * atr_current

        raw_swing_highs = _filter_by_distance(raw_swing_highs, min_dist)
        raw_swing_lows = _filter_by_distance(raw_swing_lows, min_dist)

    return {
        "swing_highs": raw_swing_highs,
        "swing_lows": raw_swing_lows,
    }


def _filter_by_distance(swings: list[dict], min_dist: float, min_bars: int = 5) -> list[dict]:
    """FIX F2-02: Keep swings >= min_dist apart in PRICE **or** >= min_bars apart in INDEX.

    The original version only compared price, which dropped time-separated
    swings at similar prices — common in ranging/flat markets.
    """
    if not swings or (min_dist <= 0.0 and min_bars <= 0):
        return swings

    kept: list[dict] = [swings[0]]
    for s in swings[1:]:
        price_ok = abs(s["price"] - kept[-1]["price"]) >= min_dist
        time_ok = abs(s["index"] - kept[-1]["index"]) >= min_bars
        if price_ok or time_ok:
            kept.append(s)
    return kept
