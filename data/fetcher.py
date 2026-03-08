"""
data/fetcher.py — OHLCV data fetcher with pluggable backends.

Backends (priority order):
    1. OandaBackend   : OANDA v20 REST API (forex candles) — Practice/Live
    2. MT5ApiBackend  : Reads OHLCV from local MT5 Flask API (`/ohlcv`)
    3. DemoBackend    : Generates synthetic data for testing / development

OANDA v20 API:
  - Endpoint: GET /v3/instruments/{instrument}/candles
  - Auth: Bearer token in Authorization header
  - Response: {"candles": [{"mid": {"o","h","l","c"}, "volume", "time", "complete"}]}
  - Granularity: M1, M5, M15, M30, H1, H4, D, W
  - Instruments: EUR_USD, XAU_USD, GBP_JPY, USD_CHF, USD_CAD, USD_JPY

Reference: masterplan.md §4, Day 1 roadmap
"""

from __future__ import annotations

import math
import random
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load .env early — fetcher.py may be imported before config/settings.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeframe mapping  (string → bar-duration in minutes)
# ---------------------------------------------------------------------------
TF_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
}

# OANDA v20 granularity strings
OANDA_GRANULARITY: dict[str, str] = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D1": "D",
    "W1": "W",
}

# Pair → OANDA instrument conversion
OANDA_INSTRUMENTS: dict[str, str] = {
    # Metals
    "XAUUSD": "XAU_USD",
    "XAGUSD": "XAG_USD",
    # Major USD pairs
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "AUDUSD": "AUD_USD",
    "NZDUSD": "NZD_USD",
    "USDCHF": "USD_CHF",
    "USDCAD": "USD_CAD",
    "USDJPY": "USD_JPY",
    "USDSEK": "USD_SEK",
    # JPY crosses
    "GBPJPY": "GBP_JPY",
    "EURJPY": "EUR_JPY",
    "AUDJPY": "AUD_JPY",
    "NZDJPY": "NZD_JPY",
    "CADJPY": "CAD_JPY",
    "CHFJPY": "CHF_JPY",
    # Non-USD non-JPY crosses
    "EURGBP": "EUR_GBP",
    "EURAUD": "EUR_AUD",
    "EURNZD": "EUR_NZD",
    "EURCHF": "EUR_CHF",
    "EURCAD": "EUR_CAD",
    "GBPAUD": "GBP_AUD",
    "GBPNZD": "GBP_NZD",
    "GBPCHF": "GBP_CHF",
    "GBPCAD": "GBP_CAD",
    "AUDNZD": "AUD_NZD",
    "AUDCAD": "AUD_CAD",
    "AUDCHF": "AUD_CHF",
    "NZDCAD": "NZD_CAD",
    "NZDCHF": "NZD_CHF",
    "CADCHF": "CAD_CHF",
}

# =========================================================================
# Abstract base
# =========================================================================
class DataBackend(ABC):
    """Interface every data backend must implement."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        pair: str,
        timeframe: str,
        count: int = 300,
        from_date: Optional[datetime] = None,
    ) -> list[dict]:
        """Return list of candle dicts with keys: open, high, low, close, volume, time."""
        ...

    @abstractmethod
    def available_pairs(self) -> list[str]:
        """Return list of available symbol names."""
        ...


# =========================================================================
# OANDA v20 REST API Backend
# =========================================================================

import socket as _socket

# Keep original getaddrinfo for DNS override
_original_getaddrinfo = _socket.getaddrinfo
_dns_patched = False
_dns_overrides_ref: dict[str, str] = {}  # Mutable ref for live updates


def _resolve_via_doh(hostname: str) -> str | None:
    """Resolve hostname via Cloudflare DNS-over-HTTPS (FIX §7.6).

    Falls back to Google DoH if Cloudflare fails.
    Returns the first A record IP, or None on failure.
    """
    doh_urls = [
        f"https://cloudflare-dns.com/dns-query?name={hostname}&type=A",
        f"https://dns.google/resolve?name={hostname}&type=A",
    ]
    for url in doh_urls:
        try:
            resp = httpx.get(
                url,
                headers={"Accept": "application/dns-json"},
                timeout=5.0,
            )
            data = resp.json()
            answers = data.get("Answer", [])
            for ans in answers:
                if ans.get("type") == 1:  # A record
                    ip = ans["data"]
                    logger.info("DoH resolved %s → %s", hostname, ip)
                    return ip
        except Exception as exc:
            logger.debug("DoH via %s failed: %s", url.split("/")[2], exc)
    return None


def refresh_dns_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Verify and refresh DNS overrides using DoH (FIX §7.6).

    For each hostname, attempts a quick TCP connect to the cached IP.
    If unreachable, re-resolves via DoH. Returns updated mapping.
    """
    import socket
    updated = dict(overrides)
    for host, ip in list(updated.items()):
        # Quick TCP probe on port 443
        try:
            sock = socket.create_connection((ip, 443), timeout=3)
            sock.close()
            logger.debug("DNS override OK: %s → %s", host, ip)
        except OSError:
            logger.warning("DNS override stale: %s → %s (unreachable)", host, ip)
            new_ip = _resolve_via_doh(host)
            if new_ip:
                updated[host] = new_ip
                logger.info("DNS override refreshed: %s → %s (was %s)", host, new_ip, ip)
            else:
                logger.error("Cannot refresh DNS for %s — keeping stale IP", host)
    return updated


