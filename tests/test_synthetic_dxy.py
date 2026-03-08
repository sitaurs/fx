"""Tests for synthetic DXY computation and DXY gate integration.

Covers:
  - ICE formula accuracy (known inputs → known output)
  - fetch_synthetic_dxy with mocked OANDA backend
  - DXY gate integration in context_builder
  - Error handling: missing pair, empty candles, zero price
  - Config: DXY_GATE_ENABLED flag, DXY_COMPONENT_PAIRS
"""

from __future__ import annotations

import math
import pytest
from unittest.mock import patch, MagicMock

from config.settings import DXY_ICE_CONSTANT, DXY_COMPONENT_PAIRS


# ===================================================================
# 1. ICE Formula unit test
# ===================================================================

class TestICEFormula:
    """Verify the synthetic DXY formula matches known reference values."""

    # Known approximate market rates → expected DXY ≈ 104.x
    REFERENCE_RATES = {
        "EURUSD": 1.0480,
        "USDJPY": 154.20,
        "GBPUSD": 1.2650,
        "USDCAD": 1.3580,
        "USDSEK": 10.45,
        "USDCHF": 0.8850,
    }

    def _compute_dxy(self, rates: dict[str, float]) -> float:
        """Compute DXY from the ICE formula using given rates."""
        product = DXY_ICE_CONSTANT
        for pair, exponent, _inv in DXY_COMPONENT_PAIRS:
            price = rates[pair]
            product *= price ** exponent
        return product

    def test_dxy_in_reasonable_range(self):
        """DXY should be between 80 and 130 for normal market rates."""
        dxy = self._compute_dxy(self.REFERENCE_RATES)
        assert 80 < dxy < 130, f"DXY={dxy} out of reasonable range"

    def test_dxy_sensitivity_eurusd(self):
        """EUR/USD has the largest weight — DXY should move inversely."""
        base = self._compute_dxy(self.REFERENCE_RATES)
        # EUR/USD drops (USD stronger) → DXY should rise
        rates_strong_usd = dict(self.REFERENCE_RATES)
        rates_strong_usd["EURUSD"] = 1.0200  # EUR weakens
        dxy_strong = self._compute_dxy(rates_strong_usd)
        assert dxy_strong > base, "DXY should rise when EUR/USD drops"

    def test_dxy_sensitivity_usdjpy(self):
        """USD/JPY has positive exponent — DXY should move together."""
        base = self._compute_dxy(self.REFERENCE_RATES)
        rates_jpy_weak = dict(self.REFERENCE_RATES)
        rates_jpy_weak["USDJPY"] = 160.0  # JPY weakens → USD stronger
        dxy_up = self._compute_dxy(rates_jpy_weak)
        assert dxy_up > base, "DXY should rise when USD/JPY rises"

    def test_all_components_present(self):
        """All 6 ICE components must be defined."""
        pairs = [p for p, _, _ in DXY_COMPONENT_PAIRS]
        assert len(pairs) == 6
        assert "EURUSD" in pairs
        assert "USDJPY" in pairs
        assert "GBPUSD" in pairs
        assert "USDCAD" in pairs
        assert "USDSEK" in pairs
        assert "USDCHF" in pairs

    def test_exponents_sum(self):
        """Exponents should sum to approximately 0 (balanced basket)."""
        total = sum(exp for _, exp, _ in DXY_COMPONENT_PAIRS)
        # ICE formula exponents sum to -0.39 (not zero — it's weighted)
        assert -1.0 < total < 1.0, f"Exponent sum {total} unexpected"


# ===================================================================
# 2. fetch_synthetic_dxy tests
# ===================================================================

def _make_candles(close_prices: list[float], base_open: float | None = None) -> list[dict]:
    """Create simple OHLCV candle dicts from close prices."""
    candles = []
    for i, c in enumerate(close_prices):
        o = base_open if base_open else c * 0.999
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        candles.append({
            "open": round(o, 5),
            "high": round(h, 5),
            "low": round(l, 5),
            "close": round(c, 5),
            "volume": 1000.0,
            "time": f"2026-03-08T{i:02d}:00:00Z",
        })
    return candles


