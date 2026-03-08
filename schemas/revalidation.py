"""
schemas/revalidation.py — Pydantic model for Gemini Flash revalidation output.

Used by production_lifecycle._revalidate_trade_setup() to get structured
AI assessment of whether an active trade setup is still valid.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RevalidationResult(BaseModel):
    """Structured output from Gemini Flash revalidation."""

    still_valid: bool = Field(
        description="Whether the trade setup is still valid given current market structure"
    )
    confidence: float = Field(
        description="Confidence 0.0-1.0 in the validity assessment"
    )
    recommended_action: str = Field(
        description=(
            "One of: 'hold' (keep position), 'tighten_sl' (move SL closer), "
            "'close_early' (close position now), 'partial_close' (close partial)"
        )
    )
    structure_trend: str = Field(
        description="Current market structure trend: 'bullish', 'bearish', or 'range'"
    )
    key_observations: str = Field(
        description="Brief summary of key market observations affecting the trade"
    )
    risk_factors: str = Field(
        description="Any new risk factors identified since trade was opened"
    )
