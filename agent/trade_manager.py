"""
agent/trade_manager.py â€” Post-entry trade management (SL+, trailing, monitoring).

Implements the masterplan §13 checklist:
    - SL+ to breakeven after 1Ã—risk profit
    - Trail SL after 1.5Ã—risk profit
    - Partial close at TP1
    - Never widen SL
    - Close rules (CHOCH, news, marubozu)

Usage::

    mgr = TradeManager(trade)
    action = mgr.evaluate(current_price, atr, structure_ok=True)
    # action.type: "HOLD" | "SL_PLUS_BE" | "TRAIL" | "PARTIAL_TP1" | "FULL_CLOSE" | "CLOSE_MANUAL"

Reference: masterplan.md §13 (Post-Open Trade Management)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from config.settings import (
    BREAKEVEN_TRIGGER_RR,
    TRAIL_TRIGGER_RR,
    SL_ATR_MULTIPLIER,
    PAIR_POINT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Result
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    HOLD = "HOLD"
    SL_PLUS_BE = "SL_PLUS_BE"        # Move SL to breakeven
    TRAIL = "TRAIL"                    # Trail SL to last swing
    PARTIAL_TP1 = "PARTIAL_TP1"        # Partial close at TP1
    FULL_CLOSE = "FULL_CLOSE"          # Full close (TP2 hit)
    CLOSE_MANUAL = "CLOSE_MANUAL"      # Close due to invalidation
    SL_HIT = "SL_HIT"                 # SL was hit


@dataclass
class TradeAction:
    """Recommended action from trade management evaluation."""

    action: ActionType
    reason: str
    new_sl: Optional[float] = None    # For SL_PLUS_BE / TRAIL
    close_percent: float = 0.0        # 0.5 for partial, 1.0 for full
    urgency: str = "normal"           # "normal" | "high" | "critical"


# ---------------------------------------------------------------------------
# Active trade state
# ---------------------------------------------------------------------------

@dataclass
class ActiveTrade:
    """In-memory representation of an active (open) trade."""

    trade_id: str
    pair: str
    direction: str               # "buy" | "sell"
    entry_price: float
    stop_loss: float             # Current SL (may be moved)
    take_profit_1: float
    take_profit_2: Optional[float] = None

    # Management state
    original_sl: float = 0.0     # Never moves â€” for risk calculation
    sl_moved_to_be: bool = False
    trail_active: bool = False
    partial_closed: bool = False
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Position sizing & realized accounting (locked at open)
    lot_size: float = 0.0
    risk_amount: float = 0.0
    remaining_size: float = 1.0
    realized_pnl: float = 0.0

    # Context data from setup (for PostMortem â€” FIX F4-08/F4-09)
    strategy_mode: str = ""
    confluence_score: int = 0
    voting_confidence: float = 0.0
    entry_zone_type: str = ""
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    recommended_entry: Optional[float] = None
    htf_bias: str = ""
    last_revalidation_at: Optional[datetime] = None
    last_revalidation_note: str = ""

    def __post_init__(self):
        if self.original_sl == 0.0:
            self.original_sl = self.stop_loss

    @property
    def initial_risk(self) -> float:
        """Absolute risk in price terms (entry to original SL)."""
        return abs(self.entry_price - self.original_sl)

    def floating_pnl(self, current_price: float) -> float:
        """Floating P/L in price terms (positive = profit)."""
        if self.direction == "buy":
            return current_price - self.entry_price
        return self.entry_price - current_price

    def floating_pips(self, current_price: float) -> float:
        """Floating P/L in pips."""
        point = PAIR_POINT.get(self.pair, 0.0001)
        return self.floating_pnl(current_price) / point

    def distance_to_tp1(self, current_price: float) -> float:
        """Distance to TP1 in price terms."""
        if self.direction == "buy":
            return self.take_profit_1 - current_price
        return current_price - self.take_profit_1

    def distance_to_sl(self, current_price: float) -> float:
        """Distance to current SL in price terms (positive = safe)."""
        if self.direction == "buy":
            return current_price - self.stop_loss
        return self.stop_loss - current_price

    def rr_current(self, current_price: float) -> float:
        """Current R:R ratio (positive = in profit territory)."""
        risk = self.initial_risk
        if risk <= 0:
            return 0.0
        return self.floating_pnl(current_price) / risk


# ---------------------------------------------------------------------------
# Trade Manager
# ---------------------------------------------------------------------------

class TradeManager:
    """Manages SL+, trailing stop, and close decisions for an active trade.

    Core rules (masterplan §13):
        1. Floating profit >= 1.0Ã—risk â†’ SL+ to breakeven
        2. Floating profit >= 1.5Ã—risk â†’ trail SL to last swing Â± 0.5Ã—ATR
        3. Price within 0.5Ã—ATR of TP1 â†’ partial close 50%
        4. TP2 hit â†’ full close
        5. SL hit â†’ CLOSED + post-mortem
        6. CHOCH on H1 against direction â†’ CLOSE_MANUAL
        7. News in 5 min â†’ tighten SL or close

    NEVER:
        - Widen SL (SL can only move in profit direction)
        - Remove SL
        - Close on "feeling" â€” only hard evidence
    """

    def __init__(
        self,
        trade: ActiveTrade,
        be_trigger_rr: float = BREAKEVEN_TRIGGER_RR,
        trail_trigger_rr: float = TRAIL_TRIGGER_RR,
        max_history: int = 500,
    ):
        self.trade = trade
        self.be_trigger_rr = be_trigger_rr
        self.trail_trigger_rr = trail_trigger_rr
        self._action_history: list[TradeAction] = []
        self._max_history = max_history  # FIX §7.9: cap history growth

    def _record_action(self, action: TradeAction) -> None:
        """Append action and trim if over capacity (FIX §7.9)."""
        self._action_history.append(action)
        if len(self._action_history) > self._max_history:
            self._action_history = self._action_history[-self._max_history:]

    @property
    def history(self) -> list[TradeAction]:
        return list(self._action_history)

    # -- Main evaluation entry point ----------------------------------------

    def evaluate(
        self,
        current_price: float,
        atr: float,
        *,
        structure_ok: bool = True,
        news_imminent: bool = False,
        last_swing_against: Optional[float] = None,
    ) -> TradeAction:
        """Evaluate current market state and return recommended action.

        Args:
            current_price: Current bid/ask price.
            atr: Current ATR value for the trade's timeframe.
            structure_ok: True if H1 structure still supports direction.
            news_imminent: True if major news within 5 minutes.
            last_swing_against: Price of last swing against trade direction
                                (for trailing stop calculation).

        Returns:
            TradeAction with recommended action.
        """
        trade = self.trade
        rr = trade.rr_current(current_price)
        dist_tp1 = trade.distance_to_tp1(current_price)
        dist_sl = trade.distance_to_sl(current_price)

        # 1. Check if SL was hit
        if dist_sl <= 0:
            action = TradeAction(
                action=ActionType.SL_HIT,
                reason="Stop loss hit",
                urgency="critical",
            )
            self._record_action(action)
            return action

        # 2. Check structure break (CHOCH against)
        if not structure_ok:
            action = TradeAction(
                action=ActionType.CLOSE_MANUAL,
                reason="H1 structure break (CHOCH) against trade direction",
                close_percent=1.0,
                urgency="critical",
            )
            self._record_action(action)
            return action

        # 3. Check news imminent
        if news_imminent:
            # Tighten SL to break-even if possible, otherwise close
            if rr >= 0.5:
                be_level = self._breakeven_level(atr)
                action = TradeAction(
                    action=ActionType.SL_PLUS_BE,
                    reason="News imminent â€” SL tightened to breakeven",
                    new_sl=be_level,
                    urgency="high",
                )
            else:
                action = TradeAction(
                    action=ActionType.CLOSE_MANUAL,
                    reason="News imminent â€” closing position (not enough profit for BE)",
                    close_percent=1.0,
                    urgency="high",
                )
            self._record_action(action)
            return action

        # 4. Check TP2 hit (full close)
        if trade.take_profit_2 is not None:
            dist_tp2 = (
                (trade.take_profit_2 - current_price)
                if trade.direction == "buy"
                else (current_price - trade.take_profit_2)
            )
            if dist_tp2 <= 0:
                action = TradeAction(
                    action=ActionType.FULL_CLOSE,
                    reason="TP2 hit â€” full close",
                    close_percent=1.0,
                    urgency="high",
                )
                self._record_action(action)
                return action

        # 5. Check TP1 hit (partial close)
        # NOTE:
        # We only execute partial on actual TP1 touch/cross to avoid
        # "phantom" realized PnL while price is merely near TP1.
        if not trade.partial_closed and dist_tp1 <= 0:
            action = TradeAction(
                action=ActionType.PARTIAL_TP1,
                reason="TP1 hit â€” partial close 50%, trail remainder",
                close_percent=0.5,
                urgency="high",
            )
            self._record_action(action)
            return action

        # 6. Trail SL (if profit >= 1.5Ã—risk) â€” FIX F4-05: progressive trailing
        if rr >= self.trail_trigger_rr:
            new_sl = self._trail_sl(current_price, atr, last_swing_against)
            if new_sl is not None:
                action = TradeAction(
                    action=ActionType.TRAIL,
                    reason=f"Profit >= {self.trail_trigger_rr}Ã—risk â€” trailing SL",
                    new_sl=new_sl,
                )
                self._record_action(action)
                return action

        # 7. SL+ to breakeven (if profit >= 1.0Ã—risk)
        if rr >= self.be_trigger_rr and not trade.sl_moved_to_be:
            be_level = self._breakeven_level(atr)
            action = TradeAction(
                action=ActionType.SL_PLUS_BE,
                reason=f"Profit >= {self.be_trigger_rr}Ã—risk â€” SL+ to breakeven",
                new_sl=be_level,
            )
            self._record_action(action)
            return action

        # 8. Default: HOLD
        point = PAIR_POINT.get(trade.pair, 0.0001)
        pips_float = trade.floating_pnl(current_price) / point
        action = TradeAction(
            action=ActionType.HOLD,
            reason=(
                f"Hold â€” floating {pips_float:+.1f} pips, "
                f"RR={rr:.2f}, dist_TP1={dist_tp1/point:.0f}p, "
                f"dist_SL={dist_sl/point:.0f}p"
            ),
        )
        return action

    # -- SL calculation helpers ---------------------------------------------

    def _breakeven_level(self, atr: float) -> float:
        """Calculate breakeven SL level = entry Â± spread buffer."""
        trade = self.trade
        # 2 pips buffer + approximate spread
        point = PAIR_POINT.get(trade.pair, 0.0001)
        buffer = 2 * point
        if trade.direction == "buy":
            return trade.entry_price + buffer
        return trade.entry_price - buffer

    def _trail_sl(
        self, current_price: float, atr: float, last_swing_against: Optional[float]
    ) -> Optional[float]:
        """Calculate trailing SL.

        With swing: last_swing_against Â± 0.5Ã—ATR
        Without swing (fallback): current_price âˆ“ 1Ã—ATR (progressive)

        FIX F4-05: Uses current_price for progressive trailing instead
        of static entry_price offset. Also allows re-evaluation when
        trail_active is already True.

        Returns None if new SL would be worse than current (violates
        "never widen SL" rule).
        """
        trade = self.trade
        offset = 0.5 * atr

        if last_swing_against is not None:
            if trade.direction == "buy":
                new_sl = last_swing_against - offset
            else:
                new_sl = last_swing_against + offset
        else:
            # FIX F4-05: Progressive fallback â€” trail from current price
            if trade.direction == "buy":
                new_sl = current_price - atr
            else:
                new_sl = current_price + atr

        # NEVER widen SL â€” only tighten
        if trade.direction == "buy":
            if new_sl <= trade.stop_loss:
                return None  # Would widen â€” reject
        else:
            if new_sl >= trade.stop_loss:
                return None  # Would widen â€” reject

        return round(new_sl, 5)

    # -- Apply actions ------------------------------------------------------

    def apply_action(self, action: TradeAction) -> None:
        """Apply an action to update trade state.

        Call this after the user confirms the action should be taken.
        """
        trade = self.trade

        if action.action == ActionType.SL_PLUS_BE and action.new_sl is not None:
            trade.stop_loss = action.new_sl
            trade.sl_moved_to_be = True
            logger.info(
                "%s: SL moved to BE at %.5f", trade.trade_id, action.new_sl
            )

        elif action.action == ActionType.TRAIL and action.new_sl is not None:
            trade.stop_loss = action.new_sl
            trade.trail_active = True
            logger.info(
                "%s: SL trailed to %.5f", trade.trade_id, action.new_sl
            )

        elif action.action == ActionType.PARTIAL_TP1:
            trade.partial_closed = True
            logger.info(
                "%s: Partial close %.0f%% at TP1",
                trade.trade_id,
                action.close_percent * 100,
            )

        elif action.action in (ActionType.FULL_CLOSE, ActionType.CLOSE_MANUAL, ActionType.SL_HIT):
            logger.info(
                "%s: Trade closed â€” %s",
                trade.trade_id,
                action.reason,
            )


# ---------------------------------------------------------------------------
# Monitoring checklist (masterplan §13)
# ---------------------------------------------------------------------------

@dataclass
class MonitoringReport:
    """Snapshot of all monitoring checks for an active trade."""

    trade_id: str
    current_price: float
    floating_pips: float
    floating_pnl_price: float
    distance_to_tp1_pips: float
    distance_to_sl_pips: float
    rr_current: float
    structure_ok: bool
    rejection_detected: bool
    momentum_aligned: bool
    news_within_30m: bool
    sl_plus_ready: bool
    recommended_action: TradeAction


def generate_monitoring_report(
    trade: ActiveTrade,
    current_price: float,
    atr: float,
    *,
    structure_ok: bool = True,
    rejection_detected: bool = False,
    momentum_aligned: bool = True,
    news_within_30m: bool = False,
    last_swing_against: Optional[float] = None,
) -> MonitoringReport:
    """Generate a full monitoring report for the trade.

    Combines all checks from masterplan §13 into a single report.
    """
    mgr = TradeManager(trade)
    action = mgr.evaluate(
        current_price,
        atr,
        structure_ok=structure_ok,
        news_imminent=news_within_30m,
        last_swing_against=last_swing_against,
    )

    point = PAIR_POINT.get(trade.pair, 0.0001)

    return MonitoringReport(
        trade_id=trade.trade_id,
        current_price=current_price,
        floating_pips=trade.floating_pips(current_price),
        floating_pnl_price=trade.floating_pnl(current_price),
        distance_to_tp1_pips=trade.distance_to_tp1(current_price) / point,
        distance_to_sl_pips=trade.distance_to_sl(current_price) / point,
        rr_current=trade.rr_current(current_price),
        structure_ok=structure_ok,
        rejection_detected=rejection_detected,
        momentum_aligned=momentum_aligned,
        news_within_30m=news_within_30m,
        sl_plus_ready=(
            trade.rr_current(current_price) >= BREAKEVEN_TRIGGER_RR
            and not trade.sl_moved_to_be
        ),
        recommended_action=action,
    )
