"""
tools/price_action.py — Price action pattern detection (confirmation filter).

Patterns (masterplan 6.8):
    - Pin bar: wick > 2× body, close back inside the zone.
    - Engulfing: current body engulfs previous body at a key level.

These are FILTERS, not primary signals.

Reference: masterplan.md §6.8
"""

from __future__ import annotations

from config.settings import PIN_BAR_MIN_WICK_RATIO, ENGULFING_MIN_BODY_RATIO


def detect_pin_bar(
    ohlcv: list[dict],
    min_wick_body_ratio: float = PIN_BAR_MIN_WICK_RATIO,
    zone_levels: list[dict] | None = None,
    atr_value: float = 0.0,
) -> dict:
    """Detect pin bar (rejection) candles.

    Args:
        ohlcv: OHLCV data.
        min_wick_body_ratio: Minimum wick-to-body ratio (default 2.0).
        zone_levels: FIX F2-14 — Optional list of zone dicts with 'high' and 'low'.
                     If provided, only pin bars near a zone are included.
        atr_value: ATR value for proximity calculation (default 0.0 = disabled).

    Returns:
        Dict with pin_bars list.
    """
    n = len(ohlcv)
    pin_bars: list[dict] = []

    for i in range(n):
        c = ohlcv[i]
        body = abs(c["close"] - c["open"])
        rng = c["high"] - c["low"]

        if body < 1e-10 or rng < 1e-10:
            continue

        upper_wick = c["high"] - max(c["open"], c["close"])
        lower_wick = min(c["open"], c["close"]) - c["low"]

        pin_type = None
        wick_ratio = 0.0

        # Bullish pin: long lower wick (rejection of lows)
        if lower_wick > min_wick_body_ratio * body and lower_wick > upper_wick:
            pin_type = "bullish_pin"
            wick_ratio = round(lower_wick / body, 2)

        # Bearish pin: long upper wick (rejection of highs)
        elif upper_wick > min_wick_body_ratio * body and upper_wick > lower_wick:
            pin_type = "bearish_pin"
            wick_ratio = round(upper_wick / body, 2)

        if pin_type is None:
            continue

        # FIX F2-14: Zone proximity filter
        if zone_levels and atr_value > 0:
            if not _near_any_zone(c, zone_levels, atr_value):
                continue

        pin_bars.append({
            "index": i,
            "type": pin_type,
            "wick_ratio": wick_ratio,
            "time": c["time"],
        })

    return {"pin_bars": pin_bars}


def detect_engulfing(
    ohlcv: list[dict],
    zone_levels: list[dict] | None = None,
    atr_value: float = 0.0,
    min_body_ratio: float = ENGULFING_MIN_BODY_RATIO,
) -> dict:
    """Detect engulfing candle patterns.

    Args:
        ohlcv: OHLCV data.
        zone_levels: FIX F2-14 — Optional list of zone dicts with 'high' and 'low'.
        atr_value: ATR value for proximity calculation (default 0.0 = disabled).
        min_body_ratio: Minimum body-to-range ratio for the engulfing candle
                        (FP-10 L-32).  Weak engulfings with tiny bodies relative
                        to their range are filtered out.  Default from config
                        ``ENGULFING_MIN_BODY_RATIO`` (0.3).

    Returns:
        Dict with engulfing_patterns list.
    """
    n = len(ohlcv)
    patterns: list[dict] = []

    for i in range(1, n):
        curr = ohlcv[i]
        prev = ohlcv[i - 1]

        curr_body = curr["close"] - curr["open"]
        prev_body = prev["close"] - prev["open"]

        curr_body_abs = abs(curr_body)
        prev_body_abs = abs(prev_body)

        if prev_body_abs < 1e-10:
            continue

        eng_type = None
        # Bullish engulfing: prev bearish, curr bullish, curr body engulfs prev body
        if prev_body < 0 and curr_body > 0:
            if curr["close"] > prev["open"] and curr["open"] < prev["close"]:
                eng_type = "bullish_engulfing"

        # Bearish engulfing: prev bullish, curr bearish, curr body engulfs prev body
        elif prev_body > 0 and curr_body < 0:
            if curr["close"] < prev["open"] and curr["open"] > prev["close"]:
                eng_type = "bearish_engulfing"

        if eng_type is None:
            continue

        # FP-10 L-32: Body significance check — engulfing body must be
        # a meaningful portion of the candle's full range to filter out
        # weak / deceptive engulfings with long wicks.
        curr_range = curr["high"] - curr["low"]
        if curr_range > 1e-10 and (curr_body_abs / curr_range) < min_body_ratio:
            continue

        # FIX F2-14: Zone proximity filter
        if zone_levels and atr_value > 0:
            if not _near_any_zone(curr, zone_levels, atr_value):
                continue

        patterns.append({
            "index": i,
            "type": eng_type,
            "strength": round(curr_body_abs / prev_body_abs, 2),
            "time": curr["time"],
        })

    return {"engulfing_patterns": patterns}


def _near_any_zone(candle: dict, zones: list[dict], atr_value: float, proximity_mult: float = 0.5) -> bool:
    """FIX F2-14: Check if a candle is within proximity_mult × ATR of any zone."""
    proximity = proximity_mult * atr_value
    price_mid = (candle["high"] + candle["low"]) / 2
    for z in zones:
        z_high = z.get("high", 0)
        z_low = z.get("low", 0)
        # Near if candle midpoint is within proximity of zone boundaries
        if price_mid >= z_low - proximity and price_mid <= z_high + proximity:
            return True
    return False
