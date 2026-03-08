"""
schemas/structure.py — Pydantic models for market structure (BOS/CHOCH).

Reference: masterplan.md §6.2

D-13: Actively used — ``StructureEvent`` is imported by
``schemas.market_data.MarketStructure`` (L-55) and consumed by
``tools/structure.py`` which returns dicts coercible to these models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class TrendState(str, Enum):
    """Market structure trend state (CON-13)."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


class StructureEventType(str, Enum):
    BOS = "bos"       # Break of Structure (trend continuation)
    CHOCH = "choch"   # Change of Character (trend reversal)


class StructureEvent(BaseModel):
    """A single BOS or CHOCH event."""

    event_type: StructureEventType
    direction: str = Field(description="'bullish' or 'bearish'")
    break_index: int = Field(description="Bar index where the break happened")
    break_price: float = Field(description="Price of the broken swing level")
    broken_swing_index: int = Field(description="Index of the swing that was broken")
    time: str = Field(default="")


class MarketStructureResult(BaseModel):
    """Full market structure analysis result."""

    trend: str = Field(description="'bullish' | 'bearish' | 'ranging'")
    events: list[StructureEvent] = Field(default_factory=list)
    last_hh: Optional[float] = None
    last_hl: Optional[float] = None
    last_lh: Optional[float] = None
    last_ll: Optional[float] = None