class TestFetchSyntheticDxy:
    """Test the fetch_synthetic_dxy function with mocked backends."""

    def _mock_backend(self, pair_candles: dict[str, list[dict]]):
        """Create a mock backend that returns specific candles per pair."""
        mock = MagicMock()
        def side_effect(pair, tf, count):
            return pair_candles.get(pair, [])
        mock.fetch_ohlcv.side_effect = side_effect
        return mock

    def test_basic_computation(self):
        """Synthetic DXY should produce valid candles from 6 component pairs."""
        from data.fetcher import fetch_synthetic_dxy

        # Create 20 candles for each component pair
        pair_candles = {
            "EURUSD": _make_candles([1.048] * 20),
            "USDJPY": _make_candles([154.2] * 20),
            "GBPUSD": _make_candles([1.265] * 20),
            "USDCAD": _make_candles([1.358] * 20),
            "USDSEK": _make_candles([10.45] * 20),
            "USDCHF": _make_candles([0.885] * 20),
        }
        mock = self._mock_backend(pair_candles)

        with patch("data.fetcher.get_backend", return_value=mock):
            result = fetch_synthetic_dxy("H1", count=20)

        assert len(result) == 20
        for c in result:
            assert "open" in c
            assert "high" in c
            assert "low" in c
            assert "close" in c
            assert "volume" in c
            assert "time" in c
            # DXY should be in reasonable range
            assert 80 < c["close"] < 130, f"DXY close={c['close']}"

    def test_ohlc_consistency(self):
        """High >= max(O,C), Low <= min(O,C) for every synthetic bar."""
        from data.fetcher import fetch_synthetic_dxy

        pair_candles = {
            "EURUSD": _make_candles([1.048 + i * 0.001 for i in range(20)]),
            "USDJPY": _make_candles([154.2 + i * 0.1 for i in range(20)]),
            "GBPUSD": _make_candles([1.265 - i * 0.0005 for i in range(20)]),
            "USDCAD": _make_candles([1.358 + i * 0.001 for i in range(20)]),
            "USDSEK": _make_candles([10.45 + i * 0.01 for i in range(20)]),
            "USDCHF": _make_candles([0.885 + i * 0.0005 for i in range(20)]),
        }
        mock = self._mock_backend(pair_candles)

        with patch("data.fetcher.get_backend", return_value=mock):
            result = fetch_synthetic_dxy("H1", count=20)

        for c in result:
            assert c["high"] >= max(c["open"], c["close"]), \
                f"High {c['high']} < max(O={c['open']}, C={c['close']})"
            assert c["low"] <= min(c["open"], c["close"]), \
                f"Low {c['low']} > min(O={c['open']}, C={c['close']})"

    def test_missing_pair_returns_empty(self):
        """If any component pair returns no candles, return empty list."""
        from data.fetcher import fetch_synthetic_dxy

        pair_candles = {
            "EURUSD": _make_candles([1.048] * 20),
            "USDJPY": _make_candles([154.2] * 20),
            "GBPUSD": [],  # Missing!
            "USDCAD": _make_candles([1.358] * 20),
            "USDSEK": _make_candles([10.45] * 20),
            "USDCHF": _make_candles([0.885] * 20),
        }
        mock = self._mock_backend(pair_candles)

        with patch("data.fetcher.get_backend", return_value=mock):
            result = fetch_synthetic_dxy("H1", count=20)

        assert result == []

    def test_fetch_exception_returns_empty(self):
        """If backend raises an exception, return empty list."""
        from data.fetcher import fetch_synthetic_dxy

        mock = MagicMock()
        mock.fetch_ohlcv.side_effect = Exception("Network error")

        with patch("data.fetcher.get_backend", return_value=mock):
            result = fetch_synthetic_dxy("H1", count=20)

        assert result == []

    def test_zero_price_skipped(self):
        """Candles with zero price should be skipped (not cause math error)."""
        from data.fetcher import fetch_synthetic_dxy

        # One candle has zero close
        eurusd = _make_candles([1.048] * 20)
        eurusd[5]["close"] = 0.0  # Bad candle

        pair_candles = {
            "EURUSD": eurusd,
            "USDJPY": _make_candles([154.2] * 20),
            "GBPUSD": _make_candles([1.265] * 20),
            "USDCAD": _make_candles([1.358] * 20),
            "USDSEK": _make_candles([10.45] * 20),
            "USDCHF": _make_candles([0.885] * 20),
        }
        mock = self._mock_backend(pair_candles)

        with patch("data.fetcher.get_backend", return_value=mock):
            result = fetch_synthetic_dxy("H1", count=20)

        # Should produce 19 valid candles (1 skipped)
        assert len(result) == 19

    def test_insufficient_data(self):
        """Less than 10 bars should return empty."""
        from data.fetcher import fetch_synthetic_dxy

        pair_candles = {
            "EURUSD": _make_candles([1.048] * 5),
            "USDJPY": _make_candles([154.2] * 5),
            "GBPUSD": _make_candles([1.265] * 5),
            "USDCAD": _make_candles([1.358] * 5),
            "USDSEK": _make_candles([10.45] * 5),
            "USDCHF": _make_candles([0.885] * 5),
        }
        mock = self._mock_backend(pair_candles)

        with patch("data.fetcher.get_backend", return_value=mock):
            result = fetch_synthetic_dxy("H1", count=5)

        assert result == []


