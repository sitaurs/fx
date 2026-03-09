"""
dashboard/backend/routes/market.py — Market data proxy for Dashboard V3.

Provides:
  - GET /api/market/candles/{pair} — OHLCV candle data from OANDA

Does NOT modify core trading system. Reads OANDA settings from config.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, Depends

from dashboard.backend.routes.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/candles/{pair}")
async def get_candles(
    pair: str,
    timeframe: str = Query("H1", description="Candle granularity: M1, M5, M15, H1, H4, D1"),
    count: int = Query(200, ge=1, le=1000),
    _user: str = Depends(require_auth),
) -> dict:
    """Fetch OHLCV candles from OANDA v20 API."""
    import asyncio
    try:
        from data.fetcher import OandaBackend
        fetcher = OandaBackend()

        # Convert pair format (EUR_USD or EURUSD)
        instrument = pair.upper()

        # OandaBackend.fetch_ohlcv is synchronous — run in executor
        loop = asyncio.get_event_loop()
        candles = await loop.run_in_executor(
            None,
            lambda: fetcher.fetch_ohlcv(
                pair=instrument,
                timeframe=timeframe.upper(),
                count=count,
            ),
        )

        # Convert to lightweight-charts format (epoch seconds)
        formatted = []
        for c in candles:
            t = c["time"]
            if isinstance(t, datetime):
                ts = int(t.timestamp())
            elif isinstance(t, str):
                # OANDA returns RFC3339: "2026-03-07T10:00:00.000000000Z"
                from datetime import datetime as dt
                try:
                    ts = int(dt.fromisoformat(t.replace("Z", "+00:00")).timestamp())
                except Exception:
                    ts = int(dt.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").timestamp())
            else:
                ts = int(t)
            formatted.append({
                "time": ts,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0),
            })

        return {"candles": formatted, "pair": instrument, "timeframe": timeframe}

    except ImportError as e:
        logger.warning("OandaBackend import failed: %s — returning empty candles", e)
        return {"candles": [], "pair": pair, "timeframe": timeframe}
    except Exception as exc:
        logger.error("Candle fetch failed for %s %s: %s", pair, timeframe, exc)
        return {"candles": [], "pair": pair, "timeframe": timeframe, "error": str(exc)}
