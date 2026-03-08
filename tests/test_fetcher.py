"""Tests for data/fetcher.py — OandaBackend, DemoBackend, convenience funcs."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from data.fetcher import (
    DemoBackend,
    DataBackend,
    OandaBackend,
    fetch_ohlcv,
    set_backend,
    get_backend,
    OANDA_INSTRUMENTS,
)


# =========================================================================
# OANDA Instrument Conversion Tests
# =========================================================================
class TestOandaInstruments:

    def test_eurusd(self):
        assert OandaBackend._to_instrument("EURUSD") == "EUR_USD"

    def test_xauusd(self):
        assert OandaBackend._to_instrument("XAUUSD") == "XAU_USD"

    def test_gbpjpy(self):
        assert OandaBackend._to_instrument("GBPJPY") == "GBP_JPY"

    def test_cross_pair_eurgbp(self):
        assert OandaBackend._to_instrument("EURGBP") == "EUR_GBP"

    def test_all_instruments_in_lookup(self):
        for pair, expected in OANDA_INSTRUMENTS.items():
            assert OandaBackend._to_instrument(pair) == expected

    def test_lowercase_normalized(self):
        assert OandaBackend._to_instrument("eurusd") == "EUR_USD"

    def test_generic_6char_fallback(self):
        # Unknown 6-char pair still converts via generic split
        result = OandaBackend._to_instrument("ABCDEF")
        assert result == "ABC_DEF"

    def test_invalid_length_raises(self):
        with pytest.raises(ValueError, match="Cannot convert"):
            OandaBackend._to_instrument("EUR")


# =========================================================================
# DemoBackend Tests (kept from Phase 1)
# =========================================================================
class TestDemoBackend:

    def test_returns_correct_count(self):
        backend = DemoBackend(seed=42)
        candles = backend.fetch_ohlcv("XAUUSD", "H1", count=100)
        assert len(candles) == 100

    def test_candle_has_required_keys(self):
        backend = DemoBackend(seed=42)
        candles = backend.fetch_ohlcv("EURUSD", "M15", count=10)
        for c in candles:
            assert {"open", "high", "low", "close", "volume", "time"}.issubset(c.keys())

    def test_ohlc_consistency(self):
        backend = DemoBackend(seed=42)
        candles = backend.fetch_ohlcv("XAUUSD", "H4", count=50)
        for c in candles:
            assert c["high"] >= c["open"]
            assert c["high"] >= c["close"]
            assert c["low"] <= c["open"]
            assert c["low"] <= c["close"]

    def test_deterministic_with_seed(self):
        from_dt = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        b1 = DemoBackend(seed=123)
        b2 = DemoBackend(seed=123)
        c1 = b1.fetch_ohlcv("XAUUSD", "H1", 20, from_date=from_dt)
        c2 = b2.fetch_ohlcv("XAUUSD", "H1", 20, from_date=from_dt)
        assert c1 == c2

    def test_available_pairs(self):
        backend = DemoBackend()
        pairs = backend.available_pairs()
        assert "XAUUSD" in pairs
        assert "EURUSD" in pairs


# =========================================================================
# Convenience Function Tests
# =========================================================================
class TestConvenienceFunctions:

    def test_set_and_get_backend(self):
        original = get_backend()
        demo = DemoBackend(seed=99)
        set_backend(demo)
        assert get_backend() is demo
        set_backend(original)  # restore

    def test_fetch_ohlcv_returns_dict(self):
        set_backend(DemoBackend(seed=42))
        result = fetch_ohlcv("XAUUSD", "H1", count=10)
        assert isinstance(result, dict)
        assert result["pair"] == "XAUUSD"
        assert result["timeframe"] == "H1"
        assert result["count"] == 10
        assert len(result["candles"]) == 10

    def test_fetch_ohlcv_different_timeframes(self):
        set_backend(DemoBackend(seed=42))
        for tf in ["M15", "M30", "H1", "H4"]:
            result = fetch_ohlcv("XAUUSD", tf, count=5)
            assert result["count"] == 5
