"""
tests/test_zones.py — TDD tests for Supply/Demand zone detection.

Reference: masterplan.md §6.4 (Supply/Demand Zones — The King)
Algorithm:
    1. Find base: 2-6 candles, avg range < 0.6 × ATR
    2. After base: displacement >= 1.2 × ATR, body ratio >= 0.6
    3. Demand = base before rally, Supply = base before drop
    4. Zone = [low_base, high_base]

Written FIRST — implementation follows.
"""

from __future__ import annotations

import pytest

from tools.supply_demand import detect_snd_zones


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(
    o: float,
    h: float,
    l: float,
    c: float,
    time: str = "2025-01-01T00:00:00Z",
    vol: float = 1000.0,
) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": vol, "time": time}


def _ts(i: int) -> str:
    return f"2025-01-01T{i:02d}:00:00Z"


def _make_demand_pattern(
    pre_base: float = 2000.0,
    base_len: int = 3,
    base_range: float = 2.0,
    displacement_size: float = 30.0,
    n_pre: int = 5,
) -> list[dict]:
    """Create a demand pattern: pre-rally → small base → big displacement up.

    Returns candles where:
      - First n_pre candles are neutral / slightly down
      - Next base_len candles are a tight base
      - Last 2 candles are a strong rally (displacement)
    """
    candles = []
    idx = 0

    # Pre-base: slight downtrend into the base area
    for i in range(n_pre):
        price = pre_base + 15 - i * 3
        candles.append(_candle(price + 1, price + 3, price - 3, price, _ts(idx)))
        idx += 1

    # Base: tight range candles
    base_mid = pre_base
    for i in range(base_len):
        o = base_mid + (base_range / 4) * (1 if i % 2 == 0 else -1)
        c = base_mid - (base_range / 4) * (1 if i % 2 == 0 else -1)
        h = base_mid + base_range / 2
        l = base_mid - base_range / 2
        candles.append(_candle(o, h, l, c, _ts(idx)))
        idx += 1

    # Displacement up (rally)
    rally_start = base_mid
    rally_step = displacement_size / 2
    for i in range(2):
        o = rally_start + rally_step * i
        c = o + rally_step
        h = c + 2
        l = o - 1
        candles.append(_candle(o, h, l, c, _ts(idx)))
        idx += 1

    return candles


def _make_supply_pattern(
    pre_base: float = 2050.0,
    base_len: int = 3,
    base_range: float = 2.0,
    displacement_size: float = 30.0,
    n_pre: int = 5,
) -> list[dict]:
    """Supply pattern: pre-drop → small base → big displacement down."""
    candles = []
    idx = 0

    # Pre-base: slight uptrend into the base area
    for i in range(n_pre):
        price = pre_base - 15 + i * 3
        candles.append(_candle(price - 1, price + 3, price - 3, price, _ts(idx)))
        idx += 1

    # Base: tight range candles
    base_mid = pre_base
    for i in range(base_len):
        o = base_mid + (base_range / 4) * (1 if i % 2 == 0 else -1)
        c = base_mid - (base_range / 4) * (1 if i % 2 == 0 else -1)
        h = base_mid + base_range / 2
        l = base_mid - base_range / 2
        candles.append(_candle(o, h, l, c, _ts(idx)))
        idx += 1

    # Displacement down (drop)
    drop_start = base_mid
    drop_step = displacement_size / 2
    for i in range(2):
        o = drop_start - drop_step * i
        c = o - drop_step
        l = c - 2
        h = o + 1
        candles.append(_candle(o, h, l, c, _ts(idx)))
        idx += 1

    return candles


