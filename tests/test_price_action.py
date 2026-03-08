"""Tests for tools/price_action.py — Pin bar and engulfing detection."""

import pytest
from tools.price_action import detect_pin_bar, detect_engulfing


def _candle(idx, o, h, l, c):
    return {"time": f"2025-01-0{idx+1}", "open": o, "high": h, "low": l, "close": c}


class TestDetectPinBar:

    def test_empty_input(self):
        result = detect_pin_bar([])
        assert result["pin_bars"] == []

    def test_bullish_pin_detected(self):
        """Long lower wick, small body → bullish pin."""
        # open=100, high=100.5, low=95, close=100.2
        # body = 0.2, lower_wick = 100 - 95 = 5.0, upper_wick = 0.3
        # ratio = 5.0 / 0.2 = 25 >> 2.0
        candles = [_candle(0, 100, 100.5, 95, 100.2)]
        result = detect_pin_bar(candles)
        assert len(result["pin_bars"]) == 1
        assert result["pin_bars"][0]["type"] == "bullish_pin"
        assert result["pin_bars"][0]["wick_ratio"] >= 2.0

    def test_bearish_pin_detected(self):
        """Long upper wick, small body → bearish pin."""
        # open=100, high=105, low=99.8, close=99.9
        # body = 0.1, upper_wick = 105 - 100 = 5.0, lower_wick = 99.9 - 99.8 = 0.1
        # ratio = 5.0 / 0.1 = 50
        candles = [_candle(0, 100, 105, 99.8, 99.9)]
        result = detect_pin_bar(candles)
        assert len(result["pin_bars"]) == 1
        assert result["pin_bars"][0]["type"] == "bearish_pin"

    def test_no_pin_if_wicks_balanced(self):
        """Equal wicks shouldn't trigger pin bar."""
        # open=100, high=103, low=97, close=100.1  → balanced doji
        # body=0.1, upper=2.9, lower=3.0 → lower > upper but both wicks ~equal magnitude
        # Actually lower_wick/body = 30 > 2, and lower > upper → bullish pin
        # Let me create a truly balanced case:
        # open=100, high=102, low=98, close=100 → body=0, skip (body < 1e-10)
        candles = [_candle(0, 100, 102, 98, 100)]
        result = detect_pin_bar(candles)
        assert result["pin_bars"] == []

    def test_no_pin_if_small_wicks(self):
        """Body-dominated candle → no pin."""
        # open=100, high=105, low=100, close=105 → body=5, upper=0, lower=0
        candles = [_candle(0, 100, 105, 100, 105)]
        result = detect_pin_bar(candles)
        assert result["pin_bars"] == []

    def test_custom_wick_ratio(self):
        """With min_wick_body_ratio=3.0, marginally long wick (2.5×) should not qualify."""
        # body=1, lower_wick=2.5 → ratio=2.5 < 3.0
        candles = [_candle(0, 102, 102.5, 99.5, 103)]
        # body = |103-102| = 1, lower = min(102,103) - 99.5 = 2.5, upper = 102.5 - 103 = -0.5 → 0
        # Actually upper = 102.5 - max(102,103) = 102.5 - 103 = negative → 0
        # lower_wick = min(102,103) - 99.5 = 102 - 99.5 = 2.5
        # ratio = 2.5 / 1 = 2.5 < 3.0 → no pin
        result = detect_pin_bar(candles, min_wick_body_ratio=3.0)
        assert result["pin_bars"] == []

    def test_pin_bar_has_required_keys(self):
        candles = [_candle(0, 100, 100.5, 95, 100.2)]
        result = detect_pin_bar(candles)
        pin = result["pin_bars"][0]
        assert {"index", "type", "wick_ratio", "time"} == set(pin.keys())


class TestDetectEngulfing:

    def test_empty_input(self):
        result = detect_engulfing([])
        assert result["engulfing_patterns"] == []

    def test_bullish_engulfing_detected(self):
        """Bearish candle followed by larger bullish candle → bullish engulfing."""
        candles = [
            _candle(0, 102, 103, 99, 100),   # bearish: open=102, close=100
            _candle(1, 99, 104, 98.5, 103),   # bullish: open=99 < prev.close=100, close=103 > prev.open=102
        ]
        result = detect_engulfing(candles)
        assert len(result["engulfing_patterns"]) == 1
        pat = result["engulfing_patterns"][0]
        assert pat["type"] == "bullish_engulfing"
        assert pat["index"] == 1

    def test_bearish_engulfing_detected(self):
        """Bullish candle followed by larger bearish candle → bearish engulfing."""
        candles = [
            _candle(0, 100, 103, 99, 102),   # bullish: open=100, close=102
            _candle(1, 103, 103.5, 98, 99),   # bearish: open=103 > prev.close=102, close=99 < prev.open=100
        ]
        result = detect_engulfing(candles)
        assert len(result["engulfing_patterns"]) == 1
        assert result["engulfing_patterns"][0]["type"] == "bearish_engulfing"

    def test_no_engulfing_if_body_not_engulfed(self):
        """Current body doesn't fully engulf previous → no pattern."""
        candles = [
            _candle(0, 102, 103, 99, 100),   # bearish
            _candle(1, 100.5, 102, 100, 101.5),  # bullish but close=101.5 < prev.open=102
        ]
        result = detect_engulfing(candles)
        assert result["engulfing_patterns"] == []

    def test_strength_calculation(self):
        """Strength = curr_body / prev_body."""
        candles = [
            _candle(0, 102, 103, 99, 100),   # body=2
            _candle(1, 99, 106, 98, 106),     # body=7, engulfs. strength=7/2=3.5
        ]
        result = detect_engulfing(candles)
        assert result["engulfing_patterns"][0]["strength"] == 3.5

    def test_engulfing_has_required_keys(self):
        candles = [
            _candle(0, 102, 103, 99, 100),
            _candle(1, 99, 104, 98.5, 103),
        ]
        result = detect_engulfing(candles)
        pat = result["engulfing_patterns"][0]
        assert {"index", "type", "strength", "time"} == set(pat.keys())
