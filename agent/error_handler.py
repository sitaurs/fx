"""
agent/error_handler.py — Central error handling & crash recovery.

Handles:
    - Rate limits (429 / RESOURCE_EXHAUSTED) → exponential backoff
    - Timeouts → retry with shorter request
    - Service unavailable → fallback model
    - Network errors → retry with backoff
    - State recovery after crash (from DB)
    - Data freshness checking

Usage::

    handler = ErrorHandler()
    result = await handler.with_retry(some_async_fn, max_retries=3)

    recovery = StateRecovery(repository)
    await recovery.recover()

Reference: masterplan.md §24 (Error Handling & Recovery)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error Categories
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVICE_DOWN = "service_down"
    INVALID_REQUEST = "invalid_request"
    NETWORK = "network"
    BROKER = "broker"
    UNKNOWN = "unknown"


_ERROR_MAPPING: dict[str, ErrorCategory] = {
    "RESOURCE_EXHAUSTED": ErrorCategory.RATE_LIMIT,
    "DEADLINE_EXCEEDED": ErrorCategory.TIMEOUT,
    "UNAVAILABLE": ErrorCategory.SERVICE_DOWN,
    "INTERNAL": ErrorCategory.SERVICE_DOWN,
    "INVALID_ARGUMENT": ErrorCategory.INVALID_REQUEST,
    "PERMISSION_DENIED": ErrorCategory.INVALID_REQUEST,
}

# HTTP status → category
_HTTP_MAPPING: dict[int, ErrorCategory] = {
    429: ErrorCategory.RATE_LIMIT,
    408: ErrorCategory.TIMEOUT,
    503: ErrorCategory.SERVICE_DOWN,
    502: ErrorCategory.SERVICE_DOWN,
    500: ErrorCategory.SERVICE_DOWN,
    400: ErrorCategory.INVALID_REQUEST,
    403: ErrorCategory.INVALID_REQUEST,
}


# ---------------------------------------------------------------------------
# ErrorHandler
# ---------------------------------------------------------------------------

class ErrorHandler:
    """Centralized error handling with retry & backoff strategies.

    Classifies errors, applies appropriate retry strategy,
    and escalates when retries are exhausted.
    """

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._error_counts: dict[ErrorCategory, int] = {}
        # M-36: Track when error counts were last reset for time-window stats.
        self._last_reset: float = time.time()

    def classify(self, exc: Exception) -> ErrorCategory:
        """Classify an exception into an ErrorCategory."""
        exc_str = str(exc).upper()

        # Check gRPC / Gemini error codes
        for code, cat in _ERROR_MAPPING.items():
            if code in exc_str:
                return cat

        # H-17: Check for HTTP status codes using regex word boundary
        # to avoid false positives like "1429" matching 429.
        exc_plain = str(exc)
        for status, cat in _HTTP_MAPPING.items():
            if re.search(r'\b' + str(status) + r'\b', exc_plain):
                return cat

        # Check common network errors
        err_type = type(exc).__name__
        if err_type in ("ConnectError", "ReadTimeout", "ConnectTimeout", "ConnectionError"):
            return ErrorCategory.NETWORK
        if err_type in ("TimeoutError", "asyncio.TimeoutError"):
            return ErrorCategory.TIMEOUT

        return ErrorCategory.UNKNOWN

    def _backoff_delay(self, attempt: int, category: ErrorCategory) -> float:
        """Calculate exponential backoff delay.

        Rate limits get longer delays. Normal errors use standard backoff.
        """
        if category == ErrorCategory.RATE_LIMIT:
            # Longer backoff for rate limits: 2s, 8s, 32s
            return self.base_delay * (4 ** attempt)
        # Standard: 1s, 2s, 4s
        return self.base_delay * (2 ** attempt)

    def is_retryable(self, category: ErrorCategory) -> bool:
        """Check if an error category is retryable."""
        return category in (
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.TIMEOUT,
            ErrorCategory.SERVICE_DOWN,
            ErrorCategory.NETWORK,
        )

    async def with_retry(
        self,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        max_retries: int | None = None,
        on_retry: Callable[[int, Exception, ErrorCategory], None] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Execute an async function with automatic retry on recoverable errors.

        Args:
            fn: Async function to call.
            *args: Positional args for fn.
            max_retries: Override default max retries.
            on_retry: Optional callback(attempt, exc, category) on each retry.
            **kwargs: Keyword args for fn.

        Returns:
            The result of fn(*args, **kwargs).

        Raises:
            The last exception if all retries fail or error is non-retryable.
        """
        retries = max_retries if max_retries is not None else self.max_retries
        last_exc: Exception | None = None

        for attempt in range(retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                category = self.classify(exc)
                self._error_counts[category] = (
                    self._error_counts.get(category, 0) + 1
                )

                if not self.is_retryable(category) or attempt >= retries:
                    logger.error(
                        "Non-retryable or max retries (%d/%d): %s [%s]",
                        attempt + 1,
                        retries + 1,
                        exc,
                        category.value,
                    )
                    raise

                delay = self._backoff_delay(attempt, category)
                logger.warning(
                    "Retryable error (%s), attempt %d/%d, "
                    "backoff %.1fs: %s",
                    category.value,
                    attempt + 1,
                    retries + 1,
                    delay,
                    exc,
                )

                if on_retry:
                    on_retry(attempt, exc, category)

                await asyncio.sleep(delay)

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc

    @property
    def error_stats(self) -> dict[str, int]:
        """Return error count by category."""
        return {k.value: v for k, v in self._error_counts.items()}

    def reset_error_counts(self) -> None:
        """Reset error counts and record the reset time (M-36)."""
        self._error_counts.clear()
        self._last_reset = time.time()

    @property
    def stats_window_seconds(self) -> float:
        """Seconds since last error-count reset (M-36)."""
        return time.time() - self._last_reset


# ---------------------------------------------------------------------------
# StateRecovery — crash recovery from database
# ---------------------------------------------------------------------------

class StateRecovery:
    """Recover agent state after a crash using persisted data.

    Loads active analyses from the database and determines
    which can be resumed vs. which must be cancelled.

    .. note:: D-15 / TODO: Not yet integrated into startup flow.
       Planned for future integration in main.py startup sequence.
    """

    def __init__(self, repository: Any):
        """Args:
            repository: database.repository.Repository instance.
        """
        self._repo = repository

    async def recover(self) -> dict:
        """Recover all active analyses.

        Returns::

            {
                "recovered": [session_id, ...],
                "cancelled": [{"session_id": ..., "reason": ...}, ...],
                "trades_recovered": [trade_id, ...],
            }
        """
        result = {
            "recovered": [],
            "cancelled": [],
            "trades_recovered": [],
        }

        # 1. Load active analysis sessions
        active = await self._repo.active_sessions()
        logger.info("Recovery: found %d active sessions", len(active))

        for session in active:
            recovery = self._evaluate_session(session)
            if recovery["action"] == "resume":
                result["recovered"].append(session.session_id)
                logger.info(
                    "Recovered session %s (state=%s, pair=%s)",
                    session.session_id,
                    session.state,
                    session.pair,
                )
            else:
                session.state = "CANCELLED"
                session.cancel_reason = recovery["reason"]
                await self._repo.save_session(session)
                result["cancelled"].append(
                    {
                        "session_id": session.session_id,
                        "reason": recovery["reason"],
                    }
                )
                logger.info(
                    "Cancelled session %s: %s",
                    session.session_id,
                    recovery["reason"],
                )

        # 2. Check for trades that were ACTIVE during crash
        open_trades = await self._repo.list_trades(limit=100)
        for trade in open_trades:
            if trade.result is None and trade.exit_price is None:
                result["trades_recovered"].append(trade.trade_id)
                logger.info(
                    "Found open trade %s (pair=%s) — needs monitoring",
                    trade.trade_id,
                    trade.pair,
                )

        logger.info(
            "Recovery complete: %d recovered, %d cancelled, %d open trades",
            len(result["recovered"]),
            len(result["cancelled"]),
            len(result["trades_recovered"]),
        )
        return result

    # M-35: Removed redundant ``from datetime import timezone as tz``
    # that previously shadowed the module-level import.
    def _evaluate_session(self, session: Any) -> dict:
        """Evaluate if a session can be resumed.

        Rules:
            - TTL expired during downtime → CANCEL
            - TRIGGERED and too much time passed → CANCEL (missed entry)
            - WATCHING/APPROACHING → can resume if < 2 hours stale
            - SCANNING → always resume
        """
        now = datetime.now(timezone.utc)
        updated = session.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)

        stale_time = (now - updated).total_seconds()

        # L-59: Use string constants matching AnalysisState enum values.
        # Full import avoided to prevent circular dependency; values
        # mirror agent.state_machine.AnalysisState.
        state = session.state

        if state == "SCANNING":
            return {"action": "resume"}

        if state == "TRIGGERED":
            # If more than 30 min since trigger → missed entry
            if stale_time > 30 * 60:
                return {
                    "action": "cancel",
                    "reason": f"Missed entry — {stale_time/60:.0f}m since trigger",
                }
            return {"action": "resume"}

        if state in ("WATCHING", "APPROACHING"):
            # Stale if > 2 hours since last update
            if stale_time > 2 * 3600:
                return {
                    "action": "cancel",
                    "reason": f"Session stale — {stale_time/3600:.1f}h since update",
                }
            return {"action": "resume"}

        if state == "ACTIVE":
            # Active trade — always try to recover
            return {"action": "resume"}

        return {"action": "cancel", "reason": f"Unknown state: {state}"}


