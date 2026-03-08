"""Tests for tools/trendline.py — Ray-based trendline (Floor/Ceiling) detection."""

import pytest
from unittest.mock import patch
from tools.trendline import detect_trendlines, MAX_LINES_PER_DIRECTION


def _swing(index, price):
    return {"index": index, "price": price, "type": "low", "timeframe": "H1"}


def _make_uptrend_ohlcv(n, base=100.0, slope=0.5, margin=0.5):
    """Generate OHLCV where every candle low stays ABOVE a trendline of given slope.

    low[i] = base + slope*i + margin  (always above the line y=base+slope*i)
    """
    bars = []
    for i in range(n):
        line_y = base + slope * i
        lo = line_y + margin
        bars.append({
            "open": lo + 1, "close": lo + 0.5,
            "high": lo + 2, "low": lo, "time": i,
        })
    return bars


def _make_downtrend_ohlcv(n, base=110.0, slope=-0.5, margin=0.5):
    """Generate OHLCV where every candle high stays BELOW a trendline."""
    bars = []
    for i in range(n):
        line_y = base + slope * i
        hi = line_y - margin
        bars.append({
            "open": hi - 1, "close": hi - 0.5,
            "high": hi, "low": hi - 2, "time": i,
        })
    return bars


class TestDetectTrendlines:

    def test_empty_input(self):
        result = detect_trendlines([], [], [])
        assert result["uptrend_lines"] == []
        assert result["downtrend_lines"] == []

    def test_single_swing_no_line(self):
        sl = [_swing(0, 100)]
        result = detect_trendlines([], sl, [])
        assert result["uptrend_lines"] == []

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_uptrend_ray_detected(self):
        """Uptrend ray: 3 ascending lows, all candle lows stay above the ray."""
        sl = [_swing(0, 100.0), _swing(10, 105.0), _swing(20, 110.0)]
        ohlcv = _make_uptrend_ohlcv(30, base=100.0, slope=0.5, margin=0.3)
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        assert len(result["uptrend_lines"]) >= 1
        line = result["uptrend_lines"][0]
        assert line["direction"] == "uptrend"
        assert line["touches"] >= 2
        assert line["slope"] > 0
        # Must have ray extension fields
        assert "ray_end_index" in line
        assert "ray_end_price" in line
        assert line["ray_end_index"] == 29  # last bar index

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_downtrend_ray_detected(self):
        """Downtrend ray: 3 descending highs, all candle highs stay below."""
        sh = [_swing(0, 110.0), _swing(10, 105.0), _swing(20, 100.0)]
        ohlcv = _make_downtrend_ohlcv(30, base=110.0, slope=-0.5, margin=0.3)
        result = detect_trendlines(sh, [], ohlcv, pair="XAUUSD", min_touches=2)
        assert len(result["downtrend_lines"]) >= 1
        line = result["downtrend_lines"][0]
        assert line["direction"] == "downtrend"
        assert line["slope"] < 0
        assert line["ray_end_index"] == 29

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_required_keys_present(self):
        sl = [_swing(0, 100.0), _swing(10, 105.0), _swing(20, 110.0)]
        ohlcv = _make_uptrend_ohlcv(30, base=100.0, slope=0.5)
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        line = result["uptrend_lines"][0]
        required = {
            "anchor_1", "anchor_2", "slope", "touches",
            "touch_indices", "score", "direction",
            "ray_end_index", "ray_end_price",
        }
        assert required.issubset(set(line.keys()))

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_more_touches_higher_score(self):
        """4-touch line should score >= 3-touch line."""
        sl3 = [_swing(0, 100.0), _swing(10, 105.0), _swing(20, 110.0)]
        sl4 = [_swing(0, 100.0), _swing(10, 105.0), _swing(20, 110.0), _swing(30, 115.0)]
        ohlcv30 = _make_uptrend_ohlcv(30, base=100.0, slope=0.5)
        ohlcv40 = _make_uptrend_ohlcv(40, base=100.0, slope=0.5)
        r3 = detect_trendlines([], sl3, ohlcv30, pair="XAUUSD", min_touches=2)
        r4 = detect_trendlines([], sl4, ohlcv40, pair="XAUUSD", min_touches=2)
        assert r4["uptrend_lines"][0]["score"] >= r3["uptrend_lines"][0]["score"]