# =========================================================================
# TestDetectSndZones
# =========================================================================
class TestDetectSndZones:

    # ----- basic detection -----

    def test_empty_returns_empty(self):
        result = detect_snd_zones([], atr_value=10.0)
        assert result["supply_zones"] == []
        assert result["demand_zones"] == []

    def test_demand_zone_detected(self):
        """Classic demand: tight base followed by rally."""
        candles = _make_demand_pattern(
            pre_base=2000.0, base_len=3, base_range=2.0, displacement_size=30.0
        )
        result = detect_snd_zones(candles, atr_value=10.0)
        demand = result["demand_zones"]
        assert len(demand) >= 1, f"Expected ≥1 demand zone, got {len(demand)}"
        # Zone should be near the base area (around 2000)
        z = demand[0]
        assert z["low"] < 2002
        assert z["high"] > 1998

    def test_supply_zone_detected(self):
        """Classic supply: tight base followed by drop."""
        candles = _make_supply_pattern(
            pre_base=2050.0, base_len=3, base_range=2.0, displacement_size=30.0
        )
        result = detect_snd_zones(candles, atr_value=10.0)
        supply = result["supply_zones"]
        assert len(supply) >= 1, f"Expected ≥1 supply zone, got {len(supply)}"
        z = supply[0]
        assert z["low"] < 2052
        assert z["high"] > 2048

    # ----- zone structure -----

    def test_zone_has_required_keys(self):
        candles = _make_demand_pattern()
        result = detect_snd_zones(candles, atr_value=10.0)
        for z in result["demand_zones"]:
            assert "zone_type" in z
            assert "high" in z
            assert "low" in z
            assert "base_start_idx" in z
            assert "displacement_strength" in z
            assert z["zone_type"] == "demand"

    # ----- filtering -----

    def test_no_zone_if_displacement_too_small(self):
        """Displacement < 1.2 × ATR should not produce a zone."""
        candles = _make_demand_pattern(
            displacement_size=5.0  # ATR=10 → need ≥12, only 5
        )
        result = detect_snd_zones(candles, atr_value=10.0)
        assert len(result["demand_zones"]) == 0

    def test_no_zone_if_base_too_wide(self):
        """Base with avg range > 0.6 × ATR is not a valid base."""
        candles = _make_demand_pattern(
            base_range=15.0,  # ATR=10 → 0.6*10=6, range=15 → too wide
            displacement_size=30.0,
        )
        result = detect_snd_zones(candles, atr_value=10.0)
        assert len(result["demand_zones"]) == 0

    def test_no_zone_if_base_too_short(self):
        """base_min_candles=2 means a single tight candle should not qualify."""
        # Build data that has exactly 1 tight candle then big displacement,
        # but make surrounding candles wide (not base-quality).
        candles = []
        # Wide candles (not base material): range >> ATR*0.6
        for i in range(5):
            p = 2000 + i * 8
            candles.append(_candle(p, p + 10, p - 10, p + 5, _ts(i)))  # range=20
        # 1 tight candle
        candles.append(_candle(2040, 2041, 2039, 2040.5, _ts(5)))  # range=2
        # Displacement up
        candles.append(_candle(2041, 2075, 2040, 2070, _ts(6)))
        candles.append(_candle(2070, 2100, 2068, 2095, _ts(7)))
        result = detect_snd_zones(candles, atr_value=10.0)
        # The single tight candle is only 1 bar, base_min=2 → no valid base
        assert len(result["demand_zones"]) == 0

    # ----- displacement body ratio -----

    def test_displacement_body_ratio_enforced(self):
        """Displacement candle body < 0.6 of range → reject."""
        candles = _make_demand_pattern(base_len=3, displacement_size=30.0)
        # Tamper the displacement candles: make wicks huge, body small
        for i in range(-2, 0):
            c = candles[i]
            mid = (c["open"] + c["close"]) / 2
            candles[i] = _candle(
                o=mid - 0.5,  # tiny body
                h=mid + 20,   # huge wick
                l=mid - 20,   # huge wick
                c=mid + 0.5,
                time=c["time"],
            )
        result = detect_snd_zones(candles, atr_value=10.0)
        # Body ratio of tampered candles ≈ 1/40 = 0.025 → should not qualify
        assert len(result["demand_zones"]) == 0

    # ----- edge cases -----

    def test_monotonic_up_no_supply(self):
        """Pure uptrend → no supply zones (no base before drop)."""
        candles = []
        for i in range(30):
            p = 2000 + i * 5
            candles.append(_candle(p, p + 2, p - 1, p + 3, _ts(i)))
        result = detect_snd_zones(candles, atr_value=10.0)
        assert len(result["supply_zones"]) == 0

    def test_monotonic_down_no_demand(self):
        """Pure downtrend → no demand zones (no base before rally)."""
        candles = []
        for i in range(30):
            p = 2200 - i * 5
            candles.append(_candle(p, p + 1, p - 2, p - 3, _ts(i)))
        result = detect_snd_zones(candles, atr_value=10.0)
        assert len(result["demand_zones"]) == 0

    def test_multiple_zones_detected(self):
        """Two demand patterns concatenated → at least 2 demand zones."""
        p1 = _make_demand_pattern(pre_base=2000.0, displacement_size=30.0)
        p2 = _make_demand_pattern(pre_base=2060.0, displacement_size=30.0)
        # Adjust timestamps for p2
        offset = len(p1)
        for i, c in enumerate(p2):
            c["time"] = _ts(offset + i)
        candles = p1 + p2
        result = detect_snd_zones(candles, atr_value=10.0)
        assert len(result["demand_zones"]) >= 2

    # ----- score ordering -----

    def test_zones_sorted_by_score_desc(self):
        """Zones should be sorted by score, highest first."""
        # Two demand zones: one with bigger displacement should score higher
        p1 = _make_demand_pattern(displacement_size=20.0)  # weaker
        p2 = _make_demand_pattern(pre_base=2060.0, displacement_size=50.0)  # stronger
        offset = len(p1)
        for i, c in enumerate(p2):
            c["time"] = _ts(offset + i)
        candles = p1 + p2
        result = detect_snd_zones(candles, atr_value=10.0)
        zones = result["demand_zones"]
        if len(zones) >= 2:
            scores = [z["score"] for z in zones]
            assert scores == sorted(scores, reverse=True), f"Not sorted desc: {scores}"
