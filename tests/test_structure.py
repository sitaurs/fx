"""
tests/test_structure.py — TDD tests for BOS/CHOCH market structure detection.

Reference: masterplan.md §6.2
Algorithm:
    - Track HH/HL (uptrend) or LH/LL (downtrend)
    - BOS: close breaks last swing + 0.05×ATR buffer → trend continuation
    - CHOCH: first break against current trend → reversal signal

Written FIRST — implementation follows.
"""

from __future__ import annotations

import pytest

from tools.structure import detect_bos_choch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _swing(idx: int, price: float, stype: str = "high") -> dict:
    return {"index": idx, "price": price, "time": f"T{idx}", "type": stype}


# =========================================================================
# TestDetectBosChoch
# =========================================================================
class TestDetectBosChoch:

    # ----- empty/minimal input -----

    def test_empty_swings_returns_ranging(self):
        result = detect_bos_choch([], [], [], atr_value=10.0)
        assert result["trend"] == "ranging"
        assert result["events"] == []

    def test_single_swing_no_events(self):
        """Need at least 2 swing highs or 2 swing lows to detect structure."""
        result = detect_bos_choch(
            ohlcv=[{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 0, "time": "T0"}] * 20,
            swing_highs=[_swing(5, 110)],
            swing_lows=[_swing(10, 90)],
            atr_value=10.0,
        )
        assert result["trend"] == "ranging"

    # ----- uptrend detection (HH + HL) -----

    def test_uptrend_with_higher_highs(self):
        """HH then another HH → bullish BOS events."""
        # Swings: HL=90, HH=110, HL=95, HH=115 → clear uptrend
        swing_highs = [_swing(5, 110), _swing(15, 120)]
        swing_lows = [_swing(0, 90), _swing(10, 95)]
        # OHLCV: bar at index 15 closes above 110 → BOS
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        # Make bar 15 break above swing high at 110
        ohlcv[15] = {"open": 112, "high": 121, "low": 111, "close": 120, "volume": 0, "time": "T15"}

        result = detect_bos_choch(ohlcv, swing_highs, swing_lows, atr_value=10.0)
        assert result["trend"] == "bullish"
        bos_events = [e for e in result["events"] if e["event_type"] == "bos"]
        assert len(bos_events) >= 1

    # ----- downtrend detection (LH + LL) -----

    def test_downtrend_with_lower_lows(self):
        """LL then another LL → bearish BOS."""
        swing_highs = [_swing(5, 110), _swing(15, 105)]  # LH
        swing_lows = [_swing(0, 95), _swing(10, 90)]      # LL
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        # Bar 10 breaks below swing low at 95
        ohlcv[10] = {"open": 93, "high": 94, "low": 88, "close": 89, "volume": 0, "time": "T10"}

        result = detect_bos_choch(ohlcv, swing_highs, swing_lows, atr_value=10.0)
        assert result["trend"] == "bearish"

    # ----- CHOCH detection -----

    def test_choch_detected_on_trend_reversal(self):
        """Uptrend → break of last HL → CHOCH bearish."""
        swing_highs = [_swing(3, 110), _swing(9, 120)]   # HH
        swing_lows = [_swing(0, 90), _swing(6, 100)]      # HL
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        # Confirm uptrend first: bar 9 breaks above 110
        ohlcv[9] = {"open": 112, "high": 121, "low": 111, "close": 120, "volume": 0, "time": "T9"}
        # Then bar 15 breaks BELOW the last HL at 100 → CHOCH
        ohlcv[15] = {"open": 98, "high": 99, "low": 88, "close": 89, "volume": 0, "time": "T15"}

        result = detect_bos_choch(ohlcv, swing_highs, swing_lows, atr_value=10.0)
        choch_events = [e for e in result["events"] if e["event_type"] == "choch"]
        assert len(choch_events) >= 1
        # The CHOCH should be bearish (reversal from bullish)
        assert any(e["direction"] == "bearish" for e in choch_events)

    # ----- output structure -----

    def test_event_has_required_keys(self):
        swing_highs = [_swing(5, 110), _swing(15, 120)]
        swing_lows = [_swing(0, 90), _swing(10, 95)]
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        ohlcv[15] = {"open": 112, "high": 121, "low": 111, "close": 120, "volume": 0, "time": "T15"}

        result = detect_bos_choch(ohlcv, swing_highs, swing_lows, atr_value=10.0)
        assert "trend" in result
        assert "events" in result
        for e in result["events"]:
            assert "event_type" in e
            assert "direction" in e
            assert "break_index" in e
            assert "break_price" in e

    # ----- ATR buffer -----

    def test_atr_buffer_prevents_false_break(self):
        """Close barely above swing (< 0.05×ATR) should NOT count as BOS."""
        swing_highs = [_swing(5, 110), _swing(15, 115)]
        swing_lows = [_swing(0, 90), _swing(10, 95)]
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        # Bar 15: close = 110.3, just barely above 110 but < 110 + 0.05*10 = 110.5
        ohlcv[15] = {"open": 109, "high": 111, "low": 108, "close": 110.3, "volume": 0, "time": "T15"}

        result = detect_bos_choch(ohlcv, swing_highs, swing_lows, atr_value=10.0)
        bos_events = [e for e in result["events"] if e["event_type"] == "bos" and e["break_index"] == 15]
        assert len(bos_events) == 0, "Close within ATR buffer should not trigger BOS"

    # ----- events sorted -----

    def test_events_sorted_by_index(self):
        swing_highs = [_swing(5, 110), _swing(15, 120)]
        swing_lows = [_swing(0, 90), _swing(10, 95)]
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        ohlcv[15] = {"open": 112, "high": 121, "low": 111, "close": 120, "volume": 0, "time": "T15"}

        result = detect_bos_choch(ohlcv, swing_highs, swing_lows, atr_value=10.0)
        indices = [e["break_index"] for e in result["events"]]
        assert indices == sorted(indices), f"Events not sorted: {indices}"