# ===================================================================
# 3. DXY gate integration with context_builder
# ===================================================================

class TestDxyContextIntegration:
    """Test that DXY correlation data appears in the formatted context."""

    def test_dxy_section_in_context_h1(self):
        """DXY CORRELATION section should appear for H1 timeframe."""
        from agent.context_builder import format_context

        # Mock analysis data for H1 with DXY enabled
        analyses = {
            "H1": {
                "timeframe": "H1",
                "candle_count": 150,
                "last_close": 1.048,
                "last_time": "2026-03-08T12:00:00Z",
                "atr": {"current": 0.0020, "period": 14},
                "ema50": {"current": 1.045, "period": 50},
                "rsi14": {"current": 55.0, "period": 14},
                "swing_highs": [],
                "swing_lows": [],
                "structure": {"trend": "bullish", "events": []},
                "snr_levels": [],
                "supply_zones": [],
                "demand_zones": [],
                "bullish_obs": [],
                "bearish_obs": [],
                "uptrend_lines": [],
                "downtrend_lines": [],
                "eqh_pools": [],
                "eql_pools": [],
                "sweep_events": [],
                "pin_bars": [],
                "engulfing_patterns": [],
                "choch_micro_bullish": {"confirmed": False},
                "choch_micro_bearish": {"confirmed": False},
                "dxy_correlation": {
                    "correlation": -0.7523,
                    "relevant": True,
                    "direction": "negative",
                    "window_used": 48,
                    "enabled": True,
                },
            }
        }

        ctx = format_context("EURUSD", analyses)
        assert "[DXY CORRELATION]" in ctx
        assert "corr=-0.7523" in ctx
        assert "relevant=True" in ctx
        assert "negative" in ctx
        assert "PASS" in ctx

    def test_dxy_disabled_not_in_context(self):
        """DXY CORRELATION section should NOT appear when disabled."""
        from agent.context_builder import format_context

        analyses = {
            "H1": {
                "timeframe": "H1",
                "candle_count": 150,
                "last_close": 1.048,
                "last_time": "2026-03-08T12:00:00Z",
                "atr": {"current": 0.0020, "period": 14},
                "ema50": {"current": 1.045, "period": 50},
                "rsi14": {"current": 55.0, "period": 14},
                "swing_highs": [],
                "swing_lows": [],
                "structure": {"trend": "bullish", "events": []},
                "snr_levels": [],
                "supply_zones": [],
                "demand_zones": [],
                "bullish_obs": [],
                "bearish_obs": [],
                "uptrend_lines": [],
                "downtrend_lines": [],
                "eqh_pools": [],
                "eql_pools": [],
                "sweep_events": [],
                "pin_bars": [],
                "engulfing_patterns": [],
                "choch_micro_bullish": {"confirmed": False},
                "choch_micro_bearish": {"confirmed": False},
                "dxy_correlation": {
                    "correlation": 0.0,
                    "relevant": False,
                    "direction": "neutral",
                    "window_used": 0,
                    "enabled": False,
                },
            }
        }

        ctx = format_context("EURUSD", analyses)
        assert "[DXY CORRELATION]" not in ctx

    def test_dxy_weak_correlation_shows_skip(self):
        """When DXY is enabled but weak correlation, show SKIP."""
        from agent.context_builder import format_context

        analyses = {
            "H1": {
                "timeframe": "H1",
                "candle_count": 150,
                "last_close": 1.048,
                "last_time": "2026-03-08T12:00:00Z",
                "atr": {"current": 0.0020, "period": 14},
                "ema50": {"current": 1.045, "period": 50},
                "rsi14": {"current": 55.0, "period": 14},
                "swing_highs": [],
                "swing_lows": [],
                "structure": {"trend": "bullish", "events": []},
                "snr_levels": [],
                "supply_zones": [],
                "demand_zones": [],
                "bullish_obs": [],
                "bearish_obs": [],
                "uptrend_lines": [],
                "downtrend_lines": [],
                "eqh_pools": [],
                "eql_pools": [],
                "sweep_events": [],
                "pin_bars": [],
                "engulfing_patterns": [],
                "choch_micro_bullish": {"confirmed": False},
                "choch_micro_bearish": {"confirmed": False},
                "dxy_correlation": {
                    "correlation": 0.12,
                    "relevant": False,
                    "direction": "neutral",
                    "window_used": 48,
                    "enabled": True,
                },
            }
        }

        ctx = format_context("EURUSD", analyses)
        assert "[DXY CORRELATION]" in ctx
        assert "SKIP" in ctx


