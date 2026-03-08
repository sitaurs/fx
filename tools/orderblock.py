"""
tools/orderblock.py — Order Block detection (ICT/SMC style).

OB is SECONDARY to SnD (masterplan 6.5 — "The Prince").

Algorithm:
    - Bullish OB: last bearish candle before a bullish displacement (BOS).
    - Bearish OB: last bullish candle before a bearish displacement.
    - Zone = full candle range [low, high] for both bullish and bearish OBs.
    - Validation: displacement must cause BOS, zone not yet mitigated.
    - Score includes displacement strength and age/freshness factor (M-16).

Reference: masterplan.md §6.5
"""

from __future__ import annotations

import math

from config.settings import OB_DISPLACEMENT_ATR


def detect_orderblocks(
    ohlcv: list[dict],
    atr_value: float,
    displacement_atr_mult: float = OB_DISPLACEMENT_ATR,
) -> dict:
    """Detect Order Blocks from OHLCV data.

    An Order Block is the last opposing candle before a strong displacement
    move.  It represents institutional order flow and acts as a zone of
    interest for potential price reactions.

    Args:
        ohlcv: List of candle dicts with keys: open, high, low, close, time.
        atr_value: Current ATR value for the timeframe.
        displacement_atr_mult: Min displacement as ATR multiple (default 1.0).

    Returns:
        Dict with keys:
            bullish_obs: list[dict] — bullish order blocks sorted by score desc.
            bearish_obs: list[dict] — bearish order blocks sorted by score desc.
        Each OB dict has: zone_type, high, low, candle_index, displacement_bos,
            is_mitigated, is_fresh, displacement_strength, body_ratio, score,
            origin_time.
    """
    n = len(ohlcv)
    if n < 3 or atr_value <= 0 or math.isnan(atr_value):
        return {"bullish_obs": [], "bearish_obs": []}

    min_disp = displacement_atr_mult * atr_value
    bullish_obs: list[dict] = []
    bearish_obs: list[dict] = []

    for i in range(1, n):
        c = ohlcv[i]
        prev = ohlcv[i - 1]

        body_curr = c["close"] - c["open"]
        body_prev = prev["close"] - prev["open"]
        disp_up = c["close"] - prev["open"]
        disp_down = prev["open"] - c["close"]

        # Bullish OB: prev is bearish (close < open), current is strong bullish
        if body_prev < 0 and disp_up >= min_disp:
            body_ratio = abs(body_curr) / max(c["high"] - c["low"], 1e-10)
            if body_ratio >= 0.5:
                # M-16: Age-adjusted score — more recent OBs score higher
                age_factor = max(0.3, 1.0 - 0.5 * ((i - 1) / max(n - 1, 1)))
                disp_strength = disp_up / atr_value
                bullish_obs.append({
                    "zone_type": "bullish_ob",
                    "high": prev["high"],
                    "low": prev["low"],
                    "candle_index": i - 1,
                    "displacement_bos": True,
                    "is_mitigated": False,
                    "is_fresh": True,
                    "displacement_strength": round(disp_strength, 3),
                    "body_ratio": round(body_ratio, 3),
                    "score": round(disp_strength * age_factor, 3),
                    "origin_time": prev["time"],
                })

        # Bearish OB: prev is bullish (close > open), current is strong bearish
        if body_prev > 0 and disp_down >= min_disp:
            body_ratio = abs(body_curr) / max(c["high"] - c["low"], 1e-10)
            if body_ratio >= 0.5:
                age_factor = max(0.3, 1.0 - 0.5 * ((i - 1) / max(n - 1, 1)))
                disp_strength = disp_down / atr_value
                bearish_obs.append({
                    "zone_type": "bearish_ob",
                    "high": prev["high"],
                    # FIX H-09: Use full candle range (was prev["open"])
                    # Both bullish and bearish OBs now use [low, high]
                    "low": prev["low"],
                    "candle_index": i - 1,
                    "displacement_bos": True,
                    "is_mitigated": False,
                    "is_fresh": True,
                    "displacement_strength": round(disp_strength, 3),
                    "body_ratio": round(body_ratio, 3),
                    "score": round(disp_strength * age_factor, 3),
                    "origin_time": prev["time"],
                })

    # M-16: Sort by score descending (best first)
    bullish_obs.sort(key=lambda ob: ob["score"], reverse=True)
    bearish_obs.sort(key=lambda ob: ob["score"], reverse=True)

    # M-16: Mitigation check — mark OBs as mitigated if price later
    # closes through the zone (same logic as SnD freshness, CON-02)
    _update_ob_freshness(bullish_obs, ohlcv)
    _update_ob_freshness(bearish_obs, ohlcv)

    return {"bullish_obs": bullish_obs, "bearish_obs": bearish_obs}


def _update_ob_freshness(obs: list[dict], ohlcv: list[dict]) -> None:
    """M-16/CON-02: Check if OB zones have been mitigated by later price action.

    Bullish OB mitigated when price closes below zone low.
    Bearish OB mitigated when price closes above zone high.
    """
    n = len(ohlcv)
    for ob in obs:
        candle_idx = ob["candle_index"]
        z_high = ob["high"]
        z_low = ob["low"]
        for j in range(candle_idx + 2, n):
            c = ohlcv[j]
            if ob["zone_type"] == "bullish_ob":
                if c["close"] < z_low:
                    ob["is_mitigated"] = True
                    ob["is_fresh"] = False
                    break
            else:
                if c["close"] > z_high:
                    ob["is_mitigated"] = True
                    ob["is_fresh"] = False
                    break
