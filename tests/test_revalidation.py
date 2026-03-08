"""
tests/test_revalidation.py — Tests for Gemini Flash revalidation system.
"""

import asyncio
import json
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.revalidation import RevalidationResult


class TestRevalidationSchema(unittest.TestCase):
    """Test RevalidationResult Pydantic model."""

    def test_valid_result(self):
        data = {
            "still_valid": True,
            "confidence": 0.85,
            "recommended_action": "hold",
            "structure_trend": "bullish",
            "key_observations": "H1 trend intact, no opposite CHoCH",
            "risk_factors": "None significant",
        }
        r = RevalidationResult(**data)
        self.assertTrue(r.still_valid)
        self.assertEqual(r.confidence, 0.85)
        self.assertEqual(r.recommended_action, "hold")

    def test_invalid_result(self):
        data = {
            "still_valid": False,
            "confidence": 0.92,
            "recommended_action": "close_early",
            "structure_trend": "bearish",
            "key_observations": "H1 CHoCH confirmed bearish",
            "risk_factors": "Strong opposite momentum, zone mitigated",
        }
        r = RevalidationResult(**data)
        self.assertFalse(r.still_valid)
        self.assertEqual(r.recommended_action, "close_early")

    def test_from_json(self):
        j = json.dumps({
            "still_valid": True,
            "confidence": 0.7,
            "recommended_action": "tighten_sl",
            "structure_trend": "range",
            "key_observations": "Consolidating near TP1",
            "risk_factors": "Possible liquidity grab above",
        })
        r = RevalidationResult.model_validate_json(j)
        self.assertTrue(r.still_valid)
        self.assertEqual(r.recommended_action, "tighten_sl")


class TestRevalidationPrompt(unittest.TestCase):
    """Test that REVALIDATION_PROMPT_TEMPLATE formats correctly."""

    def test_prompt_formatting(self):
        from agent.system_prompt import REVALIDATION_PROMPT_TEMPLATE

        prompt = REVALIDATION_PROMPT_TEMPLATE.format(
            pair="EURUSD",
            direction="buy",
            entry_price=1.08100,
            current_price=1.08500,
            stop_loss=1.07800,
            take_profit_1=1.09000,
            take_profit_2=1.09500,
            rr_current="1.33",
            sl_moved_to_be=True,
            trail_active=False,
            strategy_mode="sniper_confluence",
            confluence_score=11,
            market_data="H1: trend=bullish, choch_bull=False, choch_bear=False",
        )
        self.assertIn("EURUSD", prompt)
        self.assertIn("buy", prompt)
        self.assertIn("1.085", prompt)
        self.assertIn("H1: trend=bullish", prompt)
        self.assertIn("RevalidationResult", prompt)


