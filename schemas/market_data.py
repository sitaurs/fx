"""
schemas/market_data.py — Core data models for OHLCV and derived data.

Reference: masterplan.md §5.2 (Pydantic Schemas)
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

from schemas.structure import StructureEvent  # L-55: used in MarketStructure.events


class Candle(BaseModel):
    """Single OHLCV candle."""

    time: str = Field(description="ISO-8601 timestamp")
    open: float
    high: float
    low: float
    close: float
    # L-56: volume defaults to 0.0 because forex feeds provide tick volume
    # (not real volume).  A value of 0.0 indicates the data source did not
    # supply volume — callers should NOT treat 0.0 as "zero trading activity".
    volume: float = Field(
        default=0.0,
        description="Tick volume; 0.0 means volume data was unavailable",
    )


class SwingPoint(BaseModel):
    """Detected swing high or swing low."""

    index: int = Field(description="Bar index in the OHLCV array")
    price: float
    time: str
    type: str = Field(description="'high' or 'low'")


class ATRValue(BaseModel):
    """ATR result for a specific period."""

    period: int
    values: list[float] = Field(description="ATR value per bar (NaN-padded at start)")
    current: float = Field(description="Most recent ATR value")


class EMAValue(BaseModel):
    """EMA result."""

    period: int
    values: list[float]
    current: float


class RSIValue(BaseModel):
    """RSI result."""

    period: int
    values: list[float]
    current: float


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class StrategyMode(str, Enum):
    INDEX_CORRELATION = "index_correlation"
    SNIPER_CONFLUENCE = "sniper_confluence"
    SCALPING_CHANNEL = "scalping_channel"


class ZoneType(str, Enum):
    SUPPLY = "supply"
    DEMAND = "demand"


class Zone(BaseModel):
    """A price zone (SnD, OB, or SNR)."""

    zone_type: ZoneType
    high: float
    low: float
    source: str = Field(description="'supply_demand' | 'order_block' | 'snr'")
    score: float = 0.0
    is_fresh: bool = True
    origin_index: int = 0
    origin_time: str = ""


class MarketStructure(BaseModel):
    """BOS / CHOCH events.

    L-55: ``events`` typed as ``list[StructureEvent]`` instead of raw
    ``list[dict]``.  ``StructureEvent`` is imported from
    ``schemas.structure`` via deferred annotation (``from __future__
    import annotations`` + ``TYPE_CHECKING``).
    """

    trend: str = Field(description="'bullish' | 'bearish' | 'ranging'")
    events: list[StructureEvent] = Field(default_factory=list)
