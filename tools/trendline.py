"""
tools/trendline.py — TRUE Trendline Detection (Ray Extension).

A trendline is NOT a short segment between two points.  It is a RAY — a line
extended from Anchor A through Anchor B all the way to the rightmost candle.

**Uptrend / Support / LANTAI:**
    The ray must sit BELOW all candle lows from Anchor A to the latest bar.
    If even ONE candle low breaches below the ray → the line is BROKEN → INVALID.

**Downtrend / Resistance / ATAP:**
    The ray must sit ABOVE all candle highs from Anchor A to the latest bar.
    If even ONE candle high breaches above the ray → the line is BROKEN → INVALID.

Algorithm:
    1.  For each pair of swing points (A, B), compute y = slope*(x - xA) + pA.
    2.  Extend the line as a RAY to index = len(ohlcv) - 1.
    3.  Validate the ENTIRE ray: every candle from A to LAST must respect the
        floor/ceiling rule.
    4.  Count bonus touches along the full ray.
    5.  Score = touches × ray_length × recency.
    6.  Return max 2 best per direction, deduplicated.

Reference: masterplan.md §6.7
"""

from __future__ import annotations

import logging
import math

from config.settings import TRENDLINE_TOLERANCE, TRENDLINE_MAX_RAY_BARS

logger = logging.getLogger(__name__)

# Max lines to return per direction (up / down)
MAX_LINES_PER_DIRECTION: int = 2


