"""
tests/test_indicators.py — Unit tests for ATR, EMA, RSI.

TEST-DRIVEN: These tests are written BEFORE the implementation.
Reference: masterplan.md §6.9 (Indicators)
"""

import pytest
import math

# Will be implemented in tools/indicators.py
from tools.indicators import compute_atr, compute_ema, compute_rsi


# ---------------------------------------------------------------------------
# Helper: build synthetic OHLCV candles
# ---------------------------------------------------------------------------
def make_candles(closes: list[float], spread: float = 1.0) -> list[dict]:
    """Create OHLCV dicts from a list of close prices.
    
    High = close + spread/2, Low = close - spread/2, Open ≈ previous close.
    """
    candles = []
    for i, c in enumerate(closes):
        candles.append({
            "time": f"2026-02-{i + 1:02d}T00:00:00Z",
            "open": closes[i - 1] if i > 0 else c,
            "high": c + spread / 2,
            "low": c - spread / 2,
            "close": c,
            "volume": 1000.0,
        })
    return candles


# ===== ATR Tests =====

class TestComputeATR:
    """Tests for compute_atr()."""

    def test_returns_dict_with_required_keys(self):
        candles = make_candles([100.0] * 20, spread=2.0)
        result = compute_atr(candles, period=14)
        assert "period" in result
        assert "values" in result
        assert "current" in result

    def test_period_matches_input(self):
        candles = make_candles([100.0] * 20, spread=2.0)
        result = compute_atr(candles, period=14)
        assert result["period"] == 14

    def test_values_length_matches_candles(self):
        candles = make_candles([100.0] * 30, spread=2.0)
        result = compute_atr(candles, period=14)
        assert len(result["values"]) == len(candles)

    def test_constant_range_gives_constant_atr(self):
        """If every candle has the same range, ATR should = that range."""
        candles = make_candles([100.0] * 30, spread=4.0)
        # Each candle: high-low = 4.0, true range = max(H-L, ...) = 4.0
        result = compute_atr(candles, period=14)
        # After warm-up, ATR should be ≈ 4.0
        assert abs(result["current"] - 4.0) < 0.01

    def test_increasing_volatility_raises_atr(self):
        """ATR should increase when candle ranges grow."""
        # 15 candles with range=2, then 15 with range=10
        small = make_candles([100.0] * 15, spread=2.0)
        big = make_candles([100.0] * 15, spread=10.0)
        # Adjust times to not overlap
        for i, c in enumerate(big):
            c["time"] = f"2026-02-{16 + i:02d}T00:00:00Z"
        candles = small + big
        result = compute_atr(candles, period=14)
        assert result["current"] > 2.0  # should be moving toward 10

    def test_minimum_candles_needed(self):
        """If fewer candles than period, should still return (with NaN padding)."""
        candles = make_candles([100.0] * 5, spread=2.0)
        result = compute_atr(candles, period=14)
        assert len(result["values"]) == 5
        # current may be NaN or a partial value
        assert isinstance(result["current"], float)

    def test_atr_always_positive(self):
        candles = make_candles([100 + i for i in range(30)], spread=3.0)
        result = compute_atr(candles, period=14)
        for v in result["values"]:
            if not math.isnan(v):
                assert v > 0


# ===== EMA Tests =====

class TestComputeEMA:
    """Tests for compute_ema()."""

    def test_returns_dict_with_required_keys(self):
        candles = make_candles([100.0] * 30, spread=2.0)
        result = compute_ema(candles, period=20)
        assert "period" in result
        assert "values" in result
        assert "current" in result

    def test_constant_price_ema_equals_price(self):
        """If price is constant, EMA should converge to that price."""
        candles = make_candles([50.0] * 40, spread=1.0)
        result = compute_ema(candles, period=20)
        assert abs(result["current"] - 50.0) < 0.01

    def test_values_length_matches_candles(self):
        candles = make_candles([100.0] * 30, spread=2.0)
        result = compute_ema(candles, period=10)
        assert len(result["values"]) == len(candles)

    def test_ema_tracks_trending_price(self):
        """In a clean uptrend, EMA should be below current price."""
        prices = [100.0 + i * 2.0 for i in range(40)]
        candles = make_candles(prices, spread=1.0)
        result = compute_ema(candles, period=20)
        assert result["current"] < prices[-1]

    def test_ema_different_periods(self):
        """Shorter EMA should react faster."""
        prices = [100.0] * 20 + [120.0] * 20
        candles = make_candles(prices, spread=1.0)
        ema_fast = compute_ema(candles, period=5)
        ema_slow = compute_ema(candles, period=20)
        # Fast EMA should be closer to 120 than slow
        assert ema_fast["current"] > ema_slow["current"]


# ===== RSI Tests =====

class TestComputeRSI:
    """Tests for compute_rsi()."""

    def test_returns_dict_with_required_keys(self):
        candles = make_candles([100.0] * 20, spread=2.0)
        result = compute_rsi(candles, period=14)
        assert "period" in result
        assert "values" in result
        assert "current" in result

    def test_constant_price_rsi_is_50(self):
        """Constant price → no gains/losses → RSI near 50 (or NaN)."""
        candles = make_candles([100.0] * 30, spread=0.0)
        result = compute_rsi(candles, period=14)
        # Constant price means 0 gain, 0 loss → RSI = 50 by convention
        assert 45.0 <= result["current"] <= 55.0 or math.isnan(result["current"])

    def test_strong_uptrend_rsi_above_70(self):
        """Steady uptrend should push RSI above 70."""
        prices = [100.0 + i * 3.0 for i in range(30)]
        candles = make_candles(prices, spread=0.5)
        result = compute_rsi(candles, period=14)
        assert result["current"] > 70.0

    def test_strong_downtrend_rsi_below_30(self):
        """Steady downtrend should push RSI below 30."""
        prices = [200.0 - i * 3.0 for i in range(30)]
        candles = make_candles(prices, spread=0.5)
        result = compute_rsi(candles, period=14)
        assert result["current"] < 30.0

    def test_rsi_bounded_0_to_100(self):
        """RSI must always be between 0 and 100."""
        prices = [100 + (i % 10) * 5 - 25 for i in range(50)]
        candles = make_candles(prices, spread=2.0)
        result = compute_rsi(candles, period=14)
        for v in result["values"]:
            if not math.isnan(v):
                assert 0.0 <= v <= 100.0

    def test_values_length_matches_candles(self):
        candles = make_candles([100.0] * 20, spread=2.0)
        result = compute_rsi(candles, period=14)
        assert len(result["values"]) == len(candles)
