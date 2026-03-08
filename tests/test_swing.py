"""
tests/test_swing.py — TDD tests for swing point detection.

Reference: masterplan.md §6.1 (Swing Detection)
- Fractals: high[i] = max(high[i-k:i+k+1])
- Filter: jarak antar swing >= 0.5 × ATR
- Params per TF: H4 k=3, H1 k=4, M30 k=5, M15 k=6

Written FIRST — implementation follows.
"""

from __future__ import annotations

import math
import pytest

from tools.swing import detect_swing_points


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(
    close: float,
    high: float | None = None,
    low: float | None = None,
    spread: float = 1.0,
    time: str = "2025-01-01T00:00:00Z",
) -> dict:
    """Create a candle dict.  If *high*/*low* not given, derive from close."""
    h = high if high is not None else close + spread / 2
    l = low if low is not None else close - spread / 2
    o = close  # simplification: open == close
    return {"open": o, "high": h, "low": l, "close": close, "volume": 100.0, "time": time}


def _make_pattern(
    values: list[float],
    spread: float = 1.0,
) -> list[dict]:
    """Turn a list of *close* values into candle dicts with sequential timestamps."""
    candles = []
    for i, v in enumerate(values):
        candles.append(
            _candle(v, spread=spread, time=f"2025-01-01T{i:02d}:00:00Z")
        )
    return candles


def _make_mountain(
    n: int = 15,
    peak_idx: int = 7,
    base: float = 100.0,
    height: float = 20.0,
) -> list[dict]:
    """Create a mountain (V-peak) pattern — price rises to peak then falls.

    Spread kept small so high/low are close to close (clean fractals).
    """
    candles = []
    for i in range(n):
        dist = abs(i - peak_idx)
        close = base + height - dist * (height / peak_idx)
        close = max(close, base)
        candles.append(
            _candle(close, spread=0.5, time=f"2025-01-01T{i:02d}:00:00Z")
        )
    return candles


def _make_valley(
    n: int = 15,
    trough_idx: int = 7,
    base: float = 120.0,
    depth: float = 20.0,
) -> list[dict]:
    """Create a valley (V-trough) — price falls to trough then rises."""
    candles = []
    for i in range(n):
        dist = abs(i - trough_idx)
        close = base - depth + dist * (depth / trough_idx)
        close = min(close, base)
        candles.append(
            _candle(close, spread=0.5, time=f"2025-01-01T{i:02d}:00:00Z")
        )
    return candles


