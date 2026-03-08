"""
database/models.py — SQLAlchemy ORM models for AI Forex Agent.

Tables:
    - trades: Completed trade records (demo & real)
    - analyses: Analysis session snapshots (state machine history)
    - settings_kv: Key-value store for dynamic settings

Reference: masterplan.md §17 (Hari 2), §23 (Demo Mode),
         Section 14 (Post-Mortem)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    String,
    Text,
    Boolean,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import enum


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TradeResult(str, enum.Enum):
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    SL_HIT = "SL_HIT"
    BE_HIT = "BE_HIT"           # breakeven hit
    TRAIL_PROFIT = "TRAIL_PROFIT"  # trailing SL hit in profit (above BE)
    MANUAL_CLOSE = "MANUAL_CLOSE"
    CANCELLED = "CANCELLED"


class TradingMode(str, enum.Enum):
    DEMO = "demo"
    REAL = "real"


# ---------------------------------------------------------------------------
# Trade model — every completed (or cancelled) trade
# ---------------------------------------------------------------------------

class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Core fields
    pair: Mapped[str] = mapped_column(String(16), index=True)  # L-40: indexed for filtered queries
    direction: Mapped[str] = mapped_column(String(8))           # "buy" | "sell"
    strategy_mode: Mapped[str] = mapped_column(String(32))      # sniper_confluence etc.
    mode: Mapped[str] = mapped_column(String(8), default="demo")  # demo | real

    # Price levels
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit_1: Mapped[float] = mapped_column(Float)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Result
    result: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pips: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rr_achieved: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Scoring
    confluence_score: Mapped[int] = mapped_column(Integer, default=0)
    voting_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # SL management history
    sl_was_moved_be: Mapped[bool] = mapped_column(Boolean, default=False)
    sl_trail_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    final_sl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # P/L tracking (CON-19: fields named "demo_" for historical reasons but
    # used in BOTH demo and real modes.  Renaming requires a DB migration;
    # for now the semantic meaning is simply "pnl" and "balance_after".)
    demo_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    demo_balance_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Post-mortem (JSON)
    post_mortem_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    opened_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Analysis session — snapshot of each state-machine session
# ---------------------------------------------------------------------------

class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    pair: Mapped[str] = mapped_column(String(16), index=True)  # L-40
    state: Mapped[str] = mapped_column(String(20))           # current AnalysisState
    direction: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    strategy_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    htf_bias: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    entry_zone_mid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Link to trade if TRIGGERED → ACTIVE → CLOSED
    trade_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Plan JSON (TradingPlan serialized)
    plan_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Cancel info
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Key-value settings — persists mode, balance, etc.
# ---------------------------------------------------------------------------

class SettingsKV(Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Equity history — persists equity chart data across restarts
# ---------------------------------------------------------------------------

class EquityPoint(Base):
    __tablename__ = "equity_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True,
    )
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    high_water_mark: Mapped[float] = mapped_column(Float, nullable=False)
