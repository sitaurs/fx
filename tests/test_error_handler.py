"""
tests/test_error_handler.py — Tests for ErrorHandler, StateRecovery, DataFreshnessChecker.

Validates retry logic, error classification, crash recovery, and data staleness.
"""

from __future__ import annotations

import asyncio
import time
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from agent.error_handler import (
    ErrorCategory,
    ErrorHandler,
    StateRecovery,
    DataFreshnessChecker,
)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def test_rate_limit_429(self):
        handler = ErrorHandler()
        exc = Exception("Error 429: Rate limit exceeded")
        assert handler.classify(exc) == ErrorCategory.RATE_LIMIT

    def test_rate_limit_resource_exhausted(self):
        handler = ErrorHandler()
        exc = Exception("RESOURCE_EXHAUSTED: quota exceeded")
        assert handler.classify(exc) == ErrorCategory.RATE_LIMIT

    def test_timeout(self):
        handler = ErrorHandler()
        exc = Exception("DEADLINE_EXCEEDED: request timed out")
        assert handler.classify(exc) == ErrorCategory.TIMEOUT

    def test_service_unavailable_503(self):
        handler = ErrorHandler()
        exc = Exception("HTTP 503 Service Unavailable")
        assert handler.classify(exc) == ErrorCategory.SERVICE_DOWN

    def test_service_unavailable_grpc(self):
        handler = ErrorHandler()
        exc = Exception("UNAVAILABLE: backend not ready")
        assert handler.classify(exc) == ErrorCategory.SERVICE_DOWN

    def test_invalid_request_400(self):
        handler = ErrorHandler()
        exc = Exception("HTTP 400 Bad Request")
        assert handler.classify(exc) == ErrorCategory.INVALID_REQUEST

    def test_network_error(self):
        handler = ErrorHandler()

        class ConnectError(Exception):
            pass

        exc = ConnectError("Connection refused")
        assert handler.classify(exc) == ErrorCategory.NETWORK

    def test_unknown_error(self):
        handler = ErrorHandler()
        exc = ValueError("some random error")
        assert handler.classify(exc) == ErrorCategory.UNKNOWN


class TestRetryability:
    def test_retryable_categories(self):
        handler = ErrorHandler()
        assert handler.is_retryable(ErrorCategory.RATE_LIMIT) is True
        assert handler.is_retryable(ErrorCategory.TIMEOUT) is True
        assert handler.is_retryable(ErrorCategory.SERVICE_DOWN) is True
        assert handler.is_retryable(ErrorCategory.NETWORK) is True

    def test_non_retryable_categories(self):
        handler = ErrorHandler()
        assert handler.is_retryable(ErrorCategory.INVALID_REQUEST) is False
        assert handler.is_retryable(ErrorCategory.UNKNOWN) is False


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_successful_call_no_retry(self):
        handler = ErrorHandler()
        fn = AsyncMock(return_value="ok")
        result = await handler.with_retry(fn)
        assert result == "ok"
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self):
        handler = ErrorHandler(max_retries=2, base_delay=0.01)
        fn = AsyncMock(
            side_effect=[
                Exception("UNAVAILABLE: try again"),
                "success",
            ]
        )
        result = await handler.with_retry(fn)
        assert result == "success"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        handler = ErrorHandler(max_retries=2, base_delay=0.01)
        fn = AsyncMock(
            side_effect=Exception("UNAVAILABLE: always failing")
        )
        with pytest.raises(Exception, match="UNAVAILABLE"):
            await handler.with_retry(fn)
        assert fn.call_count == 3  # 1 original + 2 retries

    @pytest.mark.asyncio
    async def test_non_retryable_fails_immediately(self):
        handler = ErrorHandler(max_retries=3, base_delay=0.01)
        fn = AsyncMock(side_effect=Exception("HTTP 400 Bad Request"))
        with pytest.raises(Exception, match="400"):
            await handler.with_retry(fn)
        assert fn.call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        handler = ErrorHandler(max_retries=2, base_delay=0.01)
        callbacks = []
        fn = AsyncMock(
            side_effect=[
                Exception("UNAVAILABLE"),
                "ok",
            ]
        )
        await handler.with_retry(
            fn,
            on_retry=lambda attempt, exc, cat: callbacks.append(
                (attempt, cat)
            ),
        )
        assert len(callbacks) == 1
        assert callbacks[0][1] == ErrorCategory.SERVICE_DOWN

    @pytest.mark.asyncio
    async def test_error_stats(self):
        handler = ErrorHandler(max_retries=1, base_delay=0.01)
        fn = AsyncMock(
            side_effect=[
                Exception("UNAVAILABLE"),
                "ok",
            ]
        )
        await handler.with_retry(fn)
        stats = handler.error_stats
        assert stats.get("service_down", 0) >= 1


