"""
notifier/handler.py — Central notification dispatcher.

Routes state-machine events → WhatsApp messages (and optionally chart).
Keeps a single ``NotificationHandler`` instance for the orchestrator to call.

M-31: Pending-queue events (``on_pending_added``, ``on_pending_expired``)
are dispatched directly by the lifecycle rather than through ``on_state_change``
because they don't correspond to analysis-state transitions.

L-48: All except blocks use explicit exception types (``OSError``,
``Exception as exc``).  No bare ``except:`` is present.

L-52: Chart generation failure gracefully degrades to text-only
(see ``_send_triggered``).

Reference: masterplan.md §22.4, §22.6
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

from notifier.whatsapp import WhatsAppNotifier, wa_notifier
from notifier.templates import (
    format_triggered_alert,
    format_cancelled_alert,
    format_sl_plus_alert,
    format_trade_closed,
    format_daily_summary,
    format_error_alert,
    format_watching_update,
    format_trade_opened,
    format_pending_added,
    format_pending_expired,
    format_drawdown_halt,
)
from charts.screenshot import ChartScreenshotGenerator, chart_generator
from schemas.plan import TradingPlan

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Central hub that turns system events into WhatsApp messages."""

    def __init__(
        self,
        notifier: WhatsAppNotifier | None = None,
        chart_gen: ChartScreenshotGenerator | None = None,
    ) -> None:
        self._wa = notifier or wa_notifier
        self._chart = chart_gen or chart_generator

    # ------------------------------------------------------------------
    # State-transition events
    # ------------------------------------------------------------------

    async def on_state_change(
        self,
        old_state: str,
        new_state: str,
        *,
        plan: Optional[TradingPlan] = None,
        cancel_reason: Optional[str] = None,
        ohlcv: Optional[pd.DataFrame] = None,
    ) -> None:
        """Dispatch notification based on state transition.

        Called by the orchestrator whenever the state machine advances.
        """
        if new_state == "TRIGGERED" and plan is not None:
            await self._send_triggered(plan, ohlcv)
        elif new_state == "CANCELLED":
            pair = plan.pair if plan else "UNKNOWN"
            reason = cancel_reason or "Unknown"
            msg = format_cancelled_alert(pair, reason)
            await self._wa.send_message(msg)
        # Other transitions (WATCHING, APPROACHING, ACTIVE) could send
        # lighter update messages – extend here as needed.

    async def _send_triggered(
        self,
        plan: TradingPlan,
        ohlcv: Optional[pd.DataFrame],
    ) -> None:
        """Send TRIGGERED alert, optionally with chart screenshot."""
        caption = format_triggered_alert(plan)

        if ohlcv is not None and len(ohlcv) >= 5:
            try:
                s = plan.primary_setup
                path = self._chart.generate_entry_chart(
                    ohlcv=ohlcv,
                    pair=plan.pair,
                    direction=s.direction.value,
                    entry_zone=(s.entry_zone_low, s.entry_zone_high),
                    stop_loss=s.stop_loss,
                    take_profit_1=s.take_profit_1,
                    take_profit_2=s.take_profit_2,
                )
                chart_url = ChartScreenshotGenerator.to_base64(path)
                await self._wa.send_image(chart_url, caption)
                # Clean up temp file
                try:
                    os.remove(path)
                except OSError:
                    pass
                return
            except Exception as exc:
                logger.warning("Chart generation failed, sending text: %s", exc)

        # Fallback: text-only message
        await self._wa.send_message(caption)

    # ------------------------------------------------------------------
    # Trade-management events
    # ------------------------------------------------------------------

    async def on_sl_moved(
        self, pair: str, old_sl: float, new_sl: float,
    ) -> None:
        msg = format_sl_plus_alert(pair, old_sl, new_sl)
        await self._wa.send_message(msg)

    async def on_trade_closed(
        self,
        pair: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pips: float,
        duration_minutes: int,
        strategy_mode: str,
        lesson: str = "N/A",
    ) -> None:
        msg = format_trade_closed(
            pair, direction, entry_price, exit_price,
            pips, duration_minutes, strategy_mode, lesson,
        )
        await self._wa.send_message(msg)

    # ------------------------------------------------------------------
    # Daily / error events
    # ------------------------------------------------------------------

    async def on_daily_end(
        self,
        date_str: str,
        total_scans: int,
        setups_found: int,
        trades_taken: int,
        trade_lines: list[str],
        total_pips: float,
        win_rate_30d: float,
        expectancy_30d: float,
    ) -> None:
        msg = format_daily_summary(
            date_str, total_scans, setups_found, trades_taken,
            trade_lines, total_pips, win_rate_30d, expectancy_30d,
        )
        await self._wa.send_message(msg)

    async def on_error(self, context: str, error: Exception) -> None:
        msg = format_error_alert(context, str(error))
        await self._wa.send_message(msg)

    # ------------------------------------------------------------------
    # Phase 4 additions — Trade lifecycle events
    # ------------------------------------------------------------------

    async def on_trade_opened(
        self,
        pair: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float | None,
        lot_size: float,
        risk_usd: float,
    ) -> None:
        """Notify when a trade is actually opened at market price."""
        msg = format_trade_opened(
            pair, direction, entry_price,
            stop_loss, take_profit_1, take_profit_2,
            lot_size, risk_usd,
        )
        await self._wa.send_message(msg)

    async def on_pending_added(
        self,
        pair: str,
        direction: str,
        zone_low: float,
        zone_high: float,
        rec_entry: float,
        ttl_hours: float,
    ) -> None:
        """Notify when a setup is queued to pending."""
        msg = format_pending_added(pair, direction, zone_low, zone_high, rec_entry, ttl_hours)
        await self._wa.send_message(msg)

    async def on_pending_expired(
        self,
        pair: str,
        direction: str,
        ttl_hours: float,
    ) -> None:
        """Notify when a pending setup times out without executing."""
        msg = format_pending_expired(pair, direction, ttl_hours)
        await self._wa.send_message(msg)

    async def on_drawdown_halt(
        self,
        halt_type: str,
        drawdown_pct: float,
        balance: float,
        high_water_mark: float,
    ) -> None:
        """Notify when drawdown guard halts trading."""
        msg = format_drawdown_halt(halt_type, drawdown_pct, balance, high_water_mark)
        await self._wa.send_message(msg)


# Module-level singleton
notification_handler = NotificationHandler()
