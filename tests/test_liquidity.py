"""Tests for tools/liquidity.py — EQH/EQL detection and sweep detection."""

import pytest
from tools.liquidity import detect_eqh_eql, detect_sweep


def _swing(index, price):
    return {"index": index, "price": price, "type": "high", "timeframe": "H1"}


class TestDetectEqhEql:
    """Tests for equal high/low (liquidity pool) detection."""

    def test_empty_inputs(self):
        result = detect_eqh_eql([], [], atr_value=1.0)
        assert result["eqh_pools"] == []
        assert result["eql_pools"] == []

    def test_single_swing_no_pool(self):
        sh = [_swing(0, 100)]
        sl = [_swing(5, 90)]
        result = detect_eqh_eql(sh, sl, atr_value=1.0)
        assert result["eqh_pools"] == []
        assert result["eql_pools"] == []

    def test_two_equal_highs_form_pool(self):
        """Two swing highs at similar price, far enough in bars → EQH pool."""
        sh = [_swing(0, 100.0), _swing(10, 100.05)]
        result = detect_eqh_eql(sh, [], atr_value=1.0, tolerance_atr_mult=0.15)
        # Tolerance = 0.15 * 1.0 = 0.15. diff = 0.05 < 0.15 → cluster
        assert len(result["eqh_pools"]) == 1
        pool = result["eqh_pools"][0]
        assert pool["pool_type"] == "eqh"
        assert pool["swing_count"] == 2

    def test_two_equal_lows_form_pool(self):
        sl = [_swing(0, 90.0), _swing(10, 90.1)]
        result = detect_eqh_eql([], sl, atr_value=1.0, tolerance_atr_mult=0.15)
        assert len(result["eql_pools"]) == 1
        assert result["eql_pools"][0]["pool_type"] == "eql"

    def test_too_far_apart_in_price_no_pool(self):
        """Highs >tolerance apart should NOT form a pool."""
        sh = [_swing(0, 100.0), _swing(10, 101.0)]
        result = detect_eqh_eql(sh, [], atr_value=1.0, tolerance_atr_mult=0.15)
        assert result["eqh_pools"] == []

    def test_too_close_in_bars_no_pool(self):
        """Highs only 2 bars apart (< min_bars_between=5) → filtered out."""
        sh = [_swing(0, 100.0), _swing(2, 100.05)]
        result = detect_eqh_eql(sh, [], atr_value=1.0, min_bars_between=5)
        assert result["eqh_pools"] == []

    def test_three_equal_highs_higher_score(self):
        sh = [_swing(0, 100.0), _swing(10, 100.04), _swing(20, 100.08)]
        result = detect_eqh_eql(sh, [], atr_value=1.0, tolerance_atr_mult=0.15)
        assert len(result["eqh_pools"]) == 1
        assert result["eqh_pools"][0]["swing_count"] == 3
        assert result["eqh_pools"][0]["score"] == 3

    def test_pool_has_required_keys(self):
        sh = [_swing(0, 100.0), _swing(10, 100.05)]
        result = detect_eqh_eql(sh, [], atr_value=1.0)
        pool = result["eqh_pools"][0]
        required = {"pool_type", "price", "swing_count", "indices", "is_swept", "score"}
        assert required.issubset(set(pool.keys()))

    def test_invalid_atr_returns_empty(self):
        sh = [_swing(0, 100), _swing(10, 100)]
        result = detect_eqh_eql(sh, [], atr_value=0)
        assert result["eqh_pools"] == []
        assert result["eql_pools"] == []


class TestDetectSweep:
    """Tests for liquidity sweep detection."""

    def _candle(self, idx, o, h, l, c):
        return {"time": f"2025-01-0{idx+1}", "open": o, "high": h, "low": l, "close": c}

    def test_empty_inputs(self):
        result = detect_sweep([], [], atr_value=1.0)
        assert result["sweep_events"] == []

    def test_eqh_sweep_detected(self):
        """Wick above pool + buffer, close below pool → sweep of EQH."""
        pool = {"pool_type": "eqh", "price": 100.0, "swing_count": 2,
                "indices": [0, 10], "is_swept": False, "score": 2}
        # Candle: open=99, high=100.2 (above 100 + 0.05=100.05), close=99.5 (< 100)
        candles = [self._candle(0, 99.0, 100.2, 98.5, 99.5)]
        result = detect_sweep(candles, [pool], atr_value=1.0, buffer_atr_mult=0.05)
        assert len(result["sweep_events"]) == 1
        evt = result["sweep_events"][0]
        assert evt["reclaim"] is True
        assert evt["sweep_index"] == 0

    def test_eql_sweep_detected(self):
        """Wick below pool - buffer, close above pool → sweep of EQL."""
        pool = {"pool_type": "eql", "price": 90.0, "swing_count": 2,
                "indices": [0, 10], "is_swept": False, "score": 2}
        # Candle: open=91, low=89.8 (below 90 - 0.05), close=90.5 (> 90)
        candles = [self._candle(0, 91.0, 91.5, 89.8, 90.5)]
        result = detect_sweep(candles, [pool], atr_value=1.0, buffer_atr_mult=0.05)
        assert len(result["sweep_events"]) == 1

    def test_breakout_not_counted_as_sweep(self):
        """If next 2 candles close beyond pool → breakout, NOT sweep."""
        pool = {"pool_type": "eqh", "price": 100.0, "swing_count": 2,
                "indices": [0, 10], "is_swept": False, "score": 2}
        candles = [
            self._candle(0, 99.0, 100.2, 98.5, 99.5),   # initial sweep wick
            self._candle(1, 100.5, 101.0, 100.0, 100.8), # close > 100
            self._candle(2, 100.8, 101.5, 100.5, 101.0), # close > 100 again
        ]
        result = detect_sweep(candles, [pool], atr_value=1.0, buffer_atr_mult=0.05, breakout_confirm_bars=2)
        assert len(result["sweep_events"]) == 0

    def test_no_sweep_if_wick_not_beyond_buffer(self):
        """Wick that doesn't go beyond pool+buffer → no sweep."""
        pool = {"pool_type": "eqh", "price": 100.0, "swing_count": 2,
                "indices": [0, 10], "is_swept": False, "score": 2}
        candles = [self._candle(0, 99.0, 100.03, 98.5, 99.5)]  # high=100.03 < 100.05
        result = detect_sweep(candles, [pool], atr_value=1.0, buffer_atr_mult=0.05)
        assert result["sweep_events"] == []


class TestDetectEqhEqlEdgeCases:

    def test_nan_atr_returns_empty(self):
        import math
        sh = [_swing(0, 100), _swing(10, 100)]
        result = detect_eqh_eql(sh, [], atr_value=float("nan"))
        assert result["eqh_pools"] == []
