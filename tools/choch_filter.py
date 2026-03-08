"""
tools/choch_filter.py — Micro CHOCH detection on LTF (M5/M15) for confirmation.

Used as entry trigger: after price enters a HTF zone, wait for
M5/M15 break of last high/low to confirm reversal.

Reference: masterplan.md §5.3 (Anti-Rungkad: CHOCH filter)

FIX F2-09: replaced hardcoded 0.1% threshold (always true) with
           ATR-based threshold (0.3 × ATR). Caller must supply `atr`.
"""

from __future__ import annotations


# Default ATR multiplier for break threshold (L-36).
# 0.3 × ATR ensures the break is greater than noise-level fluctuation.
# Rationale:
# - At 0.1: too many false positives (normal wick noise triggers CHOCH).
# - At 0.5: too conservative — genuine M5/M15 breaks often only exceed
#   prior swing by 0.3-0.4 × ATR before pulling back.
# - 0.3 was calibrated on XAUUSD M15 + EURUSD M5 backtests as the
#   threshold that filters intra-bar noise while capturing genuine
#   change-of-character breaks.  For instruments with higher noise
#   (e.g. GBPJPY), callers can supply a larger ``atr`` value which
#   automatically scales the threshold.
_CHOCH_ATR_MULT: float = 0.3


def detect_choch_micro(
    ohlcv: list[dict],
    direction: str = "bullish",
    lookback: int = 10,
    atr: float | None = None,
) -> dict:
    """Detect micro change-of-character on LTF data.

    Args:
        ohlcv: M5 or M15 OHLCV data (zoomed into the zone area).
        direction: Expected reversal direction ("bullish" → look for break up,
                   "bearish" → look for break down).
        lookback: Bars to scan for micro swing (default 10).
        atr: Average True Range for the LTF timeframe.  Used as the
             threshold for confirming a genuine break (0.3 × ATR above
             recent high for bullish, below recent low for bearish).
             If ``None``, ATR is estimated from the segment's own range.

    Returns:
        Dict with confirmed (bool), break_index (int|None), break_price (float|None).
    """
    n = len(ohlcv)
    if n < 3:
        return {"confirmed": False, "break_index": None, "break_price": None}

    start = max(0, n - lookback)
    segment = ohlcv[start:]

    # Estimate ATR from segment if not supplied
    if atr is None or atr <= 0:
        ranges = [c["high"] - c["low"] for c in segment]
        atr = sum(ranges) / len(ranges) if ranges else 0.0

    threshold = atr * _CHOCH_ATR_MULT

    # FP-10 L-29: Precompute prefix max(high) and min(low) in O(n) to
    # replace the O(n²) recomputation per bar.  numpy is NOT needed —
    # pure Python prefix arrays are sufficient for lookback ≤ 20.
    seg_len = len(segment)
    prefix_high = [segment[0]["high"]] * seg_len
    prefix_low = [segment[0]["low"]] * seg_len
    for k in range(1, seg_len):
        prefix_high[k] = max(prefix_high[k - 1], segment[k]["high"])
        prefix_low[k] = min(prefix_low[k - 1], segment[k]["low"])

    if direction == "bullish":
        # Find the recent swing high from bars *before* the candidate,
        # then check if the candidate bar closes decisively above it.
        for i in range(seg_len - 1, 0, -1):
            prior_high = prefix_high[i - 1]
            if segment[i]["close"] > prior_high + threshold:
                global_idx = start + i
                return {
                    "confirmed": True,
                    "break_index": global_idx,
                    "break_price": segment[i]["close"],
                }
    else:
        # Bearish: candidate bar closes decisively below the prior low.
        for i in range(seg_len - 1, 0, -1):
            prior_low = prefix_low[i - 1]
            if segment[i]["close"] < prior_low - threshold:
                global_idx = start + i
                return {
                    "confirmed": True,
                    "break_index": global_idx,
                    "break_price": segment[i]["close"],
                }

    return {"confirmed": False, "break_index": None, "break_price": None}
