"""
notifier/templates.py — WhatsApp message formatting templates.

Each function takes structured data and returns a ready-to-send
WhatsApp-formatted string (bold = ``*text*``, italic = ``_text_``).

Reference: masterplan.md §22.3
"""

from __future__ import annotations

from typing import Optional

from config.strategy_rules import MAX_POSSIBLE_SCORE  # M-29: dynamic score cap
from schemas.plan import SetupCandidate, TradingPlan

# L-51: All user-facing strings use plain f-strings. When i18n is needed,
# extract them into a resource dict keyed by template name and locale.
# See: https://docs.python.org/3/library/gettext.html


# ---------------------------------------------------------------------------
# Alert templates
# ---------------------------------------------------------------------------

def format_triggered_alert(plan: TradingPlan) -> str:
    """Format a TRIGGERED entry alert."""
    s = plan.primary_setup
    tp2_line = f"\U0001F3AF TP2: {s.take_profit_2}" if s.take_profit_2 else ""
    rec_line = f"\U0001F4CC Rec Entry: {s.recommended_entry}" if s.recommended_entry else ""
    return (
        f"\u26A1 *ENTRY ALERT*\n\n"
        f"*{plan.pair}* — {s.direction.value.upper()}\n\n"
        f"\U0001F4CD Entry Zone: {s.entry_zone_low} – {s.entry_zone_high}\n"
        f"{rec_line}\n"
        f"\U0001F6D1 Stop Loss: {s.stop_loss}\n"
        f"\U0001F3AF TP1: {s.take_profit_1}\n"
        f"{tp2_line}\n"
        f"\n"
        f"\U0001F4CA Score: {s.confluence_score}/{MAX_POSSIBLE_SCORE}\n"
        f"\U0001F3B2 Confidence: {int(plan.confidence * 100)}%\n"
        f"\u23F0 Valid Until: {plan.valid_until}\n\n"
        f"Trigger: {s.trigger_condition}\n\n"
        f"\u26A0\uFE0F {s.invalidation}"
    ).strip()


def format_watching_update(
    pair: str,
    update_num: int,
    state: str,
    current_price: float,
    setup_direction: str,
    entry_zone: str,
    status: str,
    changes: str,
    action: str,
    reason: str,
) -> str:
    """Format a WATCHING / APPROACHING state-update message."""
    emoji = {
        "VALID": "\u2705",
        "WEAKENED": "\u26A0\uFE0F",
        "INVALIDATED": "\u274C",
    }.get(status, "\u2753")
    return (
        f"UPDATE #{update_num} — *{pair}* {state}\n\n"
        f"ORIGINAL SETUP: {setup_direction} di {entry_zone} (LOCKED \u2705)\n"
        f"CURRENT PRICE: {current_price}\n"
        f"SETUP STATUS: {status} {emoji}\n\n"
        f"PERUBAHAN:\n{changes}\n\n"
        f"AKSI: *{action}*\n"
        f"ALASAN: {reason}"
    )


def format_sl_plus_alert(pair: str, old_sl: float, new_sl: float) -> str:
    """Format SL+ (stop-loss moved to break-even / profit)."""
    return (
        f"\U0001F504 *SL MOVED*\n\n"
        f"*{pair}*\n"
        f"Old SL: {old_sl}\n"
        f"New SL: {new_sl} \u2705\n\n"
        f"Position now risk-free!"
    )


def format_cancelled_alert(pair: str, reason: str) -> str:
    """Format setup CANCELLED notification."""
    return (
        f"\u274C *CANCELLED* — {pair}\n\n"
        f"Reason: {reason}\n"
        f"Cool-down: 30 minutes"
    )


def format_trade_closed(
    pair: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pips: float,
    duration_minutes: int,
    strategy_mode: str,
    lesson: str = "N/A",
) -> str:
    """Format trade-closed summary."""
    emoji = "\u2705" if pips > 0 else "\u274C"
    sign = "+" if pips > 0 else ""
    return (
        f"{emoji} *TRADE CLOSED*\n\n"
        f"*{pair}* — {direction.upper()}\n"
        f"Entry: {entry_price}\n"
        f"Exit: {exit_price}\n"
        f"Result: {sign}{pips} pips\n\n"
        f"Duration: {duration_minutes} minutes\n"
        f"Strategy: {strategy_mode}\n\n"
        f"*Post-Mortem:*\n{lesson}"
    )