def _install_dns_overrides(overrides: dict[str, str]) -> None:
    """Monkey-patch socket.getaddrinfo to bypass ISP DNS blocking.

    Indonesian ISPs redirect OANDA domains to ``aduankonten.id``.
    This patches DNS resolution to use the real IPs (resolved via
    Cloudflare DoH) for affected hostnames.

    FIX §7.6: Uses _dns_overrides_ref as mutable reference so
    refresh_dns_overrides() can update IPs at runtime.
    """
    global _dns_patched, _dns_overrides_ref
    if _dns_patched:
        # Update the ref if called again with new overrides
        _dns_overrides_ref.update(overrides)
        return

    _dns_overrides_ref.update(overrides)

    def _patched_getaddrinfo(host, port, *args, **kwargs):
        if host in _dns_overrides_ref:
            real_ip = _dns_overrides_ref[host]
            return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, '', (real_ip, port))]
        return _original_getaddrinfo(host, port, *args, **kwargs)

    _socket.getaddrinfo = _patched_getaddrinfo
    _dns_patched = True
    logger.info("DNS overrides installed for OANDA: %s", list(overrides.keys()))


class OandaBackend(DataBackend):
    """Fetch forex OHLCV data from OANDA v20 REST API.

    Uses the ``/v3/instruments/{instrument}/candles`` endpoint for
    historical data (up to 5000 candles per request).

    Features:
        - DNS bypass for ISP blocking (Indonesia)
        - Rate limiter (120 req/s OANDA limit — we use conservative 50/s)
        - Auto practice/live URL detection from account ID
        - OHLC conversion from OANDA ``mid.o/h/l/c`` → standard dict format

    Configuration (from env or constructor):
        OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_BASE_URL
    """

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        dns_overrides: dict[str, str] | None = None,
        timeout_seconds: float = 15.0,
    ):
        self._api_key = api_key or os.getenv("OANDA_API_KEY", "")
        self._account_id = account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        self._timeout = timeout_seconds
        self._client: httpx.Client | None = None

        # Auto-detect practice vs live
        if base_url:
            self._base_url = base_url
        else:
            from config.settings import OANDA_BASE_URL
            self._base_url = OANDA_BASE_URL

        # Install DNS overrides if needed
        if dns_overrides is None:
            from config.settings import OANDA_DNS_OVERRIDES
            dns_overrides = OANDA_DNS_OVERRIDES
        if dns_overrides:
            _install_dns_overrides(dns_overrides)

    def _ensure_client(self) -> httpx.Client:
        """Lazy-init httpx client with auth headers."""
        if self._client is not None:
            return self._client

        if not self._api_key:
            raise ValueError(
                "OANDA_API_KEY not set. Provide via env var or constructor."
            )

        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        logger.info("OANDA client initialized (base=%s)", self._base_url)
        return self._client

    @staticmethod
    def _to_instrument(pair: str) -> str:
        """Convert 'EURUSD' → 'EUR_USD', 'XAUUSD' → 'XAU_USD'."""
        pair = pair.upper().strip()
        if pair in OANDA_INSTRUMENTS:
            return OANDA_INSTRUMENTS[pair]
        # Generic: split 6-char pair at index 3
        if len(pair) == 6:
            return f"{pair[:3]}_{pair[3:]}"
        raise ValueError(f"Cannot convert '{pair}' to OANDA instrument format.")

    def fetch_ohlcv(
        self,
        pair: str,
        timeframe: str,
        count: int = 300,
        from_date: Optional[datetime] = None,
    ) -> list[dict]:
        """Fetch candles from OANDA v20 REST API.

        Uses GET /v3/instruments/{instrument}/candles with mid prices.

        Args:
            pair: e.g. "XAUUSD", "EURUSD" — auto-converted to OANDA format.
            timeframe: "M1", "M5", "M15", "H1", "H4", "D1", "W1".
            count: Number of candles desired (max 5000).
            from_date: Optional start datetime.

        Returns:
            List of candle dicts: {open, high, low, close, volume, time}.
        """
        client = self._ensure_client()

        instrument = self._to_instrument(pair)
        granularity = OANDA_GRANULARITY.get(timeframe)
        if granularity is None:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. "
                f"Supported: {list(OANDA_GRANULARITY.keys())}"
            )

        # Build params
        params: dict = {
            "granularity": granularity,
            "price": "M",  # midpoint
        }

        if from_date is not None:
            params["from"] = from_date.isoformat()
            params["count"] = min(count, 5000)
        else:
            params["count"] = min(count, 5000)

        url = f"{self._base_url}/v3/instruments/{instrument}/candles"

        logger.info(
            "OANDA candles(%s, %s, count=%d) …",
            instrument, granularity, params.get("count", count),
        )

        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "OANDA HTTP %d for %s %s: %s",
                exc.response.status_code, pair, timeframe,
                exc.response.text[:300],
            )
            return []
        except httpx.HTTPError as exc:
            logger.error("OANDA request failed for %s %s: %s", pair, timeframe, exc)
            return []

        data = resp.json()
        raw_candles = data.get("candles", [])

        if not raw_candles:
            logger.warning("OANDA returned 0 candles for %s %s", pair, timeframe)
            return []

        # Convert OANDA format → internal format
        candles: list[dict] = []
        for c in raw_candles:
            mid = c.get("mid")
            if not mid:
                continue  # skip if no mid price (shouldn't happen with price=M)
            candles.append({
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": float(c.get("volume", 0)),
                "time": c["time"],
            })

        logger.info(
            "Fetched %d candles for %s %s via OANDA",
            len(candles), pair, timeframe,
        )

        if len(candles) < count * 0.5:
            logger.warning(
                "OANDA returned only %d/%d candles for %s %s "
                "(market may be closed or weekend)",
                len(candles), count, pair, timeframe,
            )

        return candles

    def available_pairs(self) -> list[str]:
        """Return instruments available on this OANDA account."""
        client = self._ensure_client()
        if not self._account_id:
            return list(OANDA_INSTRUMENTS.keys())
        try:
            resp = client.get(
                f"{self._base_url}/v3/accounts/{self._account_id}/instruments"
            )
            resp.raise_for_status()
            instruments = resp.json().get("instruments", [])
            return [inst["name"] for inst in instruments]
        except Exception as exc:
            logger.warning("Failed to fetch OANDA instruments: %s", exc)
            return list(OANDA_INSTRUMENTS.keys())

    def close(self) -> None:
        """Close the httpx client."""
        if self._client:
            self._client.close()
            self._client = None


