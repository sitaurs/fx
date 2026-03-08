"""
schemas/zones.py — Pydantic models for SnD zones, OB, SNR levels, liquidity.

Reference: masterplan.md §5.2, §6.3, §6.4, §6.5, §6.6

D-14 / CON-24 — Usage intent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
These are **detailed** typed schemas for zone-related data.  The tools in
``tools/supply_demand.py``, ``tools/orderblock.py``, ``tools/liquidity.py``
currently return **plain dicts** for performance.  These Pydantic models serve
as the canonical schema documentation and can be used for:

* Callers that want runtime validation (``SnDZone(**raw_dict)``).
* Dashboard serialization / API response typing.
* Future migration path when tools adopt typed returns.

For a lighter-weight generic zone see ``schemas.market_data.Zone``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

from schemas.market_data import ZoneType  # L-57: reuse canonical supply/demand enum


# L-57: Enum for order-block zone types (different from supply/demand).
class OBType(str, Enum):
    """Order-block zone type."""
    BULLISH_OB = "bullish_ob"
    BEARISH_OB = "bearish_ob"


# L-57: Enum for liquidity pool types.
class PoolType(str, Enum):
    """Liquidity pool type."""
    EQH = "eqh"   # equal highs
    EQL = "eql"   # equal lows


class ZoneFormation(str, Enum):
    """How the zone was formed (masterplan 6.4)."""
    RALLY_BASE_RALLY = "rally_base_rally"   # demand continuation
    DROP_BASE_DROP = "drop_base_drop"       # supply continuation
    RALLY_BASE_DROP = "rally_base_drop"     # supply reversal
    DROP_BASE_RALLY = "drop_base_rally"     # demand reversal


class SnDZone(BaseModel):
    """Supply or Demand zone detected from base + displacement pattern."""

    # L-57: Use ZoneType enum (was plain str).
    zone_type: ZoneType = Field(description="'supply' or 'demand'")
    formation: ZoneFormation
    high: float = Field(description="Upper boundary of the zone")
    low: float = Field(description="Lower boundary of the zone")
    base_start_idx: int = Field(description="Index of first base candle")
    base_end_idx: int = Field(description="Index of last base candle")
    displacement_strength: float = Field(description="Displacement size in ATR multiples")
    body_ratio: float = Field(description="Body/range ratio of displacement candle(s)")
    score: float = Field(default=0.0, description="Quality score: displacement × freshness × retest")
    is_fresh: bool = Field(default=True, description="Has not been mitigated yet")
    origin_time: str = Field(default="", description="Timestamp of base start")


class SNRLevel(BaseModel):
    """Support/Resistance level from multi-TF swing clustering."""

    price: float
    touches: int = Field(description="Number of swing touches in the cluster")
    recency_score: float = Field(default=0.0, description="Higher if more recent")
    rejection_strength: float = Field(default=0.0)
    tf_weight: float = Field(default=1.0, description="Higher TF = higher weight")
    is_major: bool = Field(default=False, description="True if score high & appears on H4/H1")
    source_tf: str = Field(default="", description="Dominant timeframe")


class OrderBlock(BaseModel):
    """ICT/SMC Order Block (secondary to SnD)."""

    # L-57: Use OBType enum (was plain str).
    zone_type: OBType = Field(description="'bullish_ob' or 'bearish_ob'")
    high: float
    low: float
    candle_index: int = Field(description="Index of the OB candle")
    displacement_bos: bool = Field(default=False, description="Displacement caused BOS")
    is_mitigated: bool = Field(default=False)
    score: float = Field(default=0.0)
    origin_time: str = Field(default="")


class LiquidityPool(BaseModel):
    """Equal Highs/Lows resting liquidity."""

    # L-57: Use PoolType enum (was plain str).
    pool_type: PoolType = Field(description="'eqh' or 'eql'")
    price: float = Field(description="Average price of the pool")
    swing_count: int = Field(description="Number of swings in the pool")
    indices: list[int] = Field(default_factory=list, description="Bar indices of contributing swings")
    is_swept: bool = Field(default=False)
    score: float = Field(default=0.0)


class SweepEvent(BaseModel):
    """Liquidity sweep detection result."""

    pool: LiquidityPool
    sweep_index: int = Field(description="Bar index where sweep happened")
    sweep_price: float
    reclaim: bool = Field(default=False, description="Did price close back inside?")
    time: str = Field(default="")