# =========================================================================
# TestDetectSwingPoints
# =========================================================================
class TestDetectSwingPoints:
    """Core functionality of detect_swing_points."""

    # ----- empty / too-short input -----

    def test_empty_input_returns_empty(self):
        result = detect_swing_points([], lookback=3)
        assert result["swing_highs"] == []
        assert result["swing_lows"] == []

    def test_too_few_candles_returns_empty(self):
        """Fewer candles than 2*lookback+1 → no fractals possible."""
        candles = _make_pattern([100, 101, 102], spread=0.5)
        result = detect_swing_points(candles, lookback=3)
        assert result["swing_highs"] == []
        assert result["swing_lows"] == []

    # ----- basic fractal detection -----

    def test_single_peak_detected(self):
        """Mountain shape → exactly 1 swing high near the peak."""
        candles = _make_mountain(n=15, peak_idx=7, base=100, height=20)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)

        highs = result["swing_highs"]
        assert len(highs) >= 1, "Should detect at least 1 swing high"
        # The peak index should be at or very near 7
        peak = max(highs, key=lambda s: s["price"])
        assert peak["index"] == 7

    def test_single_trough_detected(self):
        """Valley shape → exactly 1 swing low near the trough."""
        candles = _make_valley(n=15, trough_idx=7, base=120, depth=20)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)

        lows = result["swing_lows"]
        assert len(lows) >= 1, "Should detect at least 1 swing low"
        trough = min(lows, key=lambda s: s["price"])
        assert trough["index"] == 7

    def test_two_peaks_and_one_trough(self):
        """W-shape: peak → trough → peak."""
        # Build: rise, fall, rise, fall
        values = [100, 102, 105, 108, 110,   # rise to first peak idx=4
                  108, 105, 102, 100, 98,     # fall to trough idx=9
                  100, 102, 105, 108, 110,    # rise to second peak idx=14
                  108, 105, 102, 100]          # fall
        candles = _make_pattern(values, spread=0.3)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)

        highs = result["swing_highs"]
        lows = result["swing_lows"]
        assert len(highs) >= 2, f"Expected ≥2 swing highs, got {len(highs)}"
        assert len(lows) >= 1, f"Expected ≥1 swing low, got {len(lows)}"

    # ----- swing point structure -----

    def test_swing_point_has_required_keys(self):
        """Each swing point must have: index, price, time, type."""
        candles = _make_mountain(n=15, peak_idx=7)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)

        for sh in result["swing_highs"]:
            assert "index" in sh
            assert "price" in sh
            assert "time" in sh
            assert sh["type"] == "high"

        for sl in result["swing_lows"]:
            assert "index" in sl
            assert "price" in sl
            assert "time" in sl
            assert sl["type"] == "low"

    # ----- ATR distance filter -----

    def test_min_distance_filters_close_swings(self):
        """With min_distance_atr > 0, closely-spaced swings get filtered."""
        # Create clear oscillating pattern: up-down-up-down with proper amplitude
        # Each cycle: 7 bars (rise 3, peak 1, fall 3) × N cycles
        values = []
        for cycle in range(6):
            base = 100.0
            offset = cycle * 7
            # rise, peak, fall pattern — peaks at different small heights
            values.extend([base, base + 3, base + 5, base + 8,
                           base + 5, base + 3, base])
        candles = _make_pattern(values, spread=0.3)

        # Without filter → should find multiple swing highs/lows
        raw = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        raw_total = len(raw["swing_highs"]) + len(raw["swing_lows"])
        assert raw_total >= 2, f"Need ≥2 raw swings for test, got {raw_total}"

        # With aggressive filter → fewer swings (peaks are close in price)
        filtered = detect_swing_points(candles, lookback=3, min_distance_atr=2.0)
        filtered_total = len(filtered["swing_highs"]) + len(filtered["swing_lows"])

        assert filtered_total < raw_total, (
            f"ATR filter should reduce swings: raw={raw_total}, filtered={filtered_total}"
        )

    def test_min_distance_zero_keeps_all_fractals(self):
        """min_distance_atr=0.0 should not filter any fractals."""
        candles = _make_mountain(n=15, peak_idx=7)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        # At least the peak should remain
        assert len(result["swing_highs"]) >= 1

    # ----- lookback parameter -----

    def test_larger_lookback_yields_fewer_swings(self):
        """Larger lookback means stricter fractal → fewer detected swings."""
        # Oscillating data with many peaks
        values = [100 + 10 * ((i % 6) - 3) for i in range(60)]
        candles = _make_pattern(values, spread=0.3)

        r_small = detect_swing_points(candles, lookback=2, min_distance_atr=0.0)
        r_large = detect_swing_points(candles, lookback=5, min_distance_atr=0.0)

        total_small = len(r_small["swing_highs"]) + len(r_small["swing_lows"])
        total_large = len(r_large["swing_highs"]) + len(r_large["swing_lows"])

        assert total_large <= total_small, (
            f"Larger lookback should find ≤ swings: k=2 → {total_small}, k=5 → {total_large}"
        )

    # ----- monotonic data -----

    def test_monotonic_up_no_swing_highs(self):
        """Strictly rising prices → no swing highs (no pivot down yet)."""
        values = list(range(100, 130))  # 30 bars, monotonic up
        candles = _make_pattern(values, spread=0.2)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        assert len(result["swing_highs"]) == 0

    def test_monotonic_down_no_swing_lows(self):
        """Strictly falling prices → no swing lows (no pivot up yet)."""
        values = list(range(130, 100, -1))  # 30 bars, monotonic down
        candles = _make_pattern(values, spread=0.2)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        assert len(result["swing_lows"]) == 0

    # ----- constant price -----

    def test_constant_price_no_swings(self):
        """Flat price → FIX F2-01: tied maxima ARE now detected in flat markets.

        With the fix, constant-price candles all qualify as swings
        (centre equals the max). The distance filter thins duplicates."""
        values = [100.0] * 30
        candles = _make_pattern(values, spread=0.0)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        # FIX F2-01: constant price → all qualify; distance filter=0 keeps all
        assert len(result["swing_highs"]) >= 1
        assert len(result["swing_lows"]) >= 1

    # ----- output-level: sorted by index -----

    def test_swings_sorted_by_index(self):
        """Swing points should be returned sorted by index (chronologically)."""
        values = [100, 102, 105, 108, 110,   # peak
                  108, 105, 102, 100, 98,     # trough
                  100, 102, 105, 108, 110,    # peak
                  108, 105, 102, 100]
        candles = _make_pattern(values, spread=0.3)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)

        for key in ("swing_highs", "swing_lows"):
            indices = [s["index"] for s in result[key]]
            assert indices == sorted(indices), f"{key} not sorted by index: {indices}"

    # ----- realistic multi-TF lookback from masterplan -----

    def test_h4_lookback_3(self):
        """H4 uses k=3 (7-bar window). Verify a clean peak is detected."""
        candles = _make_mountain(n=21, peak_idx=10, base=2000, height=50)
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        highs = result["swing_highs"]
        assert any(s["index"] == 10 for s in highs), "Peak at idx=10 not found with k=3"

    def test_m15_lookback_6(self):
        """M15 uses k=6 (13-bar window). Verify a clean peak is detected."""
        candles = _make_mountain(n=30, peak_idx=15, base=2000, height=50)
        result = detect_swing_points(candles, lookback=6, min_distance_atr=0.0)
        highs = result["swing_highs"]
        assert any(s["index"] == 15 for s in highs), "Peak at idx=15 not found with k=6"
