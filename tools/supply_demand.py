"""
tools/supply_demand.py — Supply/Demand zone detection (⭐ The King).

Algorithm (masterplan 6.4):
    1. Scan for "base" segments: 2-6 consecutive candles with avg_range < 0.6 × ATR.
    2. Check displacement AFTER the base:
       - Displacement = move away from base >= 1.2 × ATR.
       - Body ratio of displacement candle(s) >= 0.6.
    3. Classify:
       - Demand = base before rally (close goes up).
       - Supply = base before drop (close goes down).
    4. Zone boundary = [low(base), high(base)].
    5. Score = displacement_strength × freshness × retest_quality.

Reference: masterplan.md §6.4
"""

from __future__ import annotations

import math
from config.settings import (
    SND_BASE_MIN_CANDLES,
    SND_BASE_MAX_CANDLES,
    SND_BASE_AVG_RANGE_ATR,
    SND_DISPLACEMENT_ATR,
    SND_DISPLACEMENT_BODY_RATIO,
    SND_MAX_ZONES,
)

# L-28: Required keys for each candle dict
_REQUIRED_CANDLE_KEYS = {"open", "high", "low", "close", "time"}


def detect_snd_zones(
    ohlcv: list[dict],
    atr_value: float,
    base_min_candles: int = SND_BASE_MIN_CANDLES,
    base_max_candles: int = SND_BASE_MAX_CANDLES,
    base_avg_range_mult: float = SND_BASE_AVG_RANGE_ATR,
    displacement_atr_mult: float = SND_DISPLACEMENT_ATR,
    displacement_body_ratio: float = SND_DISPLACEMENT_BODY_RATIO,
    max_zones: int = SND_MAX_ZONES,
) -> dict:
    """Detect Supply and Demand zones from OHLCV data.

    Args:
        ohlcv: List of candle dicts (open, high, low, close, volume, time).
        atr_value: Current ATR value for the timeframe.
        base_min_candles: Minimum candles in a valid base (default 2).
        base_max_candles: Maximum candles in a valid base (default 6).
        base_avg_range_mult: Max avg candle range as ATR multiple (default 0.6).
        displacement_atr_mult: Min displacement size as ATR multiple (default 1.2).
        displacement_body_ratio: Min body/range ratio for displacement candle (default 0.6).
        max_zones: Maximum zones returned per type (default from config, L-22).

    Returns:
        Dict with keys:
            supply_zones: list[dict] — sorted by score descending, limited to max_zones.
            demand_zones: list[dict] — sorted by score descending, limited to max_zones.
        Each zone dict has: zone_type, formation, high, low, base_start_idx,
            base_end_idx, displacement_strength, body_ratio, score, is_fresh,
            origin_time.

    Raises:
        ValueError: If candle dicts are missing required keys (L-28).
    """
    n = len(ohlcv)

    # L-28: Input validation — check first candle has required keys
    if n > 0 and not _REQUIRED_CANDLE_KEYS.issubset(ohlcv[0].keys()):
        missing = _REQUIRED_CANDLE_KEYS - set(ohlcv[0].keys())
        raise ValueError(f"Candle dict missing required keys: {missing}")

    if n < base_min_candles + 1:  # need at least base + 1 displacement candle
        return {"supply_zones": [], "demand_zones": []}

    if atr_value <= 0 or math.isnan(atr_value):
        return {"supply_zones": [], "demand_zones": []}

    max_base_range = base_avg_range_mult * atr_value
    min_displacement = displacement_atr_mult * atr_value

    supply_zones: list[dict] = []
    demand_zones: list[dict] = []

    # Sliding window to find bases of varying length
    i = 0
    while i < n - base_min_candles:
        found_zone = False

        for blen in range(base_min_candles, base_max_candles + 1):
            base_end = i + blen - 1
            if base_end >= n - 1:
                break  # need at least 1 candle after base for displacement

            # Check base quality: avg range < threshold
            base_candles = ohlcv[i : i + blen]
            avg_range = sum(c["high"] - c["low"] for c in base_candles) / blen

            if avg_range > max_base_range:
                continue  # base too wide

            # Check displacement after base
            zone = _check_displacement(
                ohlcv=ohlcv,
                base_start=i,
                base_end=base_end,
                base_candles=base_candles,
                atr_value=atr_value,
                min_displacement=min_displacement,
                displacement_body_ratio=displacement_body_ratio,
            )

            if zone is not None:
                if zone["zone_type"] == "supply":
                    supply_zones.append(zone)
                else:
                    demand_zones.append(zone)
                # Skip past this base to avoid overlapping zones
                i = base_end + 1
                found_zone = True
                break

        if not found_zone:
            i += 1

    # Sort by score descending
    supply_zones.sort(key=lambda z: z["score"], reverse=True)
    demand_zones.sort(key=lambda z: z["score"], reverse=True)

    # FIX F2-06: Retroactive freshness check — mark zones mitigated if
    # any candle after the base has traded through the zone.
    _update_freshness(supply_zones, ohlcv)
    _update_freshness(demand_zones, ohlcv)

    # L-22: Limit returned zones to max_zones per type
    return {
        "supply_zones": supply_zones[:max_zones],
        "demand_zones": demand_zones[:max_zones],
    }