def detect_trendlines(
    swing_highs: list[dict],
    swing_lows: list[dict],
    ohlcv: list[dict],
    pair: str = "XAUUSD",
    min_touches: int = 2,
    atr_value: float = 0.0,
    max_ray_bars: int = TRENDLINE_MAX_RAY_BARS,
) -> dict:
    """Detect trendlines as extended rays that act as floor/ceiling.

    Args:
        swing_highs: Swing high points [{index, price, ...}].
        swing_lows: Swing low points [{index, price, ...}].
        ohlcv: Full candle data — REQUIRED for ray validation.
        pair: Trading pair (for tolerance lookup).
        min_touches: Minimum touches including anchors (default 2).
        atr_value: Current ATR value for dynamic tolerance (FIX F2-10).
        max_ray_bars: Maximum bars a ray may extend beyond the last anchor
                      before it is considered unreliable (FP-10 M-22).
                      Default from ``TRENDLINE_MAX_RAY_BARS`` config.

    Returns:
        Dict with uptrend_lines (max 2) and downtrend_lines (max 2).
        Each line includes ray_end_index, ray_end_price for chart drawing,
        and ``type`` ("support" | "resistance") for chart overlay (CON-17).
    """
    static_tol = TRENDLINE_TOLERANCE.get(pair, 0.001)
    # FIX F2-10: ATR-adaptive tolerance — bounded between 50% and 300% of static
    if atr_value > 0:
        tolerance = min(0.15 * atr_value, static_tol * 3)
        tolerance = max(tolerance, static_tol * 0.5)
    else:
        tolerance = static_tol
    n = len(ohlcv) if ohlcv else 0

    uptl = _fit_ray_trendlines(swing_lows, ohlcv, "low", tolerance, min_touches, n, max_ray_bars)
    dntl = _fit_ray_trendlines(swing_highs, ohlcv, "high", tolerance, min_touches, n, max_ray_bars)

    return {
        "uptrend_lines": uptl,
        "downtrend_lines": dntl,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _line_y(slope: float, x: int, x0: int, y0: float) -> float:
    """Calculate y-value of line at index *x*."""
    return slope * (x - x0) + y0


def _ray_is_valid(
    ohlcv: list[dict],
    idx_start: int,
    slope: float,
    p_start: float,
    price_key: str,
    tolerance: float,
) -> bool:
    """Check that the ENTIRE ray from idx_start to the LAST candle is unbroken.

    For uptrend (price_key="low"):
        Every candle low from idx_start+1 to end must be >= line_y - tolerance.
        (The line is a FLOOR — nothing may fall through it.)

    For downtrend (price_key="high"):
        Every candle high from idx_start+1 to end must be <= line_y + tolerance.
        (The line is a CEILING — nothing may punch through it.)

    Returns True if the ray is VALID (unbroken), False if broken.
    """
    if not ohlcv:
        return True  # no data → cannot validate → assume valid

    last_idx = len(ohlcv) - 1

    for k in range(idx_start + 1, last_idx + 1):
        line_val = _line_y(slope, k, idx_start, p_start)
        candle_price = ohlcv[k][price_key]

        if price_key == "low":
            # FLOOR check: candle low must stay ABOVE the line
            if candle_price < line_val - tolerance:
                return False
        else:
            # CEILING check: candle high must stay BELOW the line
            if candle_price > line_val + tolerance:
                return False

    return True


def _fit_ray_trendlines(
    swings: list[dict],
    ohlcv: list[dict],
    price_key: str,
    tolerance: float,
    min_touches: int,
    total_bars: int,
    max_ray_bars: int = 100,
) -> list[dict]:
    """Build trendlines as RAYS validated from anchor to the last candle.

    Steps:
        1. For each pair of swings (A, B): compute slope.
        2. Reject wrong-direction slopes.
        3. FP-10 M-22: Reject if ray extends > max_ray_bars beyond last anchor.
        4. Reject if ANY candle from A to LAST CANDLE breaches the ray.
        5. Count touches along the full ray among all swings.
        6. Score by touches × ray_length × recency.
        7. Deduplicate, return top MAX_LINES_PER_DIRECTION.
    """
    if len(swings) < 2 or total_bars < 2:
        return []

    sorted_sw = sorted(swings, key=lambda s: s["index"])
    last_bar_idx = total_bars - 1
    candidates: list[dict] = []

    for i in range(len(sorted_sw)):
        for j in range(i + 1, len(sorted_sw)):
            s1, s2 = sorted_sw[i], sorted_sw[j]
            idx1, idx2 = s1["index"], s2["index"]
            p1, p2 = s1["price"], s2["price"]

            if idx2 <= idx1:
                continue

            span = idx2 - idx1
            if span < 5:
                logger.debug("Rejected pair (%d,%d): span %d < 5", idx1, idx2, span)
                continue

            slope = (p2 - p1) / span

            # --- Slope direction filter ---
            if price_key == "low" and slope <= 0:
                logger.debug("Rejected pair (%d,%d): uptrend slope %.6f <= 0", idx1, idx2, slope)
                continue  # uptrend must ascend
            if price_key == "high" and slope >= 0:
                logger.debug("Rejected pair (%d,%d): downtrend slope %.6f >= 0", idx1, idx2, slope)
                continue  # downtrend must descend

            # --- FP-10 M-22: Ray extension validity bounds ---
            extension_bars = last_bar_idx - idx2
            if extension_bars > max_ray_bars:
                logger.debug(
                    "Rejected pair (%d,%d): extension %d bars > max %d",
                    idx1, idx2, extension_bars, max_ray_bars,
                )
                continue

            # ============================================================
            # CRITICAL: Validate the FULL RAY from anchor A to LAST candle
            # This is the "Lantai/Atap" rule: the line must hold from
            # anchor A all the way to the current price.
            # ============================================================
            if ohlcv and not _ray_is_valid(
                ohlcv, idx1, slope, p1, price_key, tolerance
            ):
                logger.debug(
                    "Rejected pair (%d,%d): ray broken (price_key=%s)",
                    idx1, idx2, price_key,
                )
                continue  # ray is broken → INVALID

            # --- Count touches along the full ray ---
            touches = []
            for sw in sorted_sw:
                expected = _line_y(slope, sw["index"], idx1, p1)
                if abs(sw["price"] - expected) <= tolerance:
                    touches.append(sw)

            if len(touches) < min_touches:
                continue

            # --- Compute score ---
            touch_count = len(touches)
            # Ray length = from anchor A to the last bar (not just to B)
            ray_length = last_bar_idx - idx1
            ray_score = math.log2(max(ray_length, 1) + 1)

            # Recency: how recent is the rightmost anchor relative to total
            recency = (idx2 / total_bars) if total_bars > 0 else 1.0

            # Tightness
            total_dev = 0.0
            for t in touches:
                expected = _line_y(slope, t["index"], idx1, p1)
                total_dev += abs(t["price"] - expected)
            avg_dev = total_dev / touch_count
            tightness = min(1.0 / (avg_dev + 1e-6), 10.0)

            score = touch_count * ray_score * (0.3 + 0.7 * recency) * tightness

            # Compute ray end point for chart drawing
            ray_end_price = _line_y(slope, last_bar_idx, idx1, p1)

            candidates.append({
                "anchor_1": {"index": idx1, "price": p1},
                "anchor_2": {"index": idx2, "price": p2},
                "ray_end_index": last_bar_idx,
                "ray_end_price": round(ray_end_price, 5),
                "slope": round(slope, 8),
                "touches": touch_count,
                "touch_indices": [t["index"] for t in touches],
                "score": round(score, 3),
                "direction": "uptrend" if price_key == "low" else "downtrend",
                # FP-10 CON-17: chart overlay type alignment
                "type": "support" if price_key == "low" else "resistance",
            })

    # Sort by score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Deduplicate
    final = _deduplicate(candidates)

    return final[:MAX_LINES_PER_DIRECTION]


def _deduplicate(lines: list[dict], overlap_threshold: float = 0.5) -> list[dict]:
    """Remove lines that share too many touch indices with a higher-scored line."""
    if not lines:
        return []

    kept: list[dict] = []
    for candidate in lines:
        c_set = set(candidate["touch_indices"])
        is_dup = False
        for existing in kept:
            e_set = set(existing["touch_indices"])
            if not c_set or not e_set:
                continue
            overlap = len(c_set & e_set) / min(len(c_set), len(e_set))
            if overlap >= overlap_threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(candidate)

    return kept


# NOTE: is_touch_valid() removed in FP-10 D-08 — it was dead code (never
# called in production).  The main detect_trendlines() already uses
# ATR-adaptive tolerance for touch validation internally.
