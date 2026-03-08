"""
main.py — AI Forex Agent entry point.

Starts:
  1. FastAPI dashboard (uvicorn) on configurable host:port
  2. APScheduler scan jobs (Asian, London, Pre-NY, Wrap-Up)
  3. Per-pair AnalysisOrchestrator pipeline
  4. Trade lifecycle (drawdown, SL/TP monitor, PostMortem, DB)

Usage::

    python main.py              # default: 0.0.0.0:8000
    python main.py --port 9000  # custom port

Reference: masterplan.md §3 (Architecture), §16 (Schedule).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal  # L-01: Used for SIGINT/SIGTERM handlers; on Windows falls back to atexit
import sys
from datetime import datetime, timezone, timedelta

import uvicorn

from config.settings import (
    MVP_PAIRS,
    WHATSAPP_API_URL,
    WHATSAPP_PHONE,
    WHATSAPP_DEVICE_ID,
    WHATSAPP_BASIC_USER,
    WHATSAPP_BASIC_PASS,
    TRADING_MODE,
    INITIAL_BALANCE,
    STATE_INTERVALS,
    MIN_SCORE_FOR_TRADE,
    CORRELATION_GROUPS,
    DB_FILE_PATH,
)
from agent.orchestrator import AnalysisOrchestrator, AnalysisOutcome
from agent.gemini_client import GeminiClient
from agent.state_machine import AnalysisState
from agent.production_lifecycle import ProductionLifecycle, get_current_price_async
from database.repository import Repository
from scheduler.runner import ScanScheduler
from notifier.whatsapp import WhatsAppNotifier
from notifier.handler import NotificationHandler
from charts.screenshot import ChartScreenshotGenerator
from dashboard.backend.main import (
    app,
    push_analysis_update,
    push_state_change,
    push_trade_closed,
    push_portfolio_update,
    update_daily_stats,
    set_repo as set_dashboard_repo,
    set_lifecycle as set_dashboard_lifecycle,
    set_scheduler as set_dashboard_scheduler,
    record_equity_point,
    load_equity_from_db,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# CON-03: Standardised log format across all modules.
# Pattern: "<ISO-timestamp> [<LEVEL>] <logger-name>: <message>"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Per-pair orchestrators (FIX §7.8: bounded to configured pairs only)
# ---------------------------------------------------------------------------
_orchestrators: dict[str, AnalysisOrchestrator] = {}
_MAX_ORCHESTRATORS = 20  # Safety cap

# ---------------------------------------------------------------------------
# Components (lazy-init in startup)
# ---------------------------------------------------------------------------
_scheduler: ScanScheduler | None = None
_wa_notifier: WhatsAppNotifier | None = None
_notification_handler: NotificationHandler | None = None
_chart_gen: ChartScreenshotGenerator | None = None
_lifecycle: ProductionLifecycle | None = None
_repo: Repository | None = None
_price_monitor_task: asyncio.Task | None = None
_scan_locks: dict[str, asyncio.Lock] = {}  # Per-pair scan lock
_started: bool = False  # Guard against double on_startup (uvicorn reimport)


def _get_orchestrator(pair: str) -> AnalysisOrchestrator:
    """Get or create an orchestrator for *pair*. FIX §7.8: capped."""
    if pair not in _orchestrators:
        if len(_orchestrators) >= _MAX_ORCHESTRATORS:
            logger.warning(
                "Orchestrator cap (%d) reached — refusing %s",
                _MAX_ORCHESTRATORS, pair,
            )
            raise RuntimeError(
                f"Too many orchestrators ({_MAX_ORCHESTRATORS}). "
                f"Is an unexpected pair being scanned?"
            )
        _orchestrators[pair] = AnalysisOrchestrator(pair=pair)
    return _orchestrators[pair]


# ---------------------------------------------------------------------------
# Scan callback — called by scheduler for each pair
# ---------------------------------------------------------------------------

async def scan_pair(pair: str) -> AnalysisOutcome | None:
    """Run the orchestrator for *pair* and push results.

    Returns the AnalysisOutcome or None if scan was skipped.
    """
    # Per-pair lock: prevent two concurrent scans for the same pair
    if pair not in _scan_locks:
        _scan_locks[pair] = asyncio.Lock()
    if _scan_locks[pair].locked():
        logger.info("⏳ Skipping %s — scan already in progress", pair)
        return None
    async with _scan_locks[pair]:
        return await _scan_pair_inner(pair)


async def scan_batch(pairs: list[str]) -> None:
    """Scan all *pairs*, rank by score, apply correlation filter, open best trades.

    This replaces the naive sequential scan-and-open approach with a
    proper cherry-picking strategy:
      1. Scan all pairs (collect analysis results)
      2. Rank valid setups by confluence score (descending)
      3. Apply correlation filter (max 1 trade per correlation group)
      4. Open trades for the top-ranked pairs that pass all filters
    """
    logger.info("📊 Batch scan starting: %s", pairs)

    # Phase 1: Scan all pairs and collect results
    scan_results: list[tuple[str, AnalysisOutcome]] = []
    for pair in pairs:
        if _lifecycle and _lifecycle.is_halted:
            logger.warning("⛔ Batch scan halted: %s", _lifecycle.halt_reason)
            break
        try:
            outcome = await scan_pair(pair)  # handles dashboard push + state changes
            if outcome and outcome.plan:
                scan_results.append((pair, outcome))
        except Exception as exc:
            logger.error("Batch scan error for %s: %s", pair, exc)
            if _notification_handler:
                try:
                    await _notification_handler.on_error(f"Batch scan {pair}", exc)
                except Exception:
                    pass

    # Phase 2: Rank valid setups by confluence score (descending)
    valid = [
        (pair, outcome)
        for pair, outcome in scan_results
        if outcome.plan
        and outcome.plan.primary_setup.confluence_score >= MIN_SCORE_FOR_TRADE
    ]
    valid.sort(
        key=lambda x: x[1].plan.primary_setup.confluence_score, reverse=True
    )

    if valid:
        ranking_str = ", ".join(
            f"{p}={o.plan.primary_setup.confluence_score}"
            for p, o in valid
        )
        logger.info("🏆 Ranked setups: %s", ranking_str)
    else:
        logger.info("📊 No valid setups found across %d pairs", len(pairs))
        return

    # Phase 3: Cherry-pick with correlation filter
    selected_groups: set[str] = set()  # track which correlation groups are taken
    # Also consider already-active trades' groups
    if _lifecycle:
        for active_pair in _lifecycle.active_pairs:
            for group, members in CORRELATION_GROUPS.items():
                if active_pair in members:
                    selected_groups.add(group)

    opened = 0
    for pair, outcome in valid:
        if not _lifecycle:
            break

        # Correlation filter: skip if same group already selected
        pair_group = None
        for group, members in CORRELATION_GROUPS.items():
            if pair in members:
                pair_group = group
                break
        if pair_group and pair_group in selected_groups:
            logger.info(
                "⚡ Skip %s (score=%d) — correlation group '%s' already active",
                pair,
                outcome.plan.primary_setup.confluence_score,
                pair_group,
            )
            continue

        # Try to open via lifecycle (respects max trades, drawdown, cooldown)
        try:
            trade = await _lifecycle.on_scan_complete(pair, outcome)
            if trade:
                logger.info(
                    "🔓 Cherry-picked: %s %s %s (score=%d, rank=#%d)",
                    trade.trade_id, pair, trade.direction,
                    outcome.plan.primary_setup.confluence_score,
                    opened + 1,
                )
                opened += 1
                if pair_group:
                    selected_groups.add(pair_group)
        except Exception as exc:
            logger.error("Lifecycle trade open error for %s: %s", pair, exc)

    logger.info(
        "📊 Batch scan done: %d scanned, %d valid, %d opened",
        len(pairs), len(valid), opened,
    )


async def _scan_pair_inner(pair: str) -> AnalysisOutcome:
    """Actual scan logic (called under per-pair lock). Returns the AnalysisOutcome."""
    logger.info("▶ Scanning %s", pair)

    # Check if lifecycle is halted (drawdown breached)
    if _lifecycle and _lifecycle.is_halted:
        logger.warning(
            "⛔ Skipping %s — trading halted: %s", pair, _lifecycle.halt_reason
        )
        return AnalysisOutcome(
            pair=pair,
            state=AnalysisState.SCANNING,
            plan=None,
            error=_lifecycle.halt_reason,
            elapsed_seconds=0,
        )

    orch = _get_orchestrator(pair)
    old_state = orch.state

    outcome: AnalysisOutcome = await orch.run_scan()

    new_state = outcome.state
    logger.info(
        "  %s  %s → %s  plan=%s  err=%s  %.1fs",
        pair,
        old_state.value,
        new_state.value,
        bool(outcome.plan),
        outcome.error or "-",
        outcome.elapsed_seconds,
    )

    # Push analysis to dashboard
    analysis_data = {
        "pair": pair,
        "state": new_state.value,
        "plan": outcome.plan.model_dump() if outcome.plan else None,
        "error": outcome.error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await push_analysis_update(pair, analysis_data)

    # State-change → dashboard WS
    if old_state != new_state:
        await push_state_change(pair, old_state.value, new_state.value)

    # Notification dispatch
    if _notification_handler and old_state != new_state:
        try:
            await _notification_handler.on_state_change(
                old_state=old_state.value,
                new_state=new_state.value,
                plan=outcome.plan,
            )
        except Exception as exc:
            logger.error("Notification error: %s", exc)

    # Trade lifecycle — only open if called as single-pair scan (not batch).
    # scan_batch() handles ranking + cherry-pick logic for batch scans.

    return outcome


# ---------------------------------------------------------------------------
# Price monitoring loop — checks active trades every 60s
# ---------------------------------------------------------------------------

async def price_monitor_loop() -> None:
    """Background loop: poll prices for active trades and handle closes."""
    logger.info("Price monitor started")
    _save_counter = 0
    while True:
        try:
            # 1. Check active trades (SL/TP/BE/trail)
            if _lifecycle and _lifecycle.active_count > 0:
                closed = await _lifecycle.check_active_trades()
                for cr in closed:
                    logger.info(
                        "🔒 Auto-closed %s: %s %s pips=%.1f pnl=$%.2f",
                        cr["trade_id"], cr["pair"], cr["result"],
                        cr["pips"], cr["pnl"],
                    )
                    # Reset orchestrator for the closed pair
                    pair = cr["pair"]
                    if pair in _orchestrators:
                        _orchestrators[pair].reset()

            # 2. Check pending queue (zone entries + TTL expiry)
            # FIX: check_pending_queue was never called — pending setups
            # never executed nor expired automatically.
            if _lifecycle:
                pending_prices: dict[str, float] = {}
                for _pp in _lifecycle.pending_pairs:
                    try:
                        pending_prices[_pp] = await get_current_price_async(_pp)
                    except Exception:
                        pass
                new_trades = await _lifecycle.check_pending_queue(pending_prices)
                for _nt in new_trades:
                    logger.info(
                        "✅ Pending executed: %s %s %s",
                        _nt.trade_id, _nt.pair, _nt.direction,
                    )
                    if _nt.pair in _orchestrators:
                        _orchestrators[_nt.pair].reset()

            # 3. Push portfolio snapshot to WS clients
            await push_portfolio_update()

            # 4. Periodic auto-save: every 5 cycles (5 min) as safety net
            _save_counter += 1
            if _save_counter >= 5 and _lifecycle:
                _save_counter = 0
                try:
                    await _lifecycle.save_active_trades()
                    await _lifecycle.save_state()
                except Exception as exc:
                    logger.error("Periodic save error: %s", exc)
        except Exception as exc:
            logger.error("Price monitor error: %s", exc)

        await asyncio.sleep(60)  # Check every minute


# ---------------------------------------------------------------------------
# Wrap-up callback
# ---------------------------------------------------------------------------

async def daily_wrapup() -> None:
    """End-of-day routine: compute real stats, send summary, reset daily."""
    logger.info("▶ Daily wrap-up starting")

    # Cancel stale orchestrators
    cancelled = 0
    for pair, orch in _orchestrators.items():
        if orch.state in (AnalysisState.WATCHING, AnalysisState.SCANNING):
            logger.info("  Cancelling stale %s (%s)", pair, orch.state.value)
            orch.reset()
            cancelled += 1

    # Get real stats from lifecycle
    summary: dict = {}
    if _lifecycle:
        summary = await _lifecycle.daily_wrapup()
        logger.info(
            "  Lifecycle summary: %d wins, %d losses, %.1f pips, $%.2f pnl",
            summary.get("wins", 0),
            summary.get("losses", 0),
            summary.get("total_pips", 0),
            summary.get("daily_pnl", 0),
        )

    # Trade lines for notification
    trade_lines: list[str] = []
    for ct in summary.get("closed_trades", []):
        trade_lines.append(
            f"  {'✅' if ct['result'] in ('TP1_HIT', 'TP2_HIT') else '❌'} "
            f"{ct['pair']} {ct['direction']} → {ct['result']}  "
            f"{ct['pips']:+.1f} pips  (lesson: {(ct.get('post_mortem_lessons') or ['n/a'])[0]})"
        )

    # Send daily summary notification
    if _notification_handler:
        try:
            await _notification_handler.on_daily_end(
                date_str=datetime.now(timezone(timedelta(hours=7))).strftime(
                    "%Y-%m-%d"
                ),
                total_scans=len(_orchestrators),
                setups_found=sum(
                    1
                    for o in _orchestrators.values()
                    if o.last_plan is not None
                ),
                trades_taken=summary.get("wins", 0) + summary.get("losses", 0),
                trade_lines=trade_lines,
                total_pips=summary.get("total_pips", 0.0),
                win_rate_30d=summary.get("winrate_30d", 0.0),
                expectancy_30d=summary.get("avg_pips_30d", 0.0),
            )
        except Exception as exc:
            logger.error("Daily summary notification error: %s", exc)

    # Push stats to dashboard
    await update_daily_stats(
        {
            "date": datetime.now(timezone(timedelta(hours=7))).strftime(
                "%Y-%m-%d"
            ),
            "total_scans": len(_orchestrators),
            "cancelled": cancelled,
            "wins": summary.get("wins", 0),
            "losses": summary.get("losses", 0),
            "total_pips": summary.get("total_pips", 0.0),
            "balance": summary.get("balance", 0.0),
            "halted": summary.get("halted", False),
        }
    )

    # Reset daily tracking for next day
    if _lifecycle:
        _lifecycle.reset_daily()

    logger.info("▶ Daily wrap-up done")


# ---------------------------------------------------------------------------
# FastAPI lifespan hooks
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """Initialise scheduler, lifecycle, DB, and notification subsystem."""
    global _scheduler, _wa_notifier, _notification_handler, _chart_gen
    global _lifecycle, _repo, _price_monitor_task

    # Guard: app object is shared between __main__ and reimported 'main'
    # module, so both register on_startup. Use app.state as single source.
    if getattr(app.state, "_agent_started", False):
        logger.warning("on_startup called again — skipping (already initialised)")
        return
    app.state._agent_started = True

    logger.info("=== AI Forex Agent starting (mode=%s) ===", TRADING_MODE)

    # --- Database ---
    _repo = Repository()
    await _repo.init_db()
    set_dashboard_repo(_repo)
    await load_equity_from_db()
    logger.info("Database initialised (equity history restored)")

    # --- Production Lifecycle ---
    _lifecycle = ProductionLifecycle(
        repo=_repo,
        mode=TRADING_MODE,
        initial_balance=INITIAL_BALANCE,
    )
    await _lifecycle.init()
    _lifecycle._gemini = GeminiClient()  # For Gemini Flash revalidation
    set_dashboard_lifecycle(_lifecycle)
    logger.info(
        "Lifecycle ready  balance=$%.2f  halted=%s",
        _lifecycle.balance,
        _lifecycle.is_halted,
    )

    # Chart generator
    _chart_gen = ChartScreenshotGenerator()

    # WhatsApp notifier (only if phone is configured)
    if WHATSAPP_PHONE:
        _wa_notifier = WhatsAppNotifier(
            base_url=WHATSAPP_API_URL,
            phone=WHATSAPP_PHONE,
            device_id=WHATSAPP_DEVICE_ID or None,
            basic_user=WHATSAPP_BASIC_USER or None,
            basic_pass=WHATSAPP_BASIC_PASS or None,
        )
        _notification_handler = NotificationHandler(
            notifier=_wa_notifier,
            chart_gen=_chart_gen,
        )
        logger.info("WhatsApp notifier ready → %s", WHATSAPP_API_URL)
    else:
        logger.warning("WHATSAPP_PHONE not set — notifications disabled")

    # Wire lifecycle callbacks → dashboard + WA
    _lifecycle.set_callbacks(
        push_trade_closed=push_trade_closed,
        push_state_change=push_state_change,
        notify_trade_closed=(
            _notification_handler.on_trade_closed
            if _notification_handler
            else None
        ),
        notify_sl_moved=(
            _notification_handler.on_sl_moved
            if _notification_handler
            else None
        ),
        notify_trade_opened=(
            _notification_handler.on_trade_opened
            if _notification_handler
            else None
        ),
        notify_pending_added=(
            _notification_handler.on_pending_added
            if _notification_handler
            else None
        ),
        notify_pending_expired=(
            _notification_handler.on_pending_expired
            if _notification_handler
            else None
        ),
        notify_drawdown_halt=(
            _notification_handler.on_drawdown_halt
            if _notification_handler
            else None
        ),
    )

    # --- Price monitor background task ---
    _price_monitor_task = asyncio.create_task(price_monitor_loop())
    logger.info("Price monitor task started")

    # Scheduler
    _scheduler = ScanScheduler(
        scan_fn=scan_pair,
        batch_fn=scan_batch,
        wrapup_fn=daily_wrapup,
        pairs=MVP_PAIRS,
    )
    _scheduler.configure()
    _scheduler.start()
    set_dashboard_scheduler(_scheduler)

    jobs = _scheduler.jobs
    logger.info("Scheduled %d jobs:", len(jobs))
    for job in jobs:
        logger.info("  • %s  next_run=%s", job.name, job.next_run_time)

    # Initial scan: session-aware (FIX: was running at ANY time after restart)
    # Only scans within active trading session windows (WIB = UTC+7).
    async def _delayed_first_scan():
        await asyncio.sleep(5)
        _WIB = timezone(timedelta(hours=7))
        now_wib = datetime.now(_WIB)
        h = now_wib.hour + now_wib.minute / 60.0
        jpy_pairs = [p for p in MVP_PAIRS if "JPY" in p]

        if 6.0 <= h < 12.0:
            # Asian session — JPY pairs only
            _pairs = jpy_pairs
            logger.info(
                "🚀 Initial scan (Asian session %02d:%02d WIB): %s",
                now_wib.hour, now_wib.minute, _pairs,
            )
        elif 13.5 <= h < 22.5:
            # London / Pre-NY / NY session — all pairs
            _pairs = MVP_PAIRS
            logger.info(
                "🚀 Initial scan (London/NY session %02d:%02d WIB): %s",
                now_wib.hour, now_wib.minute, _pairs,
            )
        else:
            logger.info(
                "⏸ Skipping initial scan — off-session (%02d:%02d WIB). "
                "Scheduler will fire at next session window.",
                now_wib.hour, now_wib.minute,
            )
            return

        await scan_batch(_pairs)

    asyncio.create_task(_delayed_first_scan())

    # Register signal handlers for emergency save on kill
    loop = asyncio.get_event_loop()
    for sig_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig_name, lambda: asyncio.create_task(_emergency_save()))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler — use atexit as fallback
            import atexit
            atexit.register(_emergency_save_sync)
            break

    logger.info("=== AI Forex Agent ready (mode=%s) ===", TRADING_MODE)


async def _emergency_save() -> None:
    """Emergency save: persist active trades on signal (SIGINT/SIGTERM)."""
    if _lifecycle:
        try:
            await _lifecycle.save_active_trades()
            await _lifecycle.save_state()
            logger.info("Emergency save completed")
        except Exception as exc:
            logger.error("Emergency save failed: %s", exc)


def _emergency_save_sync() -> None:
    """Sync fallback for atexit on Windows."""
    if _lifecycle and _lifecycle._repo:
        import sqlite3
        try:
            active_data = []
            for pair, (trade, _mgr) in _lifecycle._active.items():
                active_data.append({
                    "pair": pair,
                    "trade_id": trade.trade_id,
                    "direction": trade.direction,
                    "entry_price": trade.entry_price,
                    "stop_loss": trade.stop_loss,
                    "take_profit_1": trade.take_profit_1,
                    "take_profit_2": getattr(trade, "take_profit_2", None),
                    "partial_closed": getattr(trade, "partial_closed", False),
                    "sl_moved_to_be": getattr(trade, "sl_moved_to_be", False),
                    "trail_active": getattr(trade, "trail_active", False),
                    "lot_size": getattr(trade, "lot_size", 0.0),
                    "risk_amount": getattr(trade, "risk_amount", 0.0),
                    "remaining_size": getattr(trade, "remaining_size", 1.0),
                    "realized_pnl": getattr(trade, "realized_pnl", 0.0),
                    "strategy_mode": getattr(trade, "strategy_mode", ""),
                    "confluence_score": getattr(trade, "confluence_score", 0),
                    "voting_confidence": getattr(trade, "voting_confidence", 0.0),
                    "htf_bias": getattr(trade, "htf_bias", ""),
                    "last_revalidation_at": (
                        trade.last_revalidation_at.isoformat()
                        if getattr(trade, "last_revalidation_at", None)
                        else None
                    ),
                    "last_revalidation_note": getattr(trade, "last_revalidation_note", ""),
                    "opened_at": trade.opened_at.isoformat() if hasattr(trade, "opened_at") and trade.opened_at else None,
                })
            import json
            conn = sqlite3.connect(DB_FILE_PATH, timeout=10)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "INSERT OR REPLACE INTO settings_kv (key, value, updated_at) VALUES (?, ?, ?)",
                ("active_trades", json.dumps(active_data), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            logger.info("Emergency sync save: %d trades persisted", len(active_data))
        except Exception as exc:
            logger.error("Emergency sync save failed: %s", exc)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Graceful shutdown: persist state, save active trades, cancel tasks, stop scheduler."""
    global _scheduler, _price_monitor_task

    # Stop price monitor
    if _price_monitor_task and not _price_monitor_task.done():
        _price_monitor_task.cancel()
        try:
            await _price_monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("Price monitor stopped")

    # Save active trades to DB before shutdown
    if _lifecycle and _repo:
        active_pairs = list(_lifecycle.active_pairs)
        if active_pairs:
            logger.info(
                "Saving %d active trade(s) to DB before shutdown: %s",
                len(active_pairs),
                active_pairs,
            )
            try:
                await _lifecycle.save_active_trades()
            except Exception as exc:
                logger.error("Failed to save active trades: %s", exc)

    # Persist lifecycle state
    if _lifecycle:
        await _lifecycle.save_state()
        logger.info("Lifecycle state persisted")

    # Close WhatsApp notifier connection pool
    if _wa_notifier:
        try:
            await _wa_notifier.close()
        except Exception:
            pass

    # Stop scheduler
    if _scheduler:
        _scheduler.shutdown()

    logger.info("=== AI Forex Agent stopped ===")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AI Forex Agent")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (dev)"
    )
    args = parser.parse_args()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