# =========================================================================
# MT5 API Backend — local Flask bridge to MetaTrader 5
# =========================================================================

class MT5ApiBackend(DataBackend):
    """Fetch OHLCV via local MT5 Flask API (`/ohlcv`).

    Expected endpoint:
        GET {base_url}/ohlcv?symbol=EURUSD&timeframe=M15&count=300
    Response:
        list[dict] with keys open/high/low/close/tick_volume/time
    """

    def __init__(self, base_url: str, timeout_seconds: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_ohlcv(
        self,
        pair: str,
        timeframe: str,
        count: int = 300,
        from_date: Optional[datetime] = None,
    ) -> list[dict]:
        del from_date  # not used by /ohlcv endpoint

        if timeframe not in TF_MINUTES:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. Supported: {list(TF_MINUTES.keys())}"
            )

        url = f"{self.base_url}/ohlcv"
        params = {
            "symbol": pair.upper().strip(),
            "timeframe": timeframe,
            "count": count,
        }

        try:
            response = httpx.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MT5 API request failed: {exc}") from exc

        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("MT5 API returned invalid payload (expected list)")

        candles: list[dict] = []
        for row in payload:
            candles.append(
                {
                    "open": float(row.get("open", 0.0)),
                    "high": float(row.get("high", 0.0)),
                    "low": float(row.get("low", 0.0)),
                    "close": float(row.get("close", 0.0)),
                    "volume": float(row.get("tick_volume", row.get("volume", 0.0))),
                    "time": str(row.get("time", "")),
                }
            )

        logger.info(
            "Fetched %d candles for %s %s via MT5ApiBackend",
            len(candles),
            pair,
            timeframe,
        )
        return candles

    def available_pairs(self) -> list[str]:
        # The external MT5 API in app_2/app does not provide a dedicated
        # list-symbols endpoint yet.
        return []