# ---------------------------------------------------------------------------
# DataFreshnessChecker
# ---------------------------------------------------------------------------

class DataFreshnessChecker:
    """Check if cached OHLCV data is fresh enough for the timeframe.

    Staleness threshold = 1.5× max age for the timeframe.

    .. note:: D-16 / TODO: Not yet integrated into data/fetcher.py.
       Planned for future integration so that stale cache data triggers
       a re-fetch instead of being silently used.
    """

    MAX_AGE_SECONDS: dict[str, int] = {
        "H4": 4 * 3600,
        "H1": 1 * 3600,
        "M30": 30 * 60,
        "M15": 15 * 60,
        "M5": 5 * 60,
        "M1": 1 * 60,
    }

    def __init__(self):
        self._cache_timestamps: dict[str, float] = {}

    def record_fetch(self, pair: str, timeframe: str) -> None:
        """Record that data was fetched now."""
        key = f"{pair}:{timeframe}"
        self._cache_timestamps[key] = time.time()

    def is_stale(self, pair: str, timeframe: str) -> bool:
        """Check if data for pair+timeframe is stale.

        Returns True if data age exceeds 1.5× the timeframe's max age,
        or if no data has been fetched.
        """
        key = f"{pair}:{timeframe}"
        ts = self._cache_timestamps.get(key)
        if ts is None:
            return True

        max_age = self.MAX_AGE_SECONDS.get(timeframe, 3600)
        stale_threshold = max_age * 1.5
        age = time.time() - ts
        return age > stale_threshold

    def age_seconds(self, pair: str, timeframe: str) -> float:
        """Return how old the cached data is (seconds). -1 if no data."""
        key = f"{pair}:{timeframe}"
        ts = self._cache_timestamps.get(key)
        if ts is None:
            return -1
        return time.time() - ts