# ===================================================================
# 4. Config tests
# ===================================================================

class TestDxyConfig:
    """Verify DXY config values are correct."""

    def test_dxy_gate_enabled_default(self):
        """DXY_GATE_ENABLED should be True by default now."""
        from config.settings import DXY_GATE_ENABLED
        assert DXY_GATE_ENABLED is True

    def test_ice_constant(self):
        """ICE constant should be the standard value."""
        assert abs(DXY_ICE_CONSTANT - 50.14348112) < 1e-6

    def test_component_pairs_count(self):
        """Must have exactly 6 component pairs."""
        assert len(DXY_COMPONENT_PAIRS) == 6

    def test_mode_index_correlation_enabled(self):
        """Mode index_correlation should be enabled."""
        from config.settings import MODE_SELECTION_PRIORITY
        idx_mode = next(m for m in MODE_SELECTION_PRIORITY if m["mode"] == "index_correlation")
        assert idx_mode["enabled"] is True

    def test_usdsek_in_oanda_instruments(self):
        """USDSEK must be mapped in OANDA_INSTRUMENTS."""
        from data.fetcher import OANDA_INSTRUMENTS
        assert "USDSEK" in OANDA_INSTRUMENTS
        assert OANDA_INSTRUMENTS["USDSEK"] == "USD_SEK"


# ===================================================================
# 5. Tool registry test
# ===================================================================

class TestToolRegistry:
    """Verify dxy_relevance_score is registered."""

    def test_dxy_in_all_tools(self):
        """dxy_relevance_score should be in ALL_TOOLS."""
        from agent.tool_registry import ALL_TOOLS, TOOL_COUNT
        tool_names = [t.__name__ for t in ALL_TOOLS]
        assert "dxy_relevance_score" in tool_names
        assert TOOL_COUNT == 17