# =========================================================================
# Demo / Stub Backend — synthetic data for testing & dev
# =========================================================================

# Realistic base prices per pair
_DEMO_BASE_PRICES: dict[str, float] = {
    "XAUUSD": 2650.0,
    "EURUSD": 1.0480,
    "USDCHF": 0.8850,
    "USDCAD": 1.3580,
    "GBPJPY": 193.50,
    "USDJPY": 154.20,
    "DXY": 104.50,
}

# Typical daily range (approximately)
_DEMO_DAILY_RANGE: dict[str, float] = {
    "XAUUSD": 30.0,
    "EURUSD": 0.0060,
    "USDCHF": 0.0050,
    "USDCAD": 0.0055,
    "GBPJPY": 1.80,
    "USDJPY": 1.10,
    "DXY": 0.60,
}


class DemoBackend(DataBackend):
    """Generate synthetic price data for testing.

    Uses geometric Brownian motion with a trend component to create
    realistic-looking candles. Deterministic when *seed* is provided.
    """

    def __init__(self, seed: int | None = 42):
        self._rng = random.Random(seed)

    def fetch_ohlcv(
        self,
        pair: str,
        timeframe: str,
        count: int = 300,
        from_date: Optional[datetime] = None,
    ) -> list[dict]:
        base = _DEMO_BASE_PRICES.get(pair, 100.0)
        daily_range = _DEMO_DAILY_RANGE.get(pair, base * 0.005)
        tf_min = TF_MINUTES.get(timeframe, 60)

        # Scale volatility by timeframe (relative to D1 = 1440 min)
        vol_scale = math.sqrt(tf_min / 1440.0)
        bar_range = daily_range * vol_scale

        if from_date is None:
            from_date = datetime.now(tz=timezone.utc) - timedelta(minutes=tf_min * count)

        price = base
        candles: list[dict] = []

        for i in range(count):
            # Random walk with slight mean-reversion to base
            drift = (base - price) * 0.002  # mean reversion
            noise = self._rng.gauss(0, bar_range * 0.4)
            change = drift + noise

            o = price
            c = price + change

            # High / low extend beyond open-close range
            body = abs(c - o)
            upper_wick = abs(self._rng.gauss(0, bar_range * 0.3))
            lower_wick = abs(self._rng.gauss(0, bar_range * 0.3))
            h = max(o, c) + upper_wick
            l = min(o, c) - lower_wick

            # Ensure OHLC consistency
            h = max(h, o, c)
            l = min(l, o, c)

            ts = from_date + timedelta(minutes=tf_min * i)
            candles.append(
                {
                    "open": round(o, 5),
                    "high": round(h, 5),
                    "low": round(l, 5),
                    "close": round(c, 5),
                    "volume": round(self._rng.uniform(500, 5000), 0),
                    "time": ts.isoformat(),
                }
            )
            price = c  # next bar opens at previous close

        return candles

    def available_pairs(self) -> list[str]:
        return list(_DEMO_BASE_PRICES.keys())


# =========================================================================
# High-level convenience function (Gemini Function Declaration)
# =========================================================================

