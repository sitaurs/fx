"""Tests for tools/orderblock.py — ICT/SMC Order Block detection."""

import pytest
from tools.orderblock import detect_orderblocks


def _candle(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


class TestDetectOrderblocks:

    def test_empty_input(self):
        result = detect_orderblocks([], atr_value=1.0)
        assert result["bullish_obs"] == []
        assert result["bearish_obs"] == []

    def test_too_few_candles(self):
        candles = [_candle("t0", 100, 101, 99, 100.5)]
        result = detect_orderblocks(candles, atr_value=1.0)
        assert result["bullish_obs"] == []

    def test_bullish_ob_detected(self):
        """Bearish candle followed by strong bullish displacement → bullish OB."""
        candles = [
            _candle("t0", 100, 101, 99, 100),     # neutral
            _candle("t1", 100, 100.5, 98, 99),     # bearish: open=100, close=99
            _candle("t2", 99, 102, 98.5, 101.5),   # bullish disp: close(101.5) - prev_open(100) = 1.5 >= 1.0
        ]
        result = detect_orderblocks(candles, atr_value=1.0, displacement_atr_mult=1.0)
        assert len(result["bullish_obs"]) >= 1
        ob = result["bullish_obs"][0]
        assert ob["zone_type"] == "bullish_ob"
        assert ob["candle_index"] == 1  # the bearish candle
        assert ob["low"] == 98  # prev candle low
        assert ob["high"] == 100.5  # FIX F2-08: prev candle HIGH (was open)

    def test_bearish_ob_detected(self):
        """Bullish candle followed by strong bearish displacement → bearish OB."""
        candles = [
            _candle("t0", 100, 101, 99, 100),
            _candle("t1", 99, 102, 98.5, 101),     # bullish: open=99, close=101
            _candle("t2", 101, 101.5, 98, 98.5),   # bearish disp: prev_open(99) - close(98.5) = 0.5
        ]
        # Need bigger displacement: prev_open(99) - close(98.5) = 0.5 < 1.0
        # Adjust: make displacement bigger
        candles[2] = _candle("t2", 101, 101.5, 97, 97.5)  # disp: 99 - 97.5 = 1.5 >= 1.0
        # Body ratio: |97.5-101| / (101.5-97) = 3.5/4.5 = 0.78 >= 0.5 ✓
        result = detect_orderblocks(candles, atr_value=1.0, displacement_atr_mult=1.0)
        assert len(result["bearish_obs"]) >= 1
        ob = result["bearish_obs"][0]
        assert ob["zone_type"] == "bearish_ob"
        assert ob["candle_index"] == 1  # the bullish candle
        assert ob["low"] == 98.5  # FIX H-09: prev candle LOW (was open)
        assert ob["high"] == 102 # prev candle high

    def test_no_ob_without_displacement(self):
        """Small move doesn't qualify as OB."""
        candles = [
            _candle("t0", 100, 101, 99, 100),
            _candle("t1", 100, 100.5, 99.5, 99.8),  # small bearish
            _candle("t2", 99.8, 100.2, 99.7, 100.1), # small bullish, disp = 0.1 < 1.0
        ]
        result = detect_orderblocks(candles, atr_value=1.0, displacement_atr_mult=1.0)
        assert result["bullish_obs"] == []
        assert result["bearish_obs"] == []

    def test_ob_has_required_keys(self):
        candles = [
            _candle("t0", 100, 101, 99, 100),
            _candle("t1", 100, 100.5, 98, 99),
            _candle("t2", 99, 102, 98.5, 101.5),
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        ob = result["bullish_obs"][0]
        required = {"zone_type", "high", "low", "candle_index", "displacement_bos",
                     "is_mitigated", "score", "origin_time"}
        assert required.issubset(set(ob.keys()))

    def test_invalid_atr_returns_empty(self):
        candles = [_candle("t0", 100, 101, 99, 100)] * 5
        result = detect_orderblocks(candles, atr_value=0)
        assert result["bullish_obs"] == []

    def test_score_increases_with_displacement(self):
        """Stronger displacement → higher score."""
        candles_small = [
            _candle("t0", 100, 101, 99, 100),
            _candle("t1", 100, 100.5, 98, 99),
            _candle("t2", 99, 102, 98.5, 101.2),  # disp = 101.2 - 100 = 1.2
        ]
        candles_big = [
            _candle("t0", 100, 101, 99, 100),
            _candle("t1", 100, 100.5, 98, 99),
            _candle("t2", 99, 105, 98.5, 103.0),  # disp = 103.0 - 100 = 3.0
        ]
        r1 = detect_orderblocks(candles_small, atr_value=1.0)
        r2 = detect_orderblocks(candles_big, atr_value=1.0)
        assert r2["bullish_obs"][0]["score"] > r1["bullish_obs"][0]["score"]
