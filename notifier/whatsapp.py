"""
notifier/whatsapp.py — WhatsApp REST client via go-whatsapp-web-multidevice.

Sends text messages and images through the local go-whatsapp API.
Endpoints:
  - POST ``/send/message``  → text
  - POST ``/send/image``    → image with caption

Features:
  - Connection pooling (shared httpx.AsyncClient)
  - Retry with exponential backoff (max 3 attempts)
  - Circuit breaker (open after 5 consecutive failures, half-open after 60s)

L-49 — API Key / Credential Rotation:
  Credentials are read from env vars at import time
  (``WHATSAPP_BASIC_USER``, ``WHATSAPP_BASIC_PASS``).
  To rotate:
    1. Update the env vars (or .env file).
    2. Restart the agent (``pm2 restart 3``).
  The ``httpx.AsyncClient`` re-creates its auth header on each request,
  so no in-process hot-reload is needed.

Reference: masterplan.md §22.1 – §22.2
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from config.settings import WHATSAPP_API_URL, WHATSAPP_PHONE
from config.settings import (
    WHATSAPP_DEVICE_ID,
    WHATSAPP_BASIC_USER,
    WHATSAPP_BASIC_PASS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Simple circuit breaker: CLOSED → OPEN (after *threshold* failures) → HALF_OPEN (after *recovery_timeout* seconds)."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, threshold: int = 5, recovery_timeout: float = 60.0) -> None:
        self.threshold = threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._state = self.CLOSED
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        if self._state == self.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = self.HALF_OPEN
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def record_success(self) -> None:
        """Reset to CLOSED on success."""
        self._failure_count = 0
        self._state = self.CLOSED

    def record_failure(self) -> None:
        """Increment failures; open circuit if threshold reached."""
        self._failure_count += 1
        if self._failure_count >= self.threshold:
            self._state = self.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit breaker OPEN after %d consecutive failures",
                self._failure_count,
            )

    def allow_request(self) -> bool:
        """Return True if the request should be allowed."""
        s = self.state  # triggers HALF_OPEN transition check
        if s == self.CLOSED:
            return True
        if s == self.HALF_OPEN:
            return True  # allow one probe request
        return False


# ---------------------------------------------------------------------------
# WhatsApp Notifier
# ---------------------------------------------------------------------------

class WhatsAppNotifier:
    """Async HTTP client for go-whatsapp-web-multidevice REST API.

    Features:
      - Shared ``httpx.AsyncClient`` (connection pooling)
      - Retry with exponential backoff (up to *max_retries* attempts)
      - Circuit breaker protection
    """

    def __init__(
        self,
        base_url: str | None = None,
        phone: str | None = None,
        device_id: str | None = None,
        basic_user: str | None = None,
        basic_pass: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self.base_url = (base_url or WHATSAPP_API_URL).rstrip("/")
        self.phone = phone or WHATSAPP_PHONE
        self.device_id = device_id if device_id is not None else WHATSAPP_DEVICE_ID
        self.basic_user = basic_user if basic_user is not None else WHATSAPP_BASIC_USER
        self.basic_pass = basic_pass if basic_pass is not None else WHATSAPP_BASIC_PASS
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._client: httpx.AsyncClient | None = None
        self.circuit = CircuitBreaker()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it on first call."""
        if self._client is None or self._client.is_closed:
            auth = None
            if self.basic_user and self.basic_pass:
                auth = (self.basic_user, self.basic_pass)
            self._client = httpx.AsyncClient(timeout=self.timeout, auth=auth)
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.device_id:
            h["X-Device-Id"] = self.device_id
        return h

    def _phone_jid(self) -> str:
        """Return the phone formatted as a WhatsApp JID."""
        raw = (self.phone or "").strip()
        if raw.endswith("@s.whatsapp.net"):
            return raw

        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits.startswith("0"):
            # Local Indonesia format (08xx) -> international (62xx)
            digits = f"62{digits[1:]}"
        if not digits:
            raise ValueError("WHATSAPP_PHONE is empty or invalid")
        return f"{digits}@s.whatsapp.net"

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Execute an HTTP request with retry + circuit breaker."""
        if not self.circuit.allow_request():
            raise ConnectionError(
                f"Circuit breaker OPEN — WhatsApp API unavailable "
                f"(failures={self.circuit.failure_count})"
            )

        client = self._get_client()
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.request(
                    method,
                    url,
                    json=json,
                    headers=self._headers(),
                    timeout=timeout or self.timeout,
                )
                resp.raise_for_status()
                self.circuit.record_success()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                self.circuit.record_failure()

                if attempt < self.max_retries:
                    delay = self.backoff_base * (2 ** (attempt - 1))
                    logger.warning(
                        "WA request failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt,
                        self.max_retries,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "WA request failed after %d attempts: %s",
                        self.max_retries,
                        exc,
                    )

        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def send_message(self, message: str) -> dict:
        """Send a text message.

        Returns the JSON response from the go-whatsapp API.
        Retries on failure with exponential backoff.
        """
        url = f"{self.base_url}/send/message"
        payload = {
            "phone": self._phone_jid(),
            "message": message,
        }
        logger.info("WA send_message → %s  len=%d", url, len(message))
        return await self._request_with_retry("POST", url, json=payload)

    async def send_image(
        self,
        image_url: str,
        caption: str,
        compress: bool = True,
    ) -> dict:
        """Send an image with caption.

        *image_url* can be an HTTP URL or a ``data:image/png;base64,...``
        data-URI.

        Returns the JSON response from the go-whatsapp API.
        Retries on failure with exponential backoff.
        """
        url = f"{self.base_url}/send/image"
        payload = {
            "phone": self._phone_jid(),
            "image_url": image_url,
            "caption": caption,
            "compress": compress,
        }
        logger.info("WA send_image → %s  caption_len=%d", url, len(caption))
        return await self._request_with_retry(
            "POST", url, json=payload, timeout=max(self.timeout, 60.0)
        )


# Module-level singleton (uses env vars)
wa_notifier = WhatsAppNotifier()
