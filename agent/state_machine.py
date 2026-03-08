"""
agent/state_machine.py — Analysis lifecycle state machine.

6+1 states: SCANNING → WATCHING → APPROACHING → TRIGGERED → ACTIVE → CLOSED
             + CANCELLED (from any non-terminal state).

Core rules (masterplan §11, §12):
  - Strict linear transitions — skipping states is illegal.
  - Conviction lock: direction, strategy_mode, htf_bias are IMMUTABLE
    once state >= WATCHING.
  - Hysteresis: cancel only if score drops below HYSTERESIS_CANCEL_SCORE (3).
  - Cooldown: COOLDOWN_MINUTES after CANCELLED before new setup.

Reference: masterplan.md §11 (State Machine), §12 (Anti-Flip-Flop)
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from config.settings import HYSTERESIS_CANCEL_SCORE, COOLDOWN_MINUTES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IllegalTransition(Exception):
    """Raised when a state transition violates the allowed graph."""


class ConvictionLockViolation(Exception):
    """Raised when a locked field (direction, htf_bias, mode) changes."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AnalysisState(str, Enum):
    SCANNING = "SCANNING"
    WATCHING = "WATCHING"
    APPROACHING = "APPROACHING"
    TRIGGERED = "TRIGGERED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# State-transition graph (allowed forward transitions)
# ---------------------------------------------------------------------------
# NOTE (FIX D-07): CANCELLED is reachable from any non-terminal state via
# the cancel() method (not through transition()).  Returning from CANCELLED
# to SCANNING is done via reset() after the cooldown period expires.
# This dual-path design keeps the linear forward graph clean while still
# supporting emergency cancellation from any state.
_ALLOWED_TRANSITIONS: dict[AnalysisState, set[AnalysisState]] = {
    AnalysisState.SCANNING: {AnalysisState.WATCHING},
    AnalysisState.WATCHING: {AnalysisState.APPROACHING},
    AnalysisState.APPROACHING: {AnalysisState.TRIGGERED},
    AnalysisState.TRIGGERED: {AnalysisState.ACTIVE},
    AnalysisState.ACTIVE: {AnalysisState.CLOSED},
    AnalysisState.CLOSED: set(),       # terminal
    AnalysisState.CANCELLED: set(),    # terminal
}

# Locked fields (conviction lock) — immutable once >= WATCHING
_LOCKED_FIELDS = ("direction", "strategy_mode", "htf_bias")


# ---------------------------------------------------------------------------
# Context dataclass
# ---------------------------------------------------------------------------

@dataclass
class SetupContext:
    """Snapshot of the current setup state carried through transitions."""

    pair: str
    direction: str            # "buy" | "sell"
    strategy_mode: str        # e.g. "sniper_confluence"
    entry_zone_mid: float
    score: int
    confidence: float
    htf_bias: str             # "bullish" | "bearish" | "range"


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class StateMachine:
    """Manages the lifecycle of a single pair's analysis session.

    Usage::

        sm = StateMachine()
        sm.transition(AnalysisState.WATCHING, ctx)
        if sm.should_cancel(updated_ctx):
            sm.cancel("score below threshold")
    """

    def __init__(self) -> None:
        self._state: AnalysisState = AnalysisState.SCANNING
        self._context: Optional[SetupContext] = None
        self._cancel_reason: Optional[str] = None
        self._cancel_time: Optional[float] = None
        self._history: list[tuple[AnalysisState, float]] = [
            (AnalysisState.SCANNING, time.time())
        ]

    # -- Properties ---------------------------------------------------------

    @property
    def state(self) -> AnalysisState:
        return self._state

    @property
    def context(self) -> Optional[SetupContext]:
        return self._context

    @property
    def cancel_reason(self) -> Optional[str]:
        return self._cancel_reason

    # -- Transition ---------------------------------------------------------

    def transition(
        self,
        target: AnalysisState,
        ctx: SetupContext,
    ) -> None:
        """Attempt to move to *target* state.

        Raises ``IllegalTransition`` if the move isn't allowed.
        Raises ``ConvictionLockViolation`` if locked fields changed.
        """
        # 1. Check legal move
        allowed = _ALLOWED_TRANSITIONS.get(self._state, set())
        if target not in allowed:
            raise IllegalTransition(
                f"Cannot transition {self._state.value} → {target.value}"
            )

        # 2. Conviction lock check (if we already have context)
        if self._context is not None:
            for fld in _LOCKED_FIELDS:
                old_val = getattr(self._context, fld)
                new_val = getattr(ctx, fld)
                if old_val != new_val:
                    raise ConvictionLockViolation(
                        f"Field '{fld}' is locked: "
                        f"'{old_val}' → '{new_val}' not allowed."
                    )

        # 3. Apply
        self._state = target
        self._context = ctx
        self._history.append((target, time.time()))
        # FIX L-18: include pair name in all state transition logs
        logger.info("State -> %s  pair=%s  from=%s",
                    target.value, ctx.pair,
                    self._history[-2][0].value if len(self._history) >= 2 else "INIT")

    # -- Cancellation -------------------------------------------------------

    def cancel(self, reason: str) -> None:
        """Force-cancel from any non-terminal state.

        Raises ``IllegalTransition`` if current state is already terminal.
        """
        if self._state in (AnalysisState.CLOSED, AnalysisState.CANCELLED):
            raise IllegalTransition(
                f"Cannot cancel from terminal state {self._state.value}"
            )
        # FIX L-18: include pair name in cancel log
        pair_name = self._context.pair if self._context else "unknown"
        logger.warning(
            "CANCELLED from %s  pair=%s  reason=%s",
            self._state.value, pair_name, reason,
        )
        self._state = AnalysisState.CANCELLED
        self._cancel_reason = reason
        self._cancel_time = time.time()
        self._history.append((AnalysisState.CANCELLED, self._cancel_time))

    # -- Hysteresis check ---------------------------------------------------

    def should_cancel(self, ctx: SetupContext) -> bool:
        """Return True if *ctx.score* has dropped below hysteresis threshold.

        The threshold is ``HYSTERESIS_CANCEL_SCORE`` (default 3).
        Score *at* threshold is still HOLD — only strictly below cancels.
        """
        return ctx.score < HYSTERESIS_CANCEL_SCORE

    # -- Cooldown -----------------------------------------------------------

    def is_in_cooldown(self) -> bool:
        """Return True if we are within the post-cancellation cooldown."""
        if self._cancel_time is None:
            return False
        elapsed = time.time() - self._cancel_time
        return elapsed < COOLDOWN_MINUTES * 60

    # -- Reset --------------------------------------------------------------

    def reset(self) -> None:
        """Reset machine back to SCANNING (after cooldown or CLOSED)."""
        pair_name = self._context.pair if self._context else "unknown"
        self._state = AnalysisState.SCANNING
        self._context = None
        self._cancel_reason = None
        self._cancel_time = None
        self._history.append((AnalysisState.SCANNING, time.time()))
        logger.info("State machine RESET -> SCANNING  pair=%s", pair_name)
