"""
agent/demo_tracker.py — Paper-trading tracker & graduation system.

Tracks virtual balance, records demo trades, checks graduation criteria
before allowing switch from DEMO → REAL mode.

Graduation criteria (masterplan §23):
    - Minimum 30 trades
    - Win-rate >= 60%
    - Average expectancy >= +5 pips
    - Max daily drawdown < 5%
    - Max total drawdown < 15%

Usage::

    tracker = DemoTracker()
    tracker.record_trade(trade_record)
    grad = tracker.check_graduation()
    if grad["ready"]:
        mode_mgr.switch_to_real()

Reference: masterplan.md §23 (Demo Mode & Graduation)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from config.settings import TRADING_MODE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MaxDrawdownExceeded(Exception):
    """Raised when drawdown exceeds configured limit."""


class GraduationNotReady(Exception):
    """Raised when attempting to switch to REAL before graduation."""


# ---------------------------------------------------------------------------
# Trade record (lightweight, in-memory)
# ---------------------------------------------------------------------------

@dataclass
class DemoTradeRecord:
    """Minimal trade record for demo tracking.

    Fields map to the DB Trade model but kept lightweight for in-memory use.
    """
    trade_id: str
    pair: str
    direction: str           # "buy" | "sell"
    entry_price: float
    stop_loss: float
    take_profit_1: float
    exit_price: float
    result: str              # "TP1_HIT", "SL_HIT", etc.
    pips: float = 0.0
    rr_achieved: float = 0.0
    duration_minutes: int = 0
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Filled by DemoTracker
    demo_pnl: float = 0.0
    demo_balance_after: float = 0.0


# ---------------------------------------------------------------------------
# DemoTracker
# ---------------------------------------------------------------------------

class DemoTracker:
    """Track virtual trading performance in demo mode.

    Maintains a virtual balance, records trades with P/L,
    and checks graduation criteria for DEMO → REAL switch.

    .. note:: D-17 / TODO: Not yet integrated into production_lifecycle.py.
       When integrated, record_trade() should be called from lifecycle
       trade-close handler for DEMO mode trades.
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        risk_per_trade: float = 0.01,         # 1%
        graduation_min_trades: int = 30,
        graduation_min_winrate: float = 0.60,
        graduation_min_expectancy: float = 5.0,  # pips
        max_daily_drawdown: float = 0.05,     # 5%
        max_total_drawdown: float = 0.15,     # 15%
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.graduation_min_trades = graduation_min_trades
        self.graduation_min_winrate = graduation_min_winrate
        self.graduation_min_expectancy = graduation_min_expectancy
        self.max_daily_drawdown = max_daily_drawdown
        self.max_total_drawdown = max_total_drawdown

        self.high_water_mark: float = initial_balance
        self.daily_start_balance: float = initial_balance
        self.trades: list[DemoTradeRecord] = []

    # -- Core ---------------------------------------------------------------

    def record_trade(self, trade: DemoTradeRecord) -> DemoTradeRecord:
        """Record a completed trade and update virtual balance.

        The P/L is calculated based on fixed risk (1% of balance)
        scaled by the achieved risk-reward ratio.

        Returns the trade with ``demo_pnl`` and ``demo_balance_after`` set.

        Raises ``MaxDrawdownExceeded`` if total or daily drawdown limits
        are breached.
        """
        risk_amount = self.balance * self.risk_per_trade

        if trade.result in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT"):
            pnl = risk_amount * max(trade.rr_achieved, 0.1)
        elif trade.result == "BE_HIT":
            pnl = 0.0
        elif trade.result == "MANUAL_CLOSE":
            # Can be win or loss — use pips sign
            pnl = risk_amount * trade.rr_achieved if trade.rr_achieved else 0.0
        else:
            # SL_HIT, CANCELLED → full risk loss
            pnl = -risk_amount

        self.balance += pnl
        trade.demo_pnl = round(pnl, 2)
        trade.demo_balance_after = round(self.balance, 2)

        if self.balance > self.high_water_mark:
            self.high_water_mark = self.balance

        self.trades.append(trade)

        logger.info(
            "DEMO trade %s: %s %.2f pips, PnL=$%.2f, balance=$%.2f",
            trade.trade_id,
            trade.result,
            trade.pips,
            pnl,
            self.balance,
        )

        # Check drawdown limits
        self._check_drawdown()

        return trade

    def _check_drawdown(self) -> None:
        """Raise MaxDrawdownExceeded if limits breached."""
        # Total drawdown from high water mark
        if self.high_water_mark > 0:
            total_dd = (self.high_water_mark - self.balance) / self.high_water_mark
            if total_dd >= self.max_total_drawdown:
                raise MaxDrawdownExceeded(
                    f"Total drawdown {total_dd:.1%} exceeds limit "
                    f"{self.max_total_drawdown:.0%}. "
                    f"Balance=${self.balance:.2f}, HWM=${self.high_water_mark:.2f}"
                )

        # Daily drawdown
        if self.daily_start_balance > 0:
            daily_dd = (
                (self.daily_start_balance - self.balance) / self.daily_start_balance
            )
            if daily_dd >= self.max_daily_drawdown:
                raise MaxDrawdownExceeded(
                    f"Daily drawdown {daily_dd:.1%} exceeds limit "
                    f"{self.max_daily_drawdown:.0%}. "
                    f"Today start=${self.daily_start_balance:.2f}, "
                    f"now=${self.balance:.2f}"
                )

    def reset_daily(self) -> None:
        """Call at start of trading day to reset daily drawdown tracking."""
        self.daily_start_balance = self.balance
        logger.info("Daily balance reset to $%.2f", self.balance)

    # -- Graduation ---------------------------------------------------------

    def check_graduation(self) -> dict:
        """Check if demo performance meets graduation criteria.

        Returns::

            {"ready": True/False, "reason": str, "stats": {...}}
        """
        total = len(self.trades)

        if total < self.graduation_min_trades:
            return {
                "ready": False,
                "reason": (
                    f"Need {self.graduation_min_trades} trades, "
                    f"have {total}"
                ),
                "stats": self._compute_stats(),
            }

        stats = self._compute_stats()

        if stats["winrate"] < self.graduation_min_winrate:
            return {
                "ready": False,
                "reason": (
                    f"Win rate {stats['winrate']:.1%} below "
                    f"{self.graduation_min_winrate:.0%}"
                ),
                "stats": stats,
            }

        if stats["avg_pips"] < self.graduation_min_expectancy:
            return {
                "ready": False,
                "reason": (
                    f"Avg expectancy {stats['avg_pips']:.1f} pips below "
                    f"{self.graduation_min_expectancy:.1f}"
                ),
                "stats": stats,
            }

        return {
            "ready": True,
            "reason": "All graduation criteria met!",
            "stats": stats,
        }

    def _compute_stats(self) -> dict:
        """Compute trading statistics."""
        if not self.trades:
            return {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "winrate": 0.0,
                "avg_pips": 0.0,
                "total_pips": 0.0,
                "balance": self.balance,
                "profit": 0.0,
                "max_drawdown": 0.0,
            }

        # M-38: TRAIL_PROFIT counted as win (same logic as H-13 in repository)
        wins = sum(
            1
            for t in self.trades
            if t.result in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT")
            or (t.result in ("MANUAL_CLOSE", "BE_HIT") and t.pips > 0)
        )
        losses = sum(1 for t in self.trades if t.result == "SL_HIT")
        total_pips = sum(t.pips for t in self.trades)

        # Track running max drawdown
        peak = self.initial_balance
        max_dd = 0.0
        running_bal = self.initial_balance
        for t in self.trades:
            running_bal += t.demo_pnl
            if running_bal > peak:
                peak = running_bal
            dd = (peak - running_bal) / peak if peak else 0.0
            if dd > max_dd:
                max_dd = dd

        return {
            "total": len(self.trades),
            "wins": wins,
            "losses": losses,
            "winrate": wins / len(self.trades) if self.trades else 0.0,
            "avg_pips": total_pips / len(self.trades) if self.trades else 0.0,
            "total_pips": total_pips,
            "balance": self.balance,
            "profit": self.balance - self.initial_balance,
            "max_drawdown": max_dd,
        }

    # -- Serialization (for persistence) ------------------------------------

    def to_dict(self) -> dict:
        """Serialize tracker state for DB persistence."""
        return {
            "initial_balance": self.initial_balance,
            "balance": self.balance,
            "high_water_mark": self.high_water_mark,
            "daily_start_balance": self.daily_start_balance,
            "trade_count": len(self.trades),
            "stats": self._compute_stats(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DemoTracker:
        """Restore tracker from persisted state.

        CON-26: Individual trades are NOT restored — only aggregate state
        (balance, HWM, daily start).  This means ``check_graduation()``
        and ``_compute_stats()`` will report ``total=0`` until new trades
        are recorded.  To fully restore, re-load trades from the database
        and call ``record_trade()`` for each (not yet implemented).
        """
        tracker = cls(initial_balance=data.get("initial_balance", 10_000.0))
        tracker.balance = data.get("balance", tracker.initial_balance)
        tracker.high_water_mark = data.get("high_water_mark", tracker.balance)
        tracker.daily_start_balance = data.get(
            "daily_start_balance", tracker.balance
        )
        return tracker


# ---------------------------------------------------------------------------
# ModeManager — controls DEMO↔REAL switching
# ---------------------------------------------------------------------------

class ModeManager:
    """Manage demo/real mode transitions.

    Wraps a DemoTracker and ensures graduation criteria are met
    before allowing the switch to REAL mode.
    """

    def __init__(
        self,
        tracker: DemoTracker | None = None,
        auto_graduate: bool = False,
    ):
        self.mode: str = TRADING_MODE  # from .env
        self.tracker = tracker or DemoTracker()
        self.auto_graduate = auto_graduate

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def is_real(self) -> bool:
        return self.mode == "real"

    def on_trade_closed(self, trade: DemoTradeRecord) -> dict | None:
        """Handle a closed trade. Returns graduation info if ready.

        In DEMO mode: record the trade and optionally check graduation.
        In REAL mode: no virtual tracking needed.
        """
        if self.mode != "demo":
            return None

        # H-18: Catch MaxDrawdownExceeded so it doesn't crash the caller.
        try:
            self.tracker.record_trade(trade)
        except MaxDrawdownExceeded as exc:
            logger.error(
                "🚨 MAX DRAWDOWN EXCEEDED: %s — halting demo trading", exc
            )
            return {
                "ready": False,
                "reason": f"Max drawdown exceeded: {exc}",
                "stats": self.tracker._compute_stats(),
                "halted": True,
            }

        if self.auto_graduate:
            graduation = self.tracker.check_graduation()
            if graduation["ready"]:
                logger.info(
                    "🎓 GRADUATION READY: %s", graduation["stats"]
                )
                return graduation
        return None

    def switch_to_real(self, repository: Any = None) -> dict:
        """Switch from DEMO to REAL mode.

        If *repository* is provided the mode is also persisted to the
        database settings_kv table (M-37).

        Raises ``GraduationNotReady`` if criteria not met.
        Returns graduation stats on success.
        """
        graduation = self.tracker.check_graduation()
        if not graduation["ready"]:
            raise GraduationNotReady(
                f"Cannot switch to real: {graduation['reason']}"
            )
        self.mode = "real"
        if repository is not None:
            # M-37: Fire-and-forget coroutine — caller should await if needed.
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(repository.set_setting("trading_mode", "real"))
            except RuntimeError:
                pass  # No event loop — skip persistence
        logger.info("🚀 MODE SWITCHED: DEMO → REAL")
        return graduation

    def force_real(self) -> None:
        """Force switch to real (manual override, bypasses checks)."""
        self.mode = "real"
        logger.warning("⚠️ MODE FORCE-SWITCHED to REAL (graduation bypassed)")

    def force_demo(self) -> None:
        """Switch back to demo mode."""
        self.mode = "demo"
        logger.info("MODE SWITCHED: back to DEMO")