class TestRevalidationIntegration(unittest.TestCase):
    """Test _revalidate_trade_setup with mocked Gemini."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("agent.production_lifecycle.collect_multi_tf_async")
    def test_gemini_flash_valid(self, mock_tf):
        """Gemini Flash says trade is still valid."""
        mock_tf.return_value = {
            "H1": {"structure": {"trend": "bullish"}},
            "M15": {"structure": {"trend": "bullish"}},
        }

        from agent.production_lifecycle import ProductionLifecycle

        lc = ProductionLifecycle.__new__(ProductionLifecycle)
        lc.active_revalidation_enabled = True
        lc.active_revalidation_interval_minutes = 0  # No cooldown
        lc._last_revalidation = {}
        lc._active = {}

        # Mock gemini client
        mock_gemini = MagicMock()
        flash_result = RevalidationResult(
            still_valid=True,
            confidence=0.9,
            recommended_action="hold",
            structure_trend="bullish",
            key_observations="Trend intact",
            risk_factors="None",
        )
        mock_resp = MagicMock()
        mock_resp.text = flash_result.model_dump_json()
        mock_gemini.agenerate_structured = AsyncMock(return_value=mock_resp)
        lc._gemini = mock_gemini

        # Mock trade
        trade = MagicMock()
        trade.direction = "buy"
        trade.entry_price = 1.08100
        trade.stop_loss = 1.07800
        trade.take_profit_1 = 1.09000
        trade.take_profit_2 = 1.09500
        trade.sl_moved_to_be = False
        trade.trail_active = False
        trade.strategy_mode = "sniper_confluence"
        trade.confluence_score = 11
        trade.rr_current = MagicMock(return_value=1.33)

        ok, note = self._run(lc._revalidate_trade_setup("EURUSD", trade, 1.08500))

        self.assertTrue(ok)
        self.assertIn("Flash", note)
        self.assertIn("valid=True", note)
        mock_gemini.agenerate_structured.assert_called_once()
        # Verify Flash model used (state="ACTIVE")
        call_kwargs = mock_gemini.agenerate_structured.call_args
        self.assertEqual(call_kwargs.kwargs.get("state") or call_kwargs[1].get("state", call_kwargs[0][0] if call_kwargs[0] else None), "ACTIVE")

    @patch("agent.production_lifecycle.collect_multi_tf_async")
    def test_gemini_flash_invalidates(self, mock_tf):
        """Gemini Flash says trade is invalid → should close."""
        mock_tf.return_value = {
            "H1": {"structure": {"trend": "bearish"}},
            "M15": {"structure": {"trend": "bearish"}},
        }

        from agent.production_lifecycle import ProductionLifecycle

        lc = ProductionLifecycle.__new__(ProductionLifecycle)
        lc.active_revalidation_enabled = True
        lc.active_revalidation_interval_minutes = 0
        lc._last_revalidation = {}
        lc._active = {}

        mock_gemini = MagicMock()
        flash_result = RevalidationResult(
            still_valid=False,
            confidence=0.95,
            recommended_action="close_early",
            structure_trend="bearish",
            key_observations="H1 CHoCH bearish confirmed",
            risk_factors="Strong bearish momentum against buy position",
        )
        mock_resp = MagicMock()
        mock_resp.text = flash_result.model_dump_json()
        mock_gemini.agenerate_structured = AsyncMock(return_value=mock_resp)
        lc._gemini = mock_gemini

        trade = MagicMock()
        trade.direction = "buy"
        trade.entry_price = 1.08100
        trade.stop_loss = 1.07800
        trade.take_profit_1 = 1.09000
        trade.take_profit_2 = 1.09500
        trade.sl_moved_to_be = False
        trade.trail_active = False
        trade.strategy_mode = "sniper_confluence"
        trade.confluence_score = 11
        trade.rr_current = MagicMock(return_value=-0.5)

        ok, note = self._run(lc._revalidate_trade_setup("EURUSD", trade, 1.07950))

        self.assertFalse(ok)
        self.assertIn("invalidated", note)

    @patch("agent.production_lifecycle.collect_multi_tf_async")
    def test_gemini_fails_fallback_heuristic(self, mock_tf):
        """When Gemini Flash fails, fall back to heuristic check."""
        mock_tf.return_value = {
            "H1": {"structure": {"trend": "bullish"}},
            "M15": {"structure": {"trend": "bullish"}},
        }

        from agent.production_lifecycle import ProductionLifecycle

        lc = ProductionLifecycle.__new__(ProductionLifecycle)
        lc.active_revalidation_enabled = True
        lc.active_revalidation_interval_minutes = 0
        lc._last_revalidation = {}
        lc._active = {}

        # Gemini raises exception
        mock_gemini = MagicMock()
        mock_gemini.agenerate_structured = AsyncMock(side_effect=Exception("API error"))
        lc._gemini = mock_gemini

        trade = MagicMock()
        trade.direction = "buy"
        trade.entry_price = 1.08100
        trade.stop_loss = 1.07800
        trade.take_profit_1 = 1.09000
        trade.take_profit_2 = 1.09500
        trade.sl_moved_to_be = False
        trade.trail_active = False
        trade.strategy_mode = "sniper_confluence"
        trade.confluence_score = 11
        trade.rr_current = MagicMock(return_value=0.5)

        ok, note = self._run(lc._revalidate_trade_setup("EURUSD", trade, 1.08250))

        self.assertTrue(ok)
        self.assertIn("Heuristic", note)  # Fell back to heuristic

    def test_revalidation_disabled(self):
        """When revalidation is disabled, return True immediately."""
        from agent.production_lifecycle import ProductionLifecycle

        lc = ProductionLifecycle.__new__(ProductionLifecycle)
        lc.active_revalidation_enabled = False

        trade = MagicMock()
        ok, note = self._run(lc._revalidate_trade_setup("EURUSD", trade, 1.08))

        self.assertTrue(ok)
        self.assertIn("disabled", note)


if __name__ == "__main__":
    unittest.main()