def _init_default_backend() -> DataBackend:
    """Pick the best available backend at import time.

    Priority:
      1) OandaBackend   (if OANDA_API_KEY + OANDA_ACCOUNT_ID set)
      2) MT5ApiBackend  (if MT5_OHLCV_API_URL is set and reachable)
      3) DemoBackend    (fallback only in TRADING_MODE=demo)
    """
    # --- 1) OANDA v20 REST API ---------------------------------------------
    oanda_key = os.getenv("OANDA_API_KEY", "").strip()
    oanda_acct = os.getenv("OANDA_ACCOUNT_ID", "").strip()
    if oanda_key and oanda_acct:
        try:
            backend = OandaBackend(
                api_key=oanda_key,
                account_id=oanda_acct,
                timeout_seconds=10.0,
            )
            # Quick probe - fetch 2 M15 candles for EUR_USD
            probe = backend.fetch_ohlcv("EURUSD", "M15", count=2)
            if probe:
                logger.info("OANDA_API_KEY detected - using OandaBackend")
                return backend
            logger.warning("OANDA probe returned no candles - backend not usable")
        except Exception as exc:
            logger.warning(
                "OANDA API unavailable (%s) - trying MT5 fallback",
                exc,
            )

    # --- 2) MT5 local API --------------------------------------------------
    mt5_api_url = os.getenv("MT5_OHLCV_API_URL", "").strip()
    if mt5_api_url:
        try:
            # FIX F1-03: Reduce probe timeout from 15s to 3s
            backend = MT5ApiBackend(base_url=mt5_api_url, timeout_seconds=3.0)
            probe = backend.fetch_ohlcv("EURUSD", "M15", count=5)
            if probe:
                logger.info("MT5_OHLCV_API_URL detected - using MT5ApiBackend")
                return backend
        except Exception as exc:
            logger.warning(
                "MT5 API unavailable (%s) - no further real-data fallback configured",
                exc,
            )

    # --- 3) DemoBackend (guard in production) -------------------------------
    # OANDA-first policy: no third-party public data fallback.
    trading_mode = os.getenv("TRADING_MODE", "demo").strip().lower()
    if trading_mode == "real":
        logger.critical(
            "TRADING_MODE=real but OANDA/MT5 backend is unavailable. "
            "Refusing to start without OANDA data."
        )
        raise RuntimeError(
            "OANDA-only mode active: no valid OANDA/MT5 data backend. "
            "Fix MT5_OHLCV_API_URL or OANDA connectivity."
        )

    logger.warning(
        "No OANDA/MT5 backend available - falling back to DemoBackend "
        "(allowed only in TRADING_MODE=demo)."
    )
    return DemoBackend(seed=42)


# FIX H-14: Lazy-init backend instead of module-level network call.
# _init_default_backend() is deferred to first get_backend() call.
_active_backend: DataBackend | None = None
_backend_init_done = False


def set_backend(backend: DataBackend) -> None:
    """Switch the active data backend at runtime."""
    global _active_backend, _backend_init_done
    _active_backend = backend
    _backend_init_done = True
    logger.info("Data backend changed to %s", type(backend).__name__)


def get_backend() -> DataBackend:
    """Return the currently active data backend (lazy-initialised).

    FIX H-14: Defers the network probe from module import time to
    first actual usage.  This prevents import-time side effects and
    reduces startup latency when fetcher is imported but not yet used.
    """
    global _active_backend, _backend_init_done
    if not _backend_init_done:
        _active_backend = _init_default_backend()
        _backend_init_done = True
    assert _active_backend is not None
    return _active_backend


def fetch_ohlcv(
    pair: str,
    timeframe: str,
    count: int = 300,
) -> dict:
    """Fetch OHLCV candle data for a trading pair and timeframe.

    This is the function registered as a Gemini Function Declaration.

    Args:
        pair: Symbol name, e.g. "XAUUSD", "EURUSD".
        timeframe: One of "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1".
        count: Number of candles to fetch (default 300).

    Returns:
        Dict with keys:
            pair (str): The requested pair.
            timeframe (str): The requested timeframe.
            count (int): Actual number of candles returned.
            candles (list[dict]): List of OHLCV candle dicts.
    """
    backend = get_backend()
    candles = backend.fetch_ohlcv(pair, timeframe, count)
    return {
        "pair": pair,
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles,
    }