def format_daily_summary(
    date_str: str,
    total_scans: int,
    setups_found: int,
    trades_taken: int,
    trade_lines: list[str],
    total_pips: float,
    win_rate_30d: float,
    expectancy_30d: float,
) -> str:
    """Format end-of-day daily summary."""
    trades_block = "\n".join(trade_lines) if trade_lines else "None"
    sign = "+" if total_pips > 0 else ""
    return (
        f"\U0001F4CA *DAILY SUMMARY* — {date_str}\n\n"
        f"Scans: {total_scans}\n"
        f"Setups Found: {setups_found}\n"
        f"Trades Taken: {trades_taken}\n\n"
        f"Results:\n{trades_block}\n\n"
        f"Total P/L: {sign}{total_pips} pips\n\n"
        f"Rolling Stats (30d):\n"
        f"Win Rate: {win_rate_30d:.1%}\n"
        f"Expectancy: {expectancy_30d:+.1f} pips/trade"
    )


def format_error_alert(context: str, error: str) -> str:
    """Format admin error notification."""
    return (
        f"\U0001F6A8 *ERROR*\n\n"
        f"Context: {context}\n"
        f"Error: {error[:200]}"
    )


# ---------------------------------------------------------------------------
# New event templates (Phase 4 additions)
# ---------------------------------------------------------------------------

def format_trade_opened(
    pair: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit_1: float,
    take_profit_2: float | None,
    lot_size: float,
    risk_usd: float,
) -> str:
    """Format TRADE OPENED confirmation (actual market fill)."""
    tp2_line = f"\U0001F3AF TP2: {take_profit_2}" if take_profit_2 else ""
    return (
        f"\U0001F7E2 *TRADE OPENED*\n\n"
        f"*{pair}* — {direction.upper()}\n"
        f"\U0001F4B2 Entry: {entry_price}\n"
        f"\U0001F6D1 Stop Loss: {stop_loss}\n"
        f"\U0001F3AF TP1: {take_profit_1}\n"
        f"{tp2_line}\n\n"
        f"Lot: {lot_size:.2f}  |  Risk: ${risk_usd:.2f}"
    ).strip()


def format_pending_added(
    pair: str,
    direction: str,
    zone_low: float,
    zone_high: float,
    rec_entry: float,
    ttl_hours: float,
) -> str:
    """Format PENDING QUEUE ADDED notification."""
    return (
        f"\U0001F4CB *PENDING SETUP*\n\n"
        f"*{pair}* — {direction.upper()}\n\n"
        f"\U0001F4CD Entry Zone: {zone_low} – {zone_high}\n"
        f"\U0001F446 Rec Entry: {rec_entry}\n\n"
        f"\u23F3 TTL: {ttl_hours:.1f}h\n"
        f"Bot will auto-enter when price hits zone."
    )


def format_pending_expired(
    pair: str,
    direction: str,
    ttl_hours: float,
) -> str:
    """Format PENDING SETUP EXPIRED notification."""
    return (
        f"\u274C *PENDING EXPIRED*\n\n"
        f"*{pair}* — {direction.upper()}\n\n"
        f"Setup expired after {ttl_hours:.1f}h without price reaching zone.\n"
        f"Price never entered the entry zone."
    )


def format_drawdown_halt(
    halt_type: str,
    drawdown_pct: float,
    balance: float,
    high_water_mark: float,
) -> str:
    """Format DRAWDOWN HALT emergency notification."""
    return (
        f"\U0001F6D1 *TRADING HALTED*\n\n"
        f"Type: {halt_type}\n"
        f"Drawdown: {drawdown_pct:.1%}\n\n"
        f"Balance: ${balance:.2f}\n"
        f"High-Water-Mark: ${high_water_mark:.2f}\n\n"
        f"\u26A0\uFE0F No new trades will be opened.\n"
        f"Manual review required."
    )
