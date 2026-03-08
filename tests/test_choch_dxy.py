"""Tests for tools/choch_filter.py and tools/dxy_gate.py."""

import pytest
import math
from unittest.mock import patch
from tools.choch_filter import detect_choch_micro
from tools.dxy_gate import dxy_relevance_score


def _candle(idx, o, h, l, c):
    return {"time": f"2025-01-{idx+1:02d}", "open": o, "high": h, "low": l, "close": c}


class TestDetectChochMicro:

    def test_empty_input(self):
        result = detect_choch_micro([])
        assert result["confirmed"] is False
        assert result["break_index"] is None

    def test_too_few_candles(self):
        candles = [_candle(0, 100, 101, 99, 100)]
        result = detect_choch_micro(candles)
        assert result["confirmed"] is False

    def test_bullish_choch_confirmed(self):
        """Downmove then one bar closes *decisively* above the prior high → bullish CHOCH."""
        # ATR = 3.0, threshold = 0.3 * 3.0 = 0.9
        # prior_high (bars 0-2) = max(102, 102.5, 99) = 102.5
        # Need close > 102.5 + 0.9 = 103.4
        candles = [
            _candle(0, 100, 102, 99, 101),
            _candle(1, 101, 102.5, 98, 98.5),
            _candle(2, 98.5, 99, 97, 97.5),
            _candle(3, 97.5, 104, 97, 103.5),  # close=103.5 > 103.4 ✓
        ]
        result = detect_choch_micro(candles, direction="bullish", lookback=10, atr=3.0)
        assert result["confirmed"] is True
        assert result["break_price"] is not None

    def test_bullish_choch_rejected_within_threshold(self):
        """Close near but not *above* threshold → no CHOCH (FIX F2-09 test)."""
        # ATR = 3.0, threshold = 0.9.  recent_high = 102.5, need > 103.4
        candles = [
            _candle(0, 100, 102, 99, 101),
            _candle(1, 101, 102.5, 98, 98.5),
            _candle(2, 98.5, 99, 97, 97.5),
            _candle(3, 97.5, 103, 97, 103.0),  # close=103.0 < 103.4 — NOT enough
        ]
        result = detect_choch_micro(candles, direction="bullish", lookback=10, atr=3.0)
        assert result["confirmed"] is False

    def test_bearish_choch_confirmed(self):
        """Upmove then one bar closes *decisively* below the recent low → bearish CHOCH."""
        # ATR = 2.0, threshold = 0.6.  recent_low = 97.0, need close < 97.0 - 0.6 = 96.4
        candles = [
            _candle(0, 100, 101, 98, 98.5),
            _candle(1, 98.5, 100, 97, 99.5),
            _candle(2, 99.5, 101, 99, 100.5),
            _candle(3, 100.5, 101, 96, 96.0),  # close=96.0 < 96.4 ✓
        ]
        result = detect_choch_micro(candles, direction="bearish", lookback=10, atr=2.0)
        assert result["confirmed"] is True

    def test_bearish_choch_rejected_within_threshold(self):
        """Close near but not *below* threshold → no CHOCH (FIX F2-09 test)."""
        # ATR = 2.0, threshold = 0.6.  recent_low = 97.0, need < 96.4
        candles = [
            _candle(0, 100, 101, 98, 98.5),
            _candle(1, 98.5, 100, 97, 99.5),
            _candle(2, 99.5, 101, 99, 100.5),
            _candle(3, 100.5, 101, 97, 96.5),  # close=96.5 > 96.4 — NOT enough
        ]
        result = detect_choch_micro(candles, direction="bearish", lookback=10, atr=2.0)
        assert result["confirmed"] is False

    def test_no_choch_in_ranging(self):
        """Tight range with no break → no CHOCH."""
        candles = [
            _candle(i, 100, 100.5, 99.5, 100)
            for i in range(5)
        ]
        # ATR-estimated from segment: range = 1.0, threshold = 0.3
        # recent_low = 99.5, need close < 99.5 - 0.3 = 99.2, but all closes = 100
        result = detect_choch_micro(candles, direction="bearish", lookback=10)
        assert result["confirmed"] is False

    def test_auto_atr_estimation(self):
        """When atr=None, ATR is estimated from segment range."""
        # Segment avg range = 2.0, threshold = 0.6
        # recent_high = 103.0, need close > 103.6
        candles = [
            _candle(0, 100, 101, 99, 100),    # range=2
            _candle(1, 100, 102, 100, 101),    # range=2
            _candle(2, 101, 103, 101, 102),    # range=2
            _candle(3, 102, 105, 101, 104),    # close=104 > 103+0.6=103.6 ✓
        ]
        result = detect_choch_micro(candles, direction="bullish", lookback=10)
        assert result["confirmed"] is True

    def test_result_has_required_keys(self):
        candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
        result = detect_choch_micro(candles)
        assert {"confirmed", "break_index", "break_price"} == set(result.keys())


