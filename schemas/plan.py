"""
schemas/plan.py — Pydantic models for AI agent output (Structured Output).

SetupCandidate: single trade setup recommendation.
TradingPlan: full analysis output with primary + alternative setups.

Reference: masterplan.md §5.2 (Pydantic Schemas)
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Optional

from config.strategy_rules import MAX_POSSIBLE_SCORE
from schemas.market_data import Direction, StrategyMode


class SetupCandidate(BaseModel):
    """A single trade-setup recommendation produced by the agent."""

    direction: Direction
    strategy_mode: StrategyMode
    entry_zone_low: float = Field(description="Batas bawah zona entry")
    entry_zone_high: float = Field(description="Batas atas zona entry")
    trigger_condition: str = Field(
        description="Kondisi trigger entry, misal 'sweep + reclaim'"
    )
    stop_loss: float
    sl_reasoning: str = Field(description="Alasan penempatan SL")
    take_profit_1: float
    take_profit_2: Optional[float] = None
    tp_reasoning: str = Field(description="Alasan penempatan TP")
    risk_reward_ratio: float = Field(
        description="Risk reward ratio, harus >= 0"
    )
    management: str = Field(description="Aturan SL+, partial TP, dll")
    ttl_hours: float = Field(
        description="Berapa jam setup ini valid, harus > 0"
    )
    invalidation: str = Field(
        description="Kondisi yang membatalkan setup"
    )
    # CON-23: bounds tied to MAX_POSSIBLE_SCORE (currently 14, not 15).
    confluence_score: int = Field(
        description=f"Skor konfluensi 0-{MAX_POSSIBLE_SCORE}"
    )
    rationale: str = Field(
        description="Penjelasan lengkap kenapa setup ini valid"
    )
    recommended_entry: Optional[float] = Field(
        default=None,
        description="Recommended fixed price for limit/stop order (computed post-AI)",
    )

    # ------------------------------------------------------------------
    # Numeric constraints enforced AFTER Gemini returns values.
    # (Removed ge/gt/le from Field() because google-genai SDK rejects
    #  exclusiveMinimum / minimum / maximum in the JSON Schema.)
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _check_numeric_bounds(self) -> "SetupCandidate":
        if self.risk_reward_ratio < 0:
            raise ValueError("risk_reward_ratio must be >= 0")
        if self.ttl_hours <= 0:
            raise ValueError("ttl_hours must be > 0")
        if not (0 <= self.confluence_score <= MAX_POSSIBLE_SCORE):
            raise ValueError(
                f"confluence_score must be 0-{MAX_POSSIBLE_SCORE}"
            )
        return self


# L-53: Valid values for htf_bias. Using a set + model_validator (not Enum)
# to stay compatible with google-genai Structured Output SDK which rejects
# certain JSON-Schema keywords.
_VALID_HTF_BIAS = {"bullish", "bearish", "range", "ranging"}


class TradingPlan(BaseModel):
    """Complete analysis output for a single pair."""

    pair: str
    analysis_time: str = Field(description="ISO-8601 timestamp")
    htf_bias: str = Field(description="'bullish' | 'bearish' | 'range'")
    htf_bias_reasoning: str
    strategy_mode: StrategyMode
    primary_setup: SetupCandidate
    alternative_setup: Optional[SetupCandidate] = None
    dxy_note: Optional[str] = None
    risk_warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(
        description="Confidence 0.0-1.0 dari voting"
    )
    valid_until: str = Field(description="ISO-8601 kapan plan ini expire")

    # L-53 / L-54: Validate htf_bias values and confidence bounds.
    @model_validator(mode="after")
    def _check_plan_bounds(self) -> "TradingPlan":
        if self.htf_bias not in _VALID_HTF_BIAS:
            raise ValueError(
                f"htf_bias must be one of {sorted(_VALID_HTF_BIAS)}, got '{self.htf_bias}'"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be 0.0-1.0, got {self.confidence}"
            )
        return self
