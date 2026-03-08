"""
tests/test_notifier.py — Tests for notifier/ (whatsapp, templates, handler).

Uses httpx mock transport to avoid real HTTP calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from notifier.whatsapp import WhatsAppNotifier
from notifier.templates import (
    format_triggered_alert,
    format_sl_plus_alert,
    format_cancelled_alert,
    format_trade_closed,
    format_daily_summary,
    format_error_alert,
)
from notifier.handler import NotificationHandler
from schemas.plan import SetupCandidate, TradingPlan
from schemas.market_data import Direction, StrategyMode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_plan() -> TradingPlan:
    """Create a minimal TradingPlan for testing."""
    setup = SetupCandidate(
        direction=Direction.SELL,
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        entry_zone_low=2348.0,
        entry_zone_high=2352.0,
        trigger_condition="sweep + reclaim",
        stop_loss=2360.0,
        sl_reasoning="above swing high",
        take_profit_1=2330.0,
        take_profit_2=2310.0,
        tp_reasoning="demand zone",
        risk_reward_ratio=2.0,
        management="SL+ at 1R",
        ttl_hours=4.0,
        invalidation="H4 close above 2365",
        confluence_score=11,
        rationale="Strong supply confluence",
    )
    return TradingPlan(
        pair="XAUUSD",
        analysis_time="2026-02-19T14:30:00Z",
        htf_bias="bearish",
        htf_bias_reasoning="H4 LH-LL structure",
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        primary_setup=setup,
        confidence=0.85,
        valid_until="2026-02-19T18:30:00Z",
    )


# ===========================================================================
# 1. Template formatting
# ===========================================================================

class TestTemplates:
    """Message templates produce correct strings."""

    def test_triggered_alert_contains_pair(self):
        plan = _make_plan()
        msg = format_triggered_alert(plan)
        assert "XAUUSD" in msg
        assert "SELL" in msg
        assert "2348.0" in msg
        assert "2360.0" in msg
        assert "11/14" in msg

    def test_triggered_alert_contains_tp2(self):
        plan = _make_plan()
        msg = format_triggered_alert(plan)
        assert "2310.0" in msg

    def test_triggered_alert_no_tp2(self):
        plan = _make_plan()
        plan.primary_setup.take_profit_2 = None
        msg = format_triggered_alert(plan)
        assert "XAUUSD" in msg  # still works without TP2

    def test_sl_plus_alert(self):
        msg = format_sl_plus_alert("EURUSD", 1.0515, 1.0488)
        assert "EURUSD" in msg
        assert "1.0515" in msg
        assert "1.0488" in msg
        assert "risk-free" in msg

    def test_cancelled_alert(self):
        msg = format_cancelled_alert("XAUUSD", "H1 CHOCH against")
        assert "CANCELLED" in msg
        assert "H1 CHOCH" in msg
        assert "30 minutes" in msg

    def test_trade_closed_win(self):
        msg = format_trade_closed(
            pair="XAUUSD", direction="sell",
            entry_price=2350.0, exit_price=2330.0,
            pips=20.0, duration_minutes=45,
            strategy_mode="sniper_confluence",
            lesson="Good entry timing",
        )
        assert "+20.0" in msg
        assert "\u2705" in msg  # ✅

    def test_trade_closed_loss(self):
        msg = format_trade_closed(
            pair="XAUUSD", direction="sell",
            entry_price=2350.0, exit_price=2360.0,
            pips=-10.0, duration_minutes=30,
            strategy_mode="sniper_confluence",
        )
        assert "-10.0" in msg
        assert "\u274C" in msg  # ❌

    def test_daily_summary(self):
        msg = format_daily_summary(
            date_str="2026-02-19",
            total_scans=15,
            setups_found=3,
            trades_taken=1,
            trade_lines=["• XAUUSD: +43 pips ✅"],
            total_pips=43.0,
            win_rate_30d=0.62,
            expectancy_30d=14.3,
        )
        assert "DAILY SUMMARY" in msg
        assert "62.0%" in msg
        assert "+14.3" in msg

    def test_error_alert(self):
        msg = format_error_alert("run_scan", "Connection timeout")
        assert "ERROR" in msg
        assert "run_scan" in msg
        assert "Connection timeout" in msg


# ===========================================================================
# 2. WhatsApp client (mocked HTTP)
# ===========================================================================

class TestWhatsAppNotifier:
    """WhatsAppNotifier makes correct HTTP requests."""

    @pytest.mark.asyncio
    async def test_send_message_payload(self):
        """send_message POSTs correct JSON to /send/message."""
        notifier = WhatsAppNotifier(
            base_url="http://test:3000",
            phone="6281234567890",
        )

        def _mock_resp():
            req = httpx.Request("POST", "http://test:3000/send/message")
            return httpx.Response(200, json={"status": "ok"}, request=req)

        mock_client = MagicMock()
        mock_client.is_closed = False

        async def mock_request(method, url, **kwargs):
            resp = _mock_resp()
            return resp

        mock_client.request = mock_request
        notifier._client = mock_client

        result = await notifier.send_message("Hello test")
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_send_image_payload(self):
        """send_image POSTs correct JSON to /send/image."""
        notifier = WhatsAppNotifier(
            base_url="http://test:3000",
            phone="6281234567890",
        )

        def _mock_resp():
            req = httpx.Request("POST", "http://test:3000/send/image")
            return httpx.Response(200, json={"status": "ok"}, request=req)

        captured_kwargs = {}

        async def mock_request(method, url, **kwargs):
            captured_kwargs.update(kwargs)
            captured_kwargs["url"] = url
            captured_kwargs["method"] = method
            return _mock_resp()

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.request = mock_request
        notifier._client = mock_client

        result = await notifier.send_image(
            "http://example.com/chart.png", "Caption", compress=True,
        )
        assert result == {"status": "ok"}
        assert "/send/image" in captured_kwargs["url"]
        payload = captured_kwargs["json"]
        assert payload["image_url"] == "http://example.com/chart.png"
        assert payload["caption"] == "Caption"
        assert payload["compress"] is True

    @pytest.mark.asyncio
    async def test_device_id_header(self):
        """If device_id is set, X-Device-Id header is sent."""
        notifier = WhatsAppNotifier(
            base_url="http://test:3000",
            phone="6281234567890",
            device_id="abc123",
        )

        def _mock_resp():
            req = httpx.Request("POST", "http://test:3000/send/message")
            return httpx.Response(200, json={"status": "ok"}, request=req)

        captured_kwargs = {}

        async def mock_request(method, url, **kwargs):
            captured_kwargs.update(kwargs)
            return _mock_resp()

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.request = mock_request
        notifier._client = mock_client

        await notifier.send_message("test")
        headers = captured_kwargs.get("headers", {})
        assert headers.get("X-Device-Id") == "abc123"


# ===========================================================================
# 3. NotificationHandler dispatch
# ===========================================================================

class TestNotificationHandler:
    """Handler dispatches events to the correct WA calls."""

    @pytest.mark.asyncio
    async def test_triggered_sends_message(self):
        """on_state_change TRIGGERED sends the alert."""
        mock_wa = AsyncMock(spec=WhatsAppNotifier)
        mock_wa.send_message = AsyncMock(return_value={"status": "ok"})
        mock_wa.send_image = AsyncMock(return_value={"status": "ok"})

        handler = NotificationHandler(notifier=mock_wa)
        plan = _make_plan()
        await handler.on_state_change(
            "APPROACHING", "TRIGGERED", plan=plan,
        )
        # Should call send_message (no ohlcv → text-only fallback)
        mock_wa.send_message.assert_called_once()
        msg = mock_wa.send_message.call_args[0][0]
        assert "XAUUSD" in msg

    @pytest.mark.asyncio
    async def test_cancelled_sends_message(self):
        """on_state_change CANCELLED sends cancel alert."""
        mock_wa = AsyncMock(spec=WhatsAppNotifier)
        mock_wa.send_message = AsyncMock(return_value={"status": "ok"})

        handler = NotificationHandler(notifier=mock_wa)
        plan = _make_plan()
        await handler.on_state_change(
            "WATCHING", "CANCELLED",
            plan=plan, cancel_reason="H1 CHOCH against",
        )
        mock_wa.send_message.assert_called_once()
        msg = mock_wa.send_message.call_args[0][0]
        assert "CANCELLED" in msg
        assert "H1 CHOCH" in msg

    @pytest.mark.asyncio
    async def test_sl_moved_sends_message(self):
        mock_wa = AsyncMock(spec=WhatsAppNotifier)
        mock_wa.send_message = AsyncMock(return_value={"status": "ok"})

        handler = NotificationHandler(notifier=mock_wa)
        await handler.on_sl_moved("XAUUSD", 2360.0, 2350.0)
        mock_wa.send_message.assert_called_once()
        msg = mock_wa.send_message.call_args[0][0]
        assert "SL MOVED" in msg

    @pytest.mark.asyncio
    async def test_error_sends_message(self):
        mock_wa = AsyncMock(spec=WhatsAppNotifier)
        mock_wa.send_message = AsyncMock(return_value={"status": "ok"})

        handler = NotificationHandler(notifier=mock_wa)
        await handler.on_error("run_scan", RuntimeError("timeout"))
        mock_wa.send_message.assert_called_once()
        msg = mock_wa.send_message.call_args[0][0]
        assert "ERROR" in msg
        assert "timeout" in msg