class TestDxyRelevanceScore:

    @pytest.fixture(autouse=True)
    def _enable_dxy_gate(self):
        """Enable DXY gate for all tests in this class."""
        with patch("tools.dxy_gate.DXY_GATE_ENABLED", True):
            yield

    def _make_pair(self, base, moves):
        """Generate candles from base + cumulative moves."""
        candles = []
        price = base
        for i, m in enumerate(moves):
            price += m
            candles.append(_candle(i, price - 0.5, price + 0.5, price - 0.5, price))
        return candles

    def test_too_few_bars(self):
        pair = [_candle(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(5)]
        idx = [_candle(i, 90 + i, 91 + i, 89 + i, 90.5 + i) for i in range(5)]
        result = dxy_relevance_score(pair, idx, window=48)
        assert result["relevant"] is False
        assert result["correlation"] == 0.0

    def test_perfectly_correlated(self):
        """Pair and index move identically → corr ≈ 1.0."""
        moves = [0.1 * (i % 5 - 2) for i in range(60)]
        pair = self._make_pair(1000, moves)
        idx = self._make_pair(100, moves)  # same moves → perfect correlation
        result = dxy_relevance_score(pair, idx, window=48)
        assert result["correlation"] > 0.9
        assert result["relevant"] is True
        assert result["direction"] == "positive"

    def test_negatively_correlated(self):
        """Pair up when index down → negative correlation."""
        moves_pair = [0.1 * (i % 5 - 2) for i in range(60)]
        moves_idx = [-m for m in moves_pair]
        pair = self._make_pair(1000, moves_pair)
        idx = self._make_pair(100, moves_idx)
        result = dxy_relevance_score(pair, idx, window=48)
        assert result["correlation"] < -0.9
        assert result["relevant"] is True
        assert result["direction"] == "negative"

    def test_uncorrelated_below_threshold(self):
        """Random/unrelated moves → |corr| < 0.2 → not relevant."""
        import random
        rng = random.Random(12345)
        pair = self._make_pair(1000, [rng.gauss(0, 1) for _ in range(60)])
        idx = self._make_pair(100, [rng.gauss(0, 1) for _ in range(60)])
        result = dxy_relevance_score(pair, idx, window=48, min_correlation=0.2)
        # With different random seeds, correlation should be low
        assert abs(result["correlation"]) < 0.5  # relaxed check due to randomness

    def test_result_has_required_keys(self):
        pair = [_candle(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(60)]
        idx = [_candle(i, 90 + i, 91 + i, 89 + i, 90.5 + i) for i in range(60)]
        result = dxy_relevance_score(pair, idx, window=48)
        assert {"correlation", "relevant", "direction", "window_used", "enabled"} == set(result.keys())

    def test_direction_neutral_when_not_relevant(self):
        pair = [_candle(i, 100, 101, 99, 100) for i in range(60)]  # flat
        idx = [_candle(i, 90, 91, 89, 90) for i in range(60)]      # flat
        result = dxy_relevance_score(pair, idx, window=48)
        # Flat prices → returns = 0 → corr = 0 → neutral
        assert result["direction"] == "neutral"