class TestRayValidation:
    """RAY must extend to the last candle — broken rays are rejected."""

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_uptrend_broken_at_end_rejected(self):
        """Uptrend valid between anchors, but the LAST candle breaks below → INVALID."""
        sl = [_swing(0, 100.0), _swing(20, 110.0)]
        ohlcv = _make_uptrend_ohlcv(30, base=100.0, slope=0.5, margin=0.5)
        # Make the last candle dip far below the trendline ray
        # At bar 29: line_y = 100 + 0.5*29 = 114.5
        ohlcv[29]["low"] = 100.0  # 14.5 points below the line → BROKEN
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        assert result["uptrend_lines"] == []

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_downtrend_broken_at_end_rejected(self):
        """Downtrend valid between anchors, but latest candle punches above → INVALID."""
        sh = [_swing(0, 110.0), _swing(20, 100.0)]
        ohlcv = _make_downtrend_ohlcv(30, base=110.0, slope=-0.5, margin=0.5)
        # At bar 29: line_y = 110 + (-0.5)*29 = 95.5
        ohlcv[29]["high"] = 120.0  # way above the line → BROKEN
        result = detect_trendlines(sh, [], ohlcv, pair="XAUUSD", min_touches=2)
        assert result["downtrend_lines"] == []

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_uptrend_broken_in_middle_rejected(self):
        """Candle at bar 15 breaks below the ray → entire ray invalid."""
        sl = [_swing(0, 100.0), _swing(10, 105.0)]
        ohlcv = _make_uptrend_ohlcv(30, base=100.0, slope=0.5, margin=0.5)
        # At bar 15: line_y = 100 + 0.5*15 = 107.5
        ohlcv[15]["low"] = 95.0  # breakout below → BROKEN
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        assert result["uptrend_lines"] == []

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_no_ohlcv_skips_validation(self):
        """When ohlcv=[] → validation is skipped (no data to check)."""
        sl = [_swing(0, 100.0), _swing(10, 105.0)]
        result = detect_trendlines([], sl, [], pair="XAUUSD", min_touches=2)
        # No ohlcv means total_bars=0 → early return []
        assert result["uptrend_lines"] == []


class TestRayExtension:
    """Verify that ray_end_index/ray_end_price correctly extend to the last bar."""

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_ray_extends_to_last_bar(self):
        sl = [_swing(0, 100.0), _swing(10, 105.0)]
        ohlcv = _make_uptrend_ohlcv(50, base=100.0, slope=0.5)
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        assert len(result["uptrend_lines"]) >= 1
        line = result["uptrend_lines"][0]
        # Ray should extend to index 49 (last bar)
        assert line["ray_end_index"] == 49
        # Expected price at bar 49: 100 + 0.5*49 = 124.5
        assert abs(line["ray_end_price"] - 124.5) < 0.01

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_ray_end_price_matches_slope(self):
        """ray_end_price must equal anchor_1.price + slope * (last_idx - anchor_1.index)."""
        sh = [_swing(0, 110.0), _swing(10, 105.0)]
        ohlcv = _make_downtrend_ohlcv(40, base=110.0, slope=-0.5)
        result = detect_trendlines(sh, [], ohlcv, pair="XAUUSD", min_touches=2)
        assert len(result["downtrend_lines"]) >= 1
        line = result["downtrend_lines"][0]
        expected_end = 110.0 + line["slope"] * (39 - 0)
        assert abs(line["ray_end_price"] - expected_end) < 0.01


class TestOutputCap:

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_max_lines_capped(self):
        """Even with many valid swings, output is capped at MAX_LINES_PER_DIRECTION."""
        sl = [_swing(i * 15, 100.0 + i * 5.0) for i in range(8)]
        ohlcv = _make_uptrend_ohlcv(130, base=100.0, slope=5.0/15.0, margin=0.3)
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        assert len(result["uptrend_lines"]) <= MAX_LINES_PER_DIRECTION

    def test_max_constant_is_2(self):
        assert MAX_LINES_PER_DIRECTION == 2


class TestSpanFilter:

    @patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0})
    def test_too_close_swings_rejected(self):
        """Swings only 3 bars apart → no valid trendline."""
        sl = [_swing(0, 100.0), _swing(3, 101.5)]
        ohlcv = _make_uptrend_ohlcv(10, base=100.0, slope=0.5)
        result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", min_touches=2)
        assert result["uptrend_lines"] == []