# ---------------------------------------------------------------------------
# State Recovery
# ---------------------------------------------------------------------------

class TestStateRecovery:
    @pytest.mark.asyncio
    async def test_recover_scanning_session(self):
        session = MagicMock()
        session.session_id = "S1"
        session.state = "SCANNING"
        session.pair = "EURUSD"
        session.updated_at = datetime.now(timezone.utc)

        repo = AsyncMock()
        repo.active_sessions = AsyncMock(return_value=[session])
        repo.list_trades = AsyncMock(return_value=[])

        recovery = StateRecovery(repo)
        result = await recovery.recover()
        assert "S1" in result["recovered"]

    @pytest.mark.asyncio
    async def test_cancel_stale_triggered(self):
        session = MagicMock()
        session.session_id = "S2"
        session.state = "TRIGGERED"
        session.pair = "XAUUSD"
        session.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)

        repo = AsyncMock()
        repo.active_sessions = AsyncMock(return_value=[session])
        repo.save_session = AsyncMock()
        repo.list_trades = AsyncMock(return_value=[])

        recovery = StateRecovery(repo)
        result = await recovery.recover()
        assert len(result["cancelled"]) == 1
        assert "missed" in result["cancelled"][0]["reason"].lower()

    @pytest.mark.asyncio
    async def test_cancel_stale_watching(self):
        session = MagicMock()
        session.session_id = "S3"
        session.state = "WATCHING"
        session.pair = "GBPJPY"
        session.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)

        repo = AsyncMock()
        repo.active_sessions = AsyncMock(return_value=[session])
        repo.save_session = AsyncMock()
        repo.list_trades = AsyncMock(return_value=[])

        recovery = StateRecovery(repo)
        result = await recovery.recover()
        assert len(result["cancelled"]) == 1
        assert "stale" in result["cancelled"][0]["reason"].lower()

    @pytest.mark.asyncio
    async def test_recover_active_trade(self):
        trade = MagicMock()
        trade.trade_id = "TRADE_001"
        trade.pair = "EURUSD"
        trade.result = None
        trade.exit_price = None

        repo = AsyncMock()
        repo.active_sessions = AsyncMock(return_value=[])
        repo.list_trades = AsyncMock(return_value=[trade])

        recovery = StateRecovery(repo)
        result = await recovery.recover()
        assert "TRADE_001" in result["trades_recovered"]


# ---------------------------------------------------------------------------
# Data Freshness
# ---------------------------------------------------------------------------

class TestDataFreshness:
    def test_no_data_is_stale(self):
        checker = DataFreshnessChecker()
        assert checker.is_stale("EURUSD", "M15") is True

    def test_fresh_data_not_stale(self):
        checker = DataFreshnessChecker()
        checker.record_fetch("EURUSD", "M15")
        assert checker.is_stale("EURUSD", "M15") is False

    def test_age_tracking(self):
        checker = DataFreshnessChecker()
        assert checker.age_seconds("EURUSD", "M15") == -1

        checker.record_fetch("EURUSD", "M15")
        age = checker.age_seconds("EURUSD", "M15")
        assert 0 <= age < 1.0

    def test_different_pairs_independent(self):
        checker = DataFreshnessChecker()
        checker.record_fetch("EURUSD", "M15")
        assert checker.is_stale("EURUSD", "M15") is False
        assert checker.is_stale("XAUUSD", "M15") is True