def _check_displacement(
    ohlcv: list[dict],
    base_start: int,
    base_end: int,
    base_candles: list[dict],
    atr_value: float,
    min_displacement: float,
    displacement_body_ratio: float,
) -> dict | None:
    """Check if there's a valid displacement move after the base.

    Looks at up to 3 candles after the base for cumulative displacement.
    """
    n = len(ohlcv)
    disp_start = base_end + 1

    if disp_start >= n:
        return None

    # Zone boundaries from the base
    base_high = max(c["high"] for c in base_candles)
    base_low = min(c["low"] for c in base_candles)

    # Check displacement candles (1-3 candles after base)
    max_disp_candles = min(3, n - disp_start)

    for disp_len in range(1, max_disp_candles + 1):
        disp_candles = ohlcv[disp_start : disp_start + disp_len]

        # Cumulative displacement = distance from base EDGE to furthest close
        # FIX F2-05: Measure from correct edge, not midpoint
        last_close = disp_candles[-1]["close"]
        first_open = disp_candles[0]["open"]

        # Demand (rally up): displacement from base HIGH (must clear the top)
        displacement_up = last_close - base_high
        # Supply (drop down): displacement from base LOW (must break the bottom)
        displacement_down = base_low - last_close

        # Check body ratio for ALL displacement candles
        total_body = 0.0
        total_range = 0.0
        for dc in disp_candles:
            body = abs(dc["close"] - dc["open"])
            rng = dc["high"] - dc["low"]
            total_body += body
            total_range += max(rng, 1e-10)

        avg_body_ratio = total_body / total_range if total_range > 0 else 0.0

        if avg_body_ratio < displacement_body_ratio:
            continue  # Wicks too big, body too small

        # Check upward displacement → DEMAND zone
        if displacement_up >= min_displacement:
            disp_strength = displacement_up / atr_value
            score = _score_zone(disp_strength, base_start, len(ohlcv))
            formation = _classify_formation("demand", ohlcv, base_start)
            return {
                "zone_type": "demand",
                "formation": formation,
                "high": base_high,
                "low": base_low,
                "base_start_idx": base_start,
                "base_end_idx": base_end,
                "displacement_strength": round(disp_strength, 3),
                "body_ratio": round(avg_body_ratio, 3),
                "score": round(score, 3),
                "is_fresh": True,
                "origin_time": ohlcv[base_start]["time"],
            }

        # Check downward displacement → SUPPLY zone
        if displacement_down >= min_displacement:
            disp_strength = displacement_down / atr_value
            score = _score_zone(disp_strength, base_start, len(ohlcv))
            formation = _classify_formation("supply", ohlcv, base_start)
            return {
                "zone_type": "supply",
                "formation": formation,
                "high": base_high,
                "low": base_low,
                "base_start_idx": base_start,
                "base_end_idx": base_end,
                "displacement_strength": round(disp_strength, 3),
                "body_ratio": round(avg_body_ratio, 3),
                "score": round(score, 3),
                "is_fresh": True,
                "origin_time": ohlcv[base_start]["time"],
            }

    return None


def _score_zone(displacement_strength: float, base_idx: int, total_bars: int) -> float:
    """Score a zone based on displacement strength and recency.

    Score components:
        - displacement_strength: larger displacement = stronger zone
        - freshness: more recent = higher score (linear decay)
    """
    # Freshness: 1.0 for most recent, 0.3 for oldest
    if total_bars > 1:
        freshness = 0.3 + 0.7 * (base_idx / (total_bars - 1))
    else:
        freshness = 1.0

    return displacement_strength * freshness


def _update_freshness(zones: list[dict], ohlcv: list[dict]) -> None:
    """FIX H-08: Mark zones as not fresh only when price closes THROUGH
    the entire zone, not just touching the edge.

    Correct logic:
        - Supply zone mitigated when a later candle closes ABOVE zone high
          (price cleared through the supply zone).
        - Demand zone mitigated when a later candle closes BELOW zone low
          (price broke through the demand zone).

    Previous (incorrect) logic marked zones as mitigated on mere retest
    (close touching the zone edge), causing many valid zones to be lost.
    """
    n = len(ohlcv)
    for zone in zones:
        base_end = zone["base_end_idx"]
        z_high = zone["high"]
        z_low = zone["low"]
        # Scan candles after the displacement (at least 2 bars after base_end)
        scan_start = base_end + 2
        for j in range(scan_start, n):
            c = ohlcv[j]
            if zone["zone_type"] == "supply":
                # Supply mitigated when price closes ABOVE zone top
                if c["close"] > z_high:
                    zone["is_fresh"] = False
                    break
            else:
                # Demand mitigated when price closes BELOW zone bottom
                if c["close"] < z_low:
                    zone["is_fresh"] = False
                    break


def _classify_formation(
    zone_type: str, ohlcv: list[dict], base_start: int
) -> str:
    """CON-11: Classify zone formation type for Pydantic alignment.

    Formation types (masterplan 6.4):
        rally_base_rally — demand continuation
        drop_base_rally  — demand reversal
        rally_base_drop  — supply reversal
        drop_base_drop   — supply continuation
    """
    # Determine pre-base direction from the 3 candles before base
    pre_dir = "rally"
    if base_start >= 2:
        pre_close = ohlcv[base_start - 1]["close"]
        earlier_idx = max(0, base_start - 3)
        earlier_close = ohlcv[earlier_idx]["close"]
        if pre_close < earlier_close:
            pre_dir = "drop"

    if zone_type == "demand":
        return f"{pre_dir}_base_rally"
    else:
        return f"{pre_dir}_base_drop"