# =========================================================================
# Synthetic DXY — ICE US Dollar Index computed from 6 OANDA pairs
# =========================================================================

def fetch_synthetic_dxy(
    timeframe: str,
    count: int = 200,
) -> list[dict]:
    """Compute synthetic DXY OHLCV from 6 component currency pairs.

    Uses the official ICE DXY formula:
        DXY = 50.14348112 × EURUSD^(-0.576) × USDJPY^(0.136)
              × GBPUSD^(-0.119) × USDCAD^(0.091)
              × USDSEK^(0.042) × USDCHF^(0.036)

    All 6 component pairs are fetched from OANDA and aligned by index.
    Each candle's OHLC is computed independently through the formula.

    Args:
        timeframe: "M1", "M5", "M15", "H1", "H4", "D1", "W1".
        count: Number of candles to fetch per component pair (default 200).

    Returns:
        List of synthetic DXY candle dicts with keys:
        {open, high, low, close, volume, time}.
        Empty list if any critical component pair fails to fetch.
    """
    from config.settings import DXY_ICE_CONSTANT, DXY_COMPONENT_PAIRS

    backend = get_backend()

    # Step 1: Fetch all 6 component pairs
    component_candles: dict[str, list[dict]] = {}
    for pair, _exp, _inv in DXY_COMPONENT_PAIRS:
        try:
            candles = backend.fetch_ohlcv(pair, timeframe, count)
            if not candles:
                logger.warning("Synthetic DXY: no candles for %s %s", pair, timeframe)
                return []
            component_candles[pair] = candles
        except Exception as exc:
            logger.error("Synthetic DXY: failed to fetch %s: %s", pair, exc)
            return []

    # Step 2: Find the minimum length across all components
    min_len = min(len(c) for c in component_candles.values())
    if min_len < 10:
        logger.warning("Synthetic DXY: insufficient data (%d bars)", min_len)
        return []

    # Step 3: Compute DXY for each bar using ICE formula
    dxy_candles: list[dict] = []
    for i in range(min_len):
        dxy_ohlc: dict[str, float] = {}
        valid = True

        for price_field in ("open", "high", "low", "close"):
            product = DXY_ICE_CONSTANT
            for pair, exponent, _inv in DXY_COMPONENT_PAIRS:
                price = component_candles[pair][i].get(price_field, 0.0)
                if price <= 0:
                    valid = False
                    break
                product *= price ** exponent
            if not valid:
                break
            dxy_ohlc[price_field] = round(product, 4)

        if not valid:
            continue

        # Ensure OHLC consistency: high >= max(O,C), low <= min(O,C)
        dxy_ohlc["high"] = max(dxy_ohlc["high"], dxy_ohlc["open"], dxy_ohlc["close"])
        dxy_ohlc["low"] = min(dxy_ohlc["low"], dxy_ohlc["open"], dxy_ohlc["close"])

        # Use the first component's time and sum volumes
        total_vol = sum(
            component_candles[pair][i].get("volume", 0)
            for pair, _, _ in DXY_COMPONENT_PAIRS
        )
        dxy_candles.append({
            "open": dxy_ohlc["open"],
            "high": dxy_ohlc["high"],
            "low": dxy_ohlc["low"],
            "close": dxy_ohlc["close"],
            "volume": total_vol,
            "time": component_candles[DXY_COMPONENT_PAIRS[0][0]][i].get("time", ""),
        })

    logger.info(
        "Synthetic DXY computed: %d bars from %s (components: %d bars each)",
        len(dxy_candles), timeframe, min_len,
    )
    return dxy_candles


async def fetch_synthetic_dxy_async(
    timeframe: str,
    count: int = 200,
) -> list[dict]:
    """Async wrapper for fetch_synthetic_dxy."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_synthetic_dxy, timeframe, count)


# =========================================================================
# Async wrapper (FIX F1-02)
# =========================================================================
import asyncio


async def fetch_ohlcv_async(
    pair: str,
    timeframe: str,
    count: int = 300,
) -> dict:
    """Async wrapper that runs sync fetch in a thread pool to avoid
    blocking the event loop.

    FIX M-24: Uses get_running_loop() instead of deprecated get_event_loop().
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_ohlcv, pair, timeframe, count)

