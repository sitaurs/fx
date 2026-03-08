"""
tests/test_state_machine.py — TDD tests for agent/state_machine.py

Tests cover:
  1. Valid transitions (happy path through all 6 states)
  2. Illegal transitions (e.g. SCANNING→TRIGGERED skipping WATCHING)
  3. Conviction lock (direction immutable once >= WATCHING)
  4. Hysteresis rules (score 5-4 = HOLD, score < 3 = CANCEL)
  5. Cooldown after cancellation (30 min)
  6. Adjustable fields vs locked fields
  7. Hard invalidation → CANCELLED from any state

Reference: masterplan.md §11, §12
"""

from __future__ import annotations

import time
import pytest

from agent.state_machine import (
    AnalysisState,
    SetupContext,
    StateMachine,
    IllegalTransition,
    ConvictionLockViolation,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_context(**overrides) -> SetupContext:
    """Create a minimal SetupContext with sensible defaults."""
    defaults = {
        "pair": "XAUUSD",
        "direction": "sell",
        "strategy_mode": "sniper_confluence",
        "entry_zone_mid": 2350.0,
        "score": 8,
        "confidence": 0.8,
        "htf_bias": "bearish",
    }
    defaults.update(overrides)
    return SetupContext(**defaults)


# ── 1. Valid transitions ──────────────────────────────────────────────────

class TestValidTransitions:
    """Full happy-path lifecycle: SCANNING → … → CLOSED."""

    def test_scanning_to_watching(self):
        sm = StateMachine()
        assert sm.state == AnalysisState.SCANNING
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        assert sm.state == AnalysisState.WATCHING

    def test_watching_to_approaching(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        assert sm.state == AnalysisState.APPROACHING

    def test_approaching_to_triggered(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        sm.transition(AnalysisState.TRIGGERED, ctx)
        assert sm.state == AnalysisState.TRIGGERED

    def test_triggered_to_active(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        sm.transition(AnalysisState.TRIGGERED, ctx)
        sm.transition(AnalysisState.ACTIVE, ctx)
        assert sm.state == AnalysisState.ACTIVE

    def test_active_to_closed(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        sm.transition(AnalysisState.TRIGGERED, ctx)
        sm.transition(AnalysisState.ACTIVE, ctx)
        sm.transition(AnalysisState.CLOSED, ctx)
        assert sm.state == AnalysisState.CLOSED

    def test_full_lifecycle(self):
        sm = StateMachine()
        ctx = _make_context()
        for target in [
            AnalysisState.WATCHING,
            AnalysisState.APPROACHING,
            AnalysisState.TRIGGERED,
            AnalysisState.ACTIVE,
            AnalysisState.CLOSED,
        ]:
            sm.transition(target, ctx)
        assert sm.state == AnalysisState.CLOSED


# ── 2. Illegal transitions ───────────────────────────────────────────────

class TestIllegalTransitions:
    """Transitions that skip states or go backward must raise."""

    def test_scanning_to_triggered_illegal(self):
        sm = StateMachine()
        ctx = _make_context()
        with pytest.raises(IllegalTransition):
            sm.transition(AnalysisState.TRIGGERED, ctx)

    def test_scanning_to_active_illegal(self):
        sm = StateMachine()
        ctx = _make_context()
        with pytest.raises(IllegalTransition):
            sm.transition(AnalysisState.ACTIVE, ctx)

    def test_watching_to_active_illegal(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        with pytest.raises(IllegalTransition):
            sm.transition(AnalysisState.ACTIVE, ctx)

    def test_active_to_watching_illegal(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        sm.transition(AnalysisState.TRIGGERED, ctx)
        sm.transition(AnalysisState.ACTIVE, ctx)
        with pytest.raises(IllegalTransition):
            sm.transition(AnalysisState.WATCHING, ctx)

    def test_closed_to_anything_illegal(self):
        """CLOSED is terminal — no further transitions."""
        sm = StateMachine()
        ctx = _make_context()
        for s in [
            AnalysisState.WATCHING,
            AnalysisState.APPROACHING,
            AnalysisState.TRIGGERED,
            AnalysisState.ACTIVE,
            AnalysisState.CLOSED,
        ]:
            sm.transition(s, ctx)
        with pytest.raises(IllegalTransition):
            sm.transition(AnalysisState.SCANNING, ctx)


# ── 3. Conviction lock ───────────────────────────────────────────────────

class TestConvictionLock:
    """Direction, strategy_mode, htf_bias are locked once >= WATCHING."""

    def test_direction_locked(self):
        sm = StateMachine()
        ctx_sell = _make_context(direction="sell")
        sm.transition(AnalysisState.WATCHING, ctx_sell)
        ctx_buy = _make_context(direction="buy")
        with pytest.raises(ConvictionLockViolation):
            sm.transition(AnalysisState.APPROACHING, ctx_buy)

    def test_htf_bias_locked(self):
        sm = StateMachine()
        ctx = _make_context(htf_bias="bearish")
        sm.transition(AnalysisState.WATCHING, ctx)
        ctx_flip = _make_context(htf_bias="bullish")
        with pytest.raises(ConvictionLockViolation):
            sm.transition(AnalysisState.APPROACHING, ctx_flip)

    def test_strategy_mode_locked(self):
        sm = StateMachine()
        ctx = _make_context(strategy_mode="sniper_confluence")
        sm.transition(AnalysisState.WATCHING, ctx)
        ctx_flip = _make_context(strategy_mode="scalping_channel")
        with pytest.raises(ConvictionLockViolation):
            sm.transition(AnalysisState.APPROACHING, ctx_flip)

    def test_adjustable_fields_allowed(self):
        """Score and confidence MAY change — no violation."""
        sm = StateMachine()
        ctx1 = _make_context(score=8, confidence=0.8)
        sm.transition(AnalysisState.WATCHING, ctx1)
        ctx2 = _make_context(score=6, confidence=0.7)
        sm.transition(AnalysisState.APPROACHING, ctx2)
        assert sm.state == AnalysisState.APPROACHING


# ── 4. Hysteresis rules ──────────────────────────────────────────────────

class TestHysteresis:
    """Score must drop below HYSTERESIS_CANCEL_SCORE (3) to cancel.
    
    Score 5→4 = HOLD (no cancel).  Score 5→2 = CANCEL.
    """

    def test_score_drop_to_4_holds(self):
        """Score 8→4 stays in WATCHING (hysteresis gap)."""
        sm = StateMachine()
        ctx = _make_context(score=8)
        sm.transition(AnalysisState.WATCHING, ctx)
        ctx_lower = _make_context(score=4)
        # Should NOT auto-cancel; still holds.
        assert sm.should_cancel(ctx_lower) is False

    def test_score_drop_to_2_cancels(self):
        """Score 8→2 triggers cancellation."""
        sm = StateMachine()
        ctx = _make_context(score=8)
        sm.transition(AnalysisState.WATCHING, ctx)
        ctx_low = _make_context(score=2)
        assert sm.should_cancel(ctx_low) is True

    def test_score_at_boundary_3_holds(self):
        """Score exactly 3 is still in the hold zone (< 3 cancels)."""
        sm = StateMachine()
        ctx = _make_context(score=8)
        sm.transition(AnalysisState.WATCHING, ctx)
        ctx_boundary = _make_context(score=3)
        assert sm.should_cancel(ctx_boundary) is False


# ── 5. Hard invalidation → CANCELLED ─────────────────────────────────────

class TestCancellation:
    """Any non-terminal state can transition to CANCELLED."""

    def test_watching_to_cancelled(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("H1 CHOCH bearish → bullish")
        assert sm.state == AnalysisState.CANCELLED

    def test_approaching_to_cancelled(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        sm.cancel("Crash candle")
        assert sm.state == AnalysisState.CANCELLED

    def test_active_to_cancelled(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.transition(AnalysisState.APPROACHING, ctx)
        sm.transition(AnalysisState.TRIGGERED, ctx)
        sm.transition(AnalysisState.ACTIVE, ctx)
        sm.cancel("Zone fully mitigated")
        assert sm.state == AnalysisState.CANCELLED

    def test_cancel_records_reason(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        reason = "News major in 5 min"
        sm.cancel(reason)
        assert sm.cancel_reason == reason

    def test_cannot_cancel_from_closed(self):
        sm = StateMachine()
        ctx = _make_context()
        for s in [
            AnalysisState.WATCHING,
            AnalysisState.APPROACHING,
            AnalysisState.TRIGGERED,
            AnalysisState.ACTIVE,
            AnalysisState.CLOSED,
        ]:
            sm.transition(s, ctx)
        with pytest.raises(IllegalTransition):
            sm.cancel("too late")


# ── 6. Cooldown after cancellation ───────────────────────────────────────

class TestCooldown:
    """After CANCELLED, must wait COOLDOWN_MINUTES before new setup."""

    def test_cooldown_active_immediately_after_cancel(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("test")
        assert sm.is_in_cooldown() is True

    def test_cooldown_expires(self):
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("test")
        # Manually override cancel time to simulate 31 min ago
        sm._cancel_time = time.time() - (31 * 60)
        assert sm.is_in_cooldown() is False

    def test_reset_after_cooldown(self):
        """After cooldown, machine can be reset to SCANNING."""
        sm = StateMachine()
        ctx = _make_context()
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("test")
        sm._cancel_time = time.time() - (31 * 60)  # force expire
        sm.reset()
        assert sm.state == AnalysisState.SCANNING


# ── 7. State properties ──────────────────────────────────────────────────

class TestStateProperties:
    """Verify enum values and helper accessors."""

    def test_all_states_exist(self):
        names = {s.value for s in AnalysisState}
        assert names == {
            "SCANNING",
            "WATCHING",
            "APPROACHING",
            "TRIGGERED",
            "ACTIVE",
            "CLOSED",
            "CANCELLED",
        }

    def test_initial_state_is_scanning(self):
        sm = StateMachine()
        assert sm.state == AnalysisState.SCANNING

    def test_context_stored_on_transition(self):
        sm = StateMachine()
        ctx = _make_context(pair="EURUSD")
        sm.transition(AnalysisState.WATCHING, ctx)
        assert sm.context is not None
        assert sm.context.pair == "EURUSD"
