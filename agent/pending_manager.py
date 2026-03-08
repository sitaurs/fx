"""
agent/pending_manager.py — Pending setup queue for AI Forex Agent.

Manages setups that passed analysis/scoring but haven't been executed yet
because market price is outside the entry zone.  The queue is monitored
every ~60 seconds; when price enters the zone the setup is promoted to
an active trade.

Key concepts:
  - PendingSetup: dataclass holding the full TradingPlan + metadata
  - PendingManager: manages the list, checks TTL, zone entry, invalidation

Reference: masterplan.md §11 (State Machine -> PENDING_QUEUE), Phase 4 plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from config.settings import PAIR_POINT
from schemas.plan import TradingPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Forex market hours utility (FIX M-05)
# ---------------------------------------------------------------------------

# Forex market: Sunday 22:00 UTC → Friday 22:00 UTC
FOREX_OPEN_DAY_HOUR = (6, 22)   # Sunday 22:00 UTC  (weekday 6 = Sunday)
FOREX_CLOSE_DAY_HOUR = (4, 22)  # Friday 22:00 UTC  (weekday 4 = Friday)


def is_forex_market_open(dt: datetime) -> bool:
    """Return True if forex market is open at the given UTC datetime.

    Forex operates Sun 22:00 UTC → Fri 22:00 UTC.
    Closed: Fri 22:00 → Sun 22:00.
    """
    wd = dt.weekday()  # Mon=0 … Sun=6
    h = dt.hour
    # Closed Saturday all day
    if wd == 5:
        return False
    # Closed Sunday before 22:00
    if wd == 6 and h < 22:
        return False
    # Closed Friday after 22:00
    if wd == 4 and h >= 22:
        return False
    return True


def count_market_hours(start: datetime, end: datetime) -> float:
    """Count forex market-open hours between *start* and *end* (UTC).

    Uses hourly granularity — accurate to ~1 hour. Sufficient for TTL
    checks where TTL is typically 2–8 hours.
    """
    if end <= start:
        return 0.0
    total_seconds = 0.0
    current = start
    # Step in 30-min increments for reasonable accuracy
    step = timedelta(minutes=30)
    while current < end:
        next_step = min(current + step, end)
        if is_forex_market_open(current):
            total_seconds += (next_step - current).total_seconds()
        current = next_step
    return total_seconds / 3600


# ---------------------------------------------------------------------------
# PendingSetup
# ---------------------------------------------------------------------------

@dataclass
class PendingSetup:
    """A trade setup waiting in the queue for price to reach entry zone."""

    setup_id: str                    # unique ID (e.g., "PQ-xxxxxxxx")
    pair: str
    plan: TradingPlan                # full plan from Gemini analysis
    direction: str                   # "buy" | "sell"
    entry_zone_low: float
    entry_zone_high: float
    recommended_entry: float         # computed optimal fixed price
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float]
    confluence_score: int
    ttl_hours: float                 # how long this setup stays valid
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default=None)  # type: ignore[assignment]
    status: str = "pending"          # pending | executing | executed | expired | cancelled | invalidated

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(hours=self.ttl_hours)

    @property
    def is_expired(self) -> bool:
        """Check if TTL has elapsed, counting only market-open hours (FIX M-05).

        During forex market close (Fri 22:00 → Sun 22:00 UTC), TTL pauses.
        """
        now = datetime.now(timezone.utc)
        elapsed = count_market_hours(self.created_at, now)
        return elapsed >= self.ttl_hours

    @property
    def remaining_ttl_minutes(self) -> float:
        """Approximate remaining TTL in minutes (market-hours aware)."""
        now = datetime.now(timezone.utc)
        elapsed = count_market_hours(self.created_at, now)
        remaining_hours = max(self.ttl_hours - elapsed, 0.0)
        return remaining_hours * 60

    def to_dict(self) -> dict:
        """Serialize for dashboard / WebSocket push."""
        point = PAIR_POINT.get(self.pair, 0.0001)
        return {
            "setup_id": self.setup_id,
            "pair": self.pair,
            "direction": self.direction,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
            "recommended_entry": round(self.recommended_entry, 5),
            "stop_loss": round(self.stop_loss, 5),
            "take_profit_1": round(self.take_profit_1, 5),
            "take_profit_2": round(self.take_profit_2, 5) if self.take_profit_2 else None,
            "confluence_score": self.confluence_score,
            "ttl_hours": self.ttl_hours,
            "remaining_ttl_minutes": round(self.remaining_ttl_minutes, 1),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }

    def to_persistence_dict(self) -> dict:
        """For JSON persistence to DB."""
        d = self.to_dict()
        d["plan_json"] = self.plan.model_dump_json() if self.plan else None
        return d


# ---------------------------------------------------------------------------
# Recommended entry calculation
# ---------------------------------------------------------------------------

def compute_recommended_entry(
    direction: str,
    entry_zone_low: float,
    entry_zone_high: float,
) -> float:
    """Compute a single 'optimal' entry price from an entry zone.

    For BUY : deeper = lower → 70% toward the zone bottom.
    For SELL: deeper = higher → 70% toward the zone top.

    This gives a better average entry (closer to invalidation edge)
    while still being inside the zone.
    """
    zone_range = entry_zone_high - entry_zone_low
    if zone_range <= 0:
        return (entry_zone_low + entry_zone_high) / 2

    if direction.lower() == "buy":
        # 30% from bottom = more aggressive entry
        return entry_zone_low + 0.30 * zone_range
    else:
        # 70% from bottom = closer to top for sells
        return entry_zone_low + 0.70 * zone_range


# ---------------------------------------------------------------------------
# PendingManager
# ---------------------------------------------------------------------------

class PendingManager:
    """Manages the pending setup queue.

    Responsibilities:
      - Add new setups from scan results
      - Check and expire TTL-expired setups
      - Check if price has entered entry zone → promote to execution
      - Remove / cancel setups
      - Serialize for dashboard

    FIX CON-04 — State naming clarification:
    The ``PendingSetup.status`` field uses its own lifecycle
    (pending → executing → executed | expired | cancelled | invalidated)
    independent of the orchestrator's ``AnalysisState`` enum
    (SCANNING → WATCHING → APPROACHING → TRIGGERED → ACTIVE → CLOSED).
    A pending setup is created AFTER the orchestrator reaches TRIGGERED
    and produces a TradingPlan, but market price is outside the entry
    zone.  Once price enters the zone, the pending setup transitions to
    ``executing`` → ``executed`` and becomes an ActiveTrade (equivalent
    to the ACTIVE state concept in the state machine).
    """

    def __init__(self, max_pending: int = 10):
        self._queue: list[PendingSetup] = []
        self.max_pending = max_pending

    @property
    def count(self) -> int:
        return len(self._queue)

    @property
    def pending_pairs(self) -> list[str]:
        return [s.pair for s in self._queue if s.status == "pending"]

    def get_all(self) -> list[PendingSetup]:
        """Return all setups (including non-pending for history)."""
        return list(self._queue)

    def get_pending(self) -> list[PendingSetup]:
        """Return only active pending setups."""
        return [s for s in self._queue if s.status == "pending"]

    def add(self, setup: PendingSetup) -> bool:
        """Add a new setup to the queue.

        Returns False if queue is full or pair already in queue.
        """
        # Don't add duplicate pair
        if any(s.pair == setup.pair and s.status == "pending" for s in self._queue):
            logger.info("Pending queue: %s already in queue — skip", setup.pair)
            return False

        # Enforce max pending
        active_pending = [s for s in self._queue if s.status == "pending"]
        if len(active_pending) >= self.max_pending:
            logger.warning(
                "Pending queue full (%d/%d) — skip %s",
                len(active_pending), self.max_pending, setup.pair,
            )
            return False

        self._queue.append(setup)
        logger.info(
            "📋 Added to pending queue: %s %s, zone=%.5f-%.5f, rec=%.5f, TTL=%.1fh",
            setup.pair, setup.direction,
            setup.entry_zone_low, setup.entry_zone_high,
            setup.recommended_entry, setup.ttl_hours,
        )
        return True

    def remove_by_id(self, setup_id: str) -> bool:
        """Cancel a setup by ID. Returns True if found and cancelled."""
        for s in self._queue:
            if s.setup_id == setup_id and s.status == "pending":
                s.status = "cancelled"
                logger.info("Pending setup cancelled: %s (%s)", s.setup_id, s.pair)
                return True
        return False

    def remove_by_pair(self, pair: str) -> bool:
        """Cancel all pending setups for a pair."""
        found = False
        for s in self._queue:
            if s.pair == pair and s.status == "pending":
                s.status = "cancelled"
                found = True
        return found

    def cleanup_expired(self) -> list[PendingSetup]:
        """Mark expired setups and return them."""
        expired = []
        for s in self._queue:
            if s.status == "pending" and s.is_expired:
                s.status = "expired"
                expired.append(s)
                logger.info("Pending setup expired: %s (%s)", s.setup_id, s.pair)
        return expired

    def check_zone_entries(
        self,
        prices: dict[str, float],
        entry_zone_buffer_pips: float = 0.0,
    ) -> list[PendingSetup]:
        """Check which pending setups have price inside entry zone.

        Returns list of setups ready for execution.
        """
        ready = []
        for s in self._queue:
            if s.status not in ("pending",):
                continue  # skip executing, executed, expired, etc.
            price = prices.get(s.pair)
            if price is None:
                continue

            # Apply buffer
            point = PAIR_POINT.get(s.pair, 0.0001)
            buffer = entry_zone_buffer_pips * point
            z_low = s.entry_zone_low - buffer
            z_high = s.entry_zone_high + buffer

            if z_low <= price <= z_high:
                ready.append(s)
                logger.info(
                    "Pending setup %s (%s): price %.5f in zone %.5f-%.5f — ready",
                    s.setup_id, s.pair, price, z_low, z_high,
                )
        return ready

    def mark_executed(self, setup_id: str) -> None:
        """Mark a setup as executed (promoted to active trade)."""
        for s in self._queue:
            if s.setup_id == setup_id:
                s.status = "executed"
                logger.info("Pending setup executed: %s (%s)", s.setup_id, s.pair)
                break

    def mark_executing(self, setup_id: str) -> bool:
        """Mark a setup as 'executing' to prevent duplicate execution (FIX H-02).

        Returns True if the setup was pending and is now marked executing.
        Returns False if setup was not found or not in pending status.
        """
        for s in self._queue:
            if s.setup_id == setup_id and s.status == "pending":
                s.status = "executing"
                logger.info("Pending setup executing: %s (%s)", s.setup_id, s.pair)
                return True
        return False

    def revert_executing(self, setup_id: str) -> None:
        """Revert an 'executing' setup back to 'pending' on failure (FIX H-02)."""
        for s in self._queue:
            if s.setup_id == setup_id and s.status == "executing":
                s.status = "pending"
                logger.warning("Reverted executing→pending: %s (%s)", s.setup_id, s.pair)
                break

    def mark_invalidated(self, setup_id: str, reason: str = "") -> None:
        """Mark a setup as invalidated (structure break, zone mitigated, etc.)."""
        for s in self._queue:
            if s.setup_id == setup_id:
                s.status = "invalidated"
                logger.info(
                    "Pending setup invalidated: %s (%s) — %s",
                    s.setup_id, s.pair, reason,
                )
                break

    def cleanup_old(self, max_age_hours: int = 24) -> None:
        """Remove non-pending setups older than max_age_hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        self._queue = [
            s for s in self._queue
            if s.status in ("pending", "executing") or s.created_at > cutoff
        ]

    def to_dashboard_list(self) -> list[dict]:
        """Serialize pending setups for dashboard WebSocket push."""
        return [s.to_dict() for s in self._queue if s.status == "pending"]

    def to_persistence_list(self) -> list[dict]:
        """Serialize all active pending/executing setups for DB persistence."""
        return [
            s.to_persistence_dict()
            for s in self._queue
            if s.status in ("pending", "executing")
        ]

    def restore_from_list(self, data: list[dict]) -> int:
        """Restore pending setups from persisted data."""
        restored = 0
        for d in data:
            try:
                # Reconstruct plan from JSON
                plan = None
                if d.get("plan_json"):
                    plan = TradingPlan.model_validate_json(d["plan_json"])

                created_at = datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(timezone.utc)
                expires_at = datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None

                setup = PendingSetup(
                    setup_id=d["setup_id"],
                    pair=d["pair"],
                    plan=plan,
                    direction=d["direction"],
                    entry_zone_low=d["entry_zone_low"],
                    entry_zone_high=d["entry_zone_high"],
                    recommended_entry=d["recommended_entry"],
                    stop_loss=d["stop_loss"],
                    take_profit_1=d["take_profit_1"],
                    take_profit_2=d.get("take_profit_2"),
                    confluence_score=d.get("confluence_score", 0),
                    ttl_hours=d.get("ttl_hours", 4.0),
                    created_at=created_at,
                    expires_at=expires_at,
                    status="pending",
                )

                # Skip if already expired (FIX L-06: log skipped)
                if setup.is_expired:
                    logger.info(
                        "Skipping expired pending setup on restore: %s (%s)",
                        setup.setup_id, setup.pair,
                    )
                    continue

                self._queue.append(setup)
                restored += 1
            except Exception as exc:
                logger.error("Failed to restore pending setup: %s", exc)

        if restored:
            logger.info("Restored %d pending setup(s) from DB", restored)
        return restored
