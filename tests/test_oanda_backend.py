"""
tests/test_oanda_backend.py — Unit tests for OandaBackend.

Tests the OandaBackend class in isolation using mocked HTTP responses,
plus integration tests against the live OANDA practice API.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from data.fetcher import (
    OandaBackend,
    DataBackend,
    OANDA_GRANULARITY,
    OANDA_INSTRUMENTS,
    _init_default_backend,
    DemoBackend,
)

# FinnhubBackend removed — OANDA is sole provider
# Tests referencing FinnhubBackend are skipped below


# =========================================================================
# Sample OANDA API responses (from real API test on 2026-02-22)
# =========================================================================
SAMPLE_CANDLES_RESPONSE = {
    "instrument": "EUR_USD",
    "granularity": "H1",
    "candles": [
        {
            "complete": True,
            "volume": 19583,
            "time": "2026-02-12T16:00:00.000000000Z",
            "mid": {"o": "1.18860", "h": "1.18900", "l": "1.18620", "c": "1.18705"},
        },
        {
            "complete": True,
            "volume": 15234,
            "time": "2026-02-12T17:00:00.000000000Z",
            "mid": {"o": "1.18705", "h": "1.18800", "l": "1.18650", "c": "1.18780"},
        },
        {
            "complete": False,
            "volume": 8912,
            "time": "2026-02-12T18:00:00.000000000Z",
            "mid": {"o": "1.18780", "h": "1.18850", "l": "1.18700", "c": "1.18810"},
        },
    ],
}

SAMPLE_M1_RESPONSE = {
    "instrument": "XAU_USD",
    "granularity": "M1",
    "candles": [
        {
            "complete": True,
            "volume": 342,
            "time": "2026-02-22T10:30:00.000000000Z",
            "mid": {"o": "5107.535", "h": "5108.720", "l": "5106.100", "c": "5107.535"},
        },
    ],
}


# =========================================================================
# Unit Tests — Mocked HTTP
# =========================================================================
class TestOandaBackendUnit:
    """Unit tests with mocked HTTP responses."""

    def _make_backend(self):
        """Create OandaBackend with test config (no DNS override, no probe)."""
        return OandaBackend(
            api_key="test_key_123",
            account_id="101-003-12345678-001",
            base_url="https://api-fxpractice.oanda.com",
            dns_overrides={},  # skip DNS patching in tests
            timeout_seconds=5.0,
        )

    def test_is_data_backend(self):
        """OandaBackend implements DataBackend interface."""
        backend = self._make_backend()
        assert isinstance(backend, DataBackend)

    def test_to_instrument_standard_pairs(self):
        """All 6 MVP pairs convert correctly."""
        expected = {
            "XAUUSD": "XAU_USD",
            "EURUSD": "EUR_USD",
            "GBPJPY": "GBP_JPY",
            "USDCHF": "USD_CHF",
            "USDCAD": "USD_CAD",
            "USDJPY": "USD_JPY",
        }
        for pair, instrument in expected.items():
            assert OandaBackend._to_instrument(pair) == instrument

    def test_to_instrument_case_insensitive(self):
        """Pair conversion is case-insensitive."""
        assert OandaBackend._to_instrument("eurusd") == "EUR_USD"
        assert OandaBackend._to_instrument("  XAUUSD  ") == "XAU_USD"

    def test_to_instrument_invalid(self):
        """Invalid pair format raises ValueError."""
        with pytest.raises(ValueError):
            OandaBackend._to_instrument("INVALID")

    def test_granularity_mapping(self):
        """All expected timeframes have OANDA granularity mappings."""
        for tf in ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]:
            assert tf in OANDA_GRANULARITY

    def test_fetch_ohlcv_parses_response(self):
        """fetch_ohlcv correctly converts OANDA response to internal format."""
        backend = self._make_backend()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CANDLES_RESPONSE
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client.get", return_value=mock_response):
            # Force client creation
            backend._client = MagicMock()
            backend._client.get = MagicMock(return_value=mock_response)

            candles = backend.fetch_ohlcv("EURUSD", "H1", count=3)

        assert len(candles) == 3
        first = candles[0]
        assert first["open"] == 1.1886
        assert first["high"] == 1.189
        assert first["low"] == 1.1862
        assert first["close"] == 1.18705
        assert first["volume"] == 19583.0
        assert "2026-02-12T16:00:00" in first["time"]

    def test_fetch_ohlcv_handles_empty_response(self):
        """Empty candles list returns empty list."""
        backend = self._make_backend()

        mock_response = MagicMock()
        mock_response.json.return_value = {"candles": []}
        mock_response.raise_for_status = MagicMock()

        backend._client = MagicMock()
        backend._client.get = MagicMock(return_value=mock_response)

        candles = backend.fetch_ohlcv("EURUSD", "H1", count=5)
        assert candles == []

    def test_fetch_ohlcv_handles_http_error(self):
        """HTTP error returns empty list (no exception)."""
        import httpx
        backend = self._make_backend()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_response
        )

        backend._client = MagicMock()
        backend._client.get = MagicMock(return_value=mock_response)

        candles = backend.fetch_ohlcv("EURUSD", "H1", count=5)
        assert candles == []

    def test_fetch_ohlcv_unsupported_timeframe(self):
        """Unsupported timeframe raises ValueError."""
        backend = self._make_backend()
        backend._client = MagicMock()

        with pytest.raises(ValueError, match="Unsupported timeframe"):
            backend.fetch_ohlcv("EURUSD", "M3", count=5)

    def test_ohlcv_format_compatibility(self):
        """Converted candles have exact same keys as other backends."""
        backend = self._make_backend()

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_M1_RESPONSE
        mock_response.raise_for_status = MagicMock()

        backend._client = MagicMock()
        backend._client.get = MagicMock(return_value=mock_response)

        candles = backend.fetch_ohlcv("XAUUSD", "M1", count=1)
        assert len(candles) == 1

        c = candles[0]
        # Must have exact keys expected by context_builder.py and tools
        required_keys = {"open", "high", "low", "close", "volume", "time"}
        assert set(c.keys()) == required_keys
        assert isinstance(c["open"], float)
        assert isinstance(c["high"], float)
        assert isinstance(c["low"], float)
        assert isinstance(c["close"], float)
        assert isinstance(c["volume"], float)
        assert isinstance(c["time"], str)

    def test_ohlcv_high_low_consistency(self):
        """high >= open, close and low <= open, close."""
        backend = self._make_backend()

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_CANDLES_RESPONSE
        mock_response.raise_for_status = MagicMock()

        backend._client = MagicMock()
        backend._client.get = MagicMock(return_value=mock_response)

        candles = backend.fetch_ohlcv("EURUSD", "H1", count=3)
        for c in candles:
            assert c["high"] >= c["open"], f"high < open: {c}"
            assert c["high"] >= c["close"], f"high < close: {c}"
            assert c["low"] <= c["open"], f"low > open: {c}"
            assert c["low"] <= c["close"], f"low > close: {c}"

    def test_count_capped_at_5000(self):
        """OANDA max is 5000 candles per request."""
        backend = self._make_backend()

        mock_response = MagicMock()
        mock_response.json.return_value = {"candles": []}
        mock_response.raise_for_status = MagicMock()

        backend._client = MagicMock()
        backend._client.get = MagicMock(return_value=mock_response)

        backend.fetch_ohlcv("EURUSD", "H1", count=10000)
        call_args = backend._client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        assert params["count"] == 5000

    def test_available_pairs_fallback(self):
        """available_pairs returns hardcoded list on error."""
        backend = self._make_backend()
        backend._client = MagicMock()
        backend._client.get.side_effect = Exception("Network error")

        pairs = backend.available_pairs()
        assert len(pairs) == len(OANDA_INSTRUMENTS)


# =========================================================================
# Backend Priority Tests (updated for OANDA)
# =========================================================================
class TestBackendPriorityWithOanda:
    """Test that _init_default_backend() respects the new priority chain."""

    def test_oanda_before_finnhub(self):
        """OANDA has higher priority than Finnhub when both are set."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "test_oanda_key",
            "OANDA_ACCOUNT_ID": "101-003-12345678-001",
            "FINNHUB_API_KEY": "test_finnhub_key",
            "TRADING_MODE": "demo",
        }
        with patch.dict(os.environ, env, clear=False):
            # Mock OandaBackend probe to succeed
            with patch.object(OandaBackend, "fetch_ohlcv",
                              return_value=[{"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100, "time": "2026-01-01"}]):
                backend = _init_default_backend()
        assert isinstance(backend, OandaBackend)

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    def test_oanda_fallback_to_finnhub(self):
        """If OANDA probe fails, fall back to Finnhub."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "test_oanda_key",
            "OANDA_ACCOUNT_ID": "101-003-12345678-001",
            "FINNHUB_API_KEY": "test_finnhub_key",
            "TRADING_MODE": "demo",
        }
        with patch.dict(os.environ, env, clear=False):
            # Mock OandaBackend probe to fail
            with patch.object(OandaBackend, "fetch_ohlcv",
                              side_effect=Exception("OANDA down")):
                backend = _init_default_backend()
        assert isinstance(backend, FinnhubBackend)

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    def test_no_oanda_without_key(self):
        """Without OANDA_API_KEY, skip OANDA and use Finnhub."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "",
            "OANDA_ACCOUNT_ID": "",
            "FINNHUB_API_KEY": "test_finnhub_key",
            "TRADING_MODE": "demo",
        }
        with patch.dict(os.environ, env, clear=False):
            backend = _init_default_backend()
        assert isinstance(backend, FinnhubBackend)

    def test_oanda_real_mode_ok(self):
        """TRADING_MODE=real with OANDA key should work."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "test_oanda_key",
            "OANDA_ACCOUNT_ID": "101-003-12345678-001",
            "FINNHUB_API_KEY": "",
            "TRADING_MODE": "real",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(OandaBackend, "fetch_ohlcv",
                              return_value=[{"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100, "time": "2026-01-01"}]):
                backend = _init_default_backend()
        assert isinstance(backend, OandaBackend)

    def test_all_empty_demo_mode(self):
        """All backends empty + demo mode → DemoBackend."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "",
            "OANDA_ACCOUNT_ID": "",
            "FINNHUB_API_KEY": "",
            "TRADING_MODE": "demo",
        }
        with patch.dict(os.environ, env, clear=False):
            backend = _init_default_backend()
        assert isinstance(backend, DemoBackend)

    def test_all_empty_real_mode_raises(self):
        """All backends empty + real mode → RuntimeError."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "",
            "OANDA_ACCOUNT_ID": "",
            "FINNHUB_API_KEY": "",
            "TRADING_MODE": "real",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="OANDA-only mode active"):
                _init_default_backend()


# =========================================================================
# DNS Override Tests
# =========================================================================
class TestDnsOverride:
    """Test the DNS bypass mechanism."""

    def test_instrument_map_complete(self):
        """All MVP pairs have OANDA instrument mappings."""
        from config.settings import MVP_PAIRS
        for pair in MVP_PAIRS:
            assert pair in OANDA_INSTRUMENTS, f"{pair} missing from OANDA_INSTRUMENTS"

    def test_granularity_map_covers_analysis_tfs(self):
        """All analysis timeframes have OANDA granularity mappings."""
        for tf in ["H4", "H1", "M15", "M1"]:
            assert tf in OANDA_GRANULARITY, f"{tf} missing from OANDA_GRANULARITY"
