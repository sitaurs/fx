"""
agent/production_lifecycle.py — Production trade lifecycle orchestrator.

Integrates ALL components into a production-ready pipeline:
  - AnalysisOrchestrator (scan, vote, plan)
  - TradeManager (SL+, trail, SL/TP detection)
  - PostMortemGenerator (auto-journal on close)
  - Repository (SQLite persistence)
  - Dashboard push (WebSocket real-time)
  - WhatsApp notifications
  - Drawdown protection (daily + total)

This is the "brain" that was previously demo-only.  It replaces
hardcoded zeros in main.py with real trade tracking.

Usage::

    lifecycle = ProductionLifecycle(repo=repo)
    await lifecycle.init()

    # In scan callback:
    await lifecycle.on_scan_complete(pair, outcome)

    # In price monitor loop:
    await lifecycle.check_active_trades()

    # End of day:
    summary = await lifecycle.daily_wrapup()

Reference: masterplan.md §3, §13, §14, §23
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Awaitable

from config.settings import (
    TRADING_MODE,
    MIN_SCORE_FOR_TRADE,
    PAIR_POINT,
    PRICE_SANITY_THRESHOLDS,
    PRICE_SANITY_DEFAULT,
    LIFECYCLE_COOLDOWN_MINUTES,
    POSITION_SIZING_MODE,
    FIXED_LOT_SIZE,
    DRAWDOWN_GUARD_ENABLED,
    ACTIVE_REVALIDATION_ENABLED,
    ACTIVE_REVALIDATION_INTERVAL_MINUTES,
    CHALLENGE_CENT_LOT_MULTIPLIER,
    CHALLENGE_CENT_SL_MULTIPLIER,
    CHALLENGE_CENT_TP_MULTIPLIER,
    ENTRY_ZONE_EXECUTION_BUFFER_PIPS,
    PENDING_SETUP_DEFAULT_TTL_HOURS,
)
from agent.trade_manager import (
    ActiveTrade,
    TradeManager,
    TradeAction,
    ActionType,
)
from agent.pending_manager import (
    PendingSetup,
    PendingManager,
    compute_recommended_entry,
)
from agent.post_mortem import PostMortemGenerator, PostMortemReport, MarketContext
from agent.orchestrator import AnalysisOutcome
from agent.context_builder import collect_multi_tf_async
from database.models import Trade
from database.repository import Repository
from schemas.plan import TradingPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Price helper — get latest close from M1
# ---------------------------------------------------------------------------

def get_current_price(pair: str) -> float:
    """Fetch the latest M1 candle close as current price proxy."""
    from data.fetcher import fetch_ohlcv

    data = fetch_ohlcv(pair, "M1", 1)
    candles = data.get("candles", [])
    if not candles:
        raise RuntimeError(f"No M1 data for {pair}")
    return float(candles[-1]["close"])


async def get_current_price_async(pair: str) -> float:
    """Async version — runs sync get_current_price in executor (FIX §7.1).

    Delegates to get_current_price so that unittest.mock.patch on
    get_current_price transparently affects this function too.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_current_price, pair)


# ---------------------------------------------------------------------------
# Production Lifecycle
# ---------------------------------------------------------------------------

class ProductionLifecycle:
    """Portfolio-level trade lifecycle for production.

    Tracks all active trades, enforces drawdown limits, generates
    post-mortems, persists to SQLite, pushes to dashboard and WhatsApp.
    """

    def __init__(
        self,
        repo: Repository,
        *,
        mode: str = TRADING_MODE,
        initial_balance: float = 10_000.0,
        risk_per_trade: float = 0.01,
        max_daily_drawdown: float = 0.05,
        max_total_drawdown: float = 0.15,
        max_concurrent_trades: int = 2,
    ):
        self._repo = repo
        self.mode = mode
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.high_water_mark = initial_balance
        self.daily_start_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.position_sizing_mode = POSITION_SIZING_MODE  # risk_percent | fixed_lot
        self.fixed_lot_size = FIXED_LOT_SIZE
        self.drawdown_guard_enabled = DRAWDOWN_GUARD_ENABLED
        self.max_daily_drawdown = max_daily_drawdown
        self.max_total_drawdown = max_total_drawdown
        self.max_concurrent_trades = max_concurrent_trades
        self.challenge_mode = "none"  # none | challenge_extreme | challenge_cent
        self._lot_value_multiplier = 1.0
        self._entry_zone_execution_buffer_pips = max(
            float(ENTRY_ZONE_EXECUTION_BUFFER_PIPS), 0.0
        )
        self.active_revalidation_enabled = ACTIVE_REVALIDATION_ENABLED
        self.active_revalidation_interval_minutes = ACTIVE_REVALIDATION_INTERVAL_MINUTES
        self._last_revalidation: dict[str, datetime] = {}

        # Active trades: pair → (ActiveTrade, TradeManager)
        self._active: dict[str, tuple[ActiveTrade, TradeManager]] = {}
        self._closed_today: list[dict] = []
        self._pending = PendingManager(max_pending=10)
        self._post_mortem = PostMortemGenerator()
        self._trade_lock = asyncio.Lock()  # Prevent concurrent trade opens
        # Cooldown: pair → earliest UTC when new trade can open
        self._pair_cooldown: dict[str, datetime] = {}
        self._cooldown_minutes = LIFECYCLE_COOLDOWN_MINUTES  # FIX F4-12: configurable
        self._gemini = None  # Set externally if Gemini client is available
        self._default_config = {
            "mode": mode,
            "initial_balance": initial_balance,
            "risk_per_trade": risk_per_trade,
            "position_sizing_mode": POSITION_SIZING_MODE,
            "fixed_lot_size": FIXED_LOT_SIZE,
            "drawdown_guard_enabled": DRAWDOWN_GUARD_ENABLED,
            "max_daily_drawdown": max_daily_drawdown,
            "max_total_drawdown": max_total_drawdown,
            "max_concurrent_trades": max_concurrent_trades,
            "challenge_mode": "none",
            "active_revalidation_enabled": ACTIVE_REVALIDATION_ENABLED,
            "active_revalidation_interval_minutes": ACTIVE_REVALIDATION_INTERVAL_MINUTES,
        }

        self._halted = False
        self._halt_reason = ""

        # Callbacks — set by main.py
        self._push_trade_closed: Optional[Callable] = None
        self._push_state_change: Optional[Callable] = None
        self._notify_trade_closed: Optional[Callable] = None
        self._notify_sl_moved: Optional[Callable] = None
        # Phase 4 additions
        self._notify_trade_opened: Optional[Callable] = None
        self._notify_pending_added: Optional[Callable] = None
        self._notify_pending_expired: Optional[Callable] = None
        self._notify_drawdown_halt: Optional[Callable] = None
        self._halt_notified: bool = False  # ensure halt alert fires only once
        # Deferred halt notification (check_drawdown is sync; fired from async check_active_trades)
        self._pending_halt_info: tuple | None = None  # (halt_type, dd_pct, balance, hwm)

    # -- Init & state restore -----------------------------------------------

    async def init(self) -> None:
        """Initialize DB and restore state from SettingsKV."""
        await self._repo.init_db()
        saved = await self._repo.get_setting_json("lifecycle_state")
        if saved:
            self.balance = saved.get("balance", self.initial_balance)
            self.high_water_mark = saved.get("high_water_mark", self.balance)
            self.daily_start_balance = saved.get("daily_start_balance", self.balance)
            self.risk_per_trade = saved.get("risk_per_trade", self.risk_per_trade)
            self.position_sizing_mode = saved.get(
                "position_sizing_mode",
                self.position_sizing_mode,
            )
            self.fixed_lot_size = saved.get("fixed_lot_size", self.fixed_lot_size)
            self.drawdown_guard_enabled = saved.get(
                "drawdown_guard_enabled",
                self.drawdown_guard_enabled,
            )
            self.max_daily_drawdown = saved.get(
                "max_daily_drawdown",
                self.max_daily_drawdown,
            )
            self.max_total_drawdown = saved.get(
                "max_total_drawdown",
                self.max_total_drawdown,
            )
            self.max_concurrent_trades = saved.get(
                "max_concurrent_trades",
                self.max_concurrent_trades,
            )
            self.active_revalidation_enabled = saved.get(
                "active_revalidation_enabled",
                self.active_revalidation_enabled,
            )
            self.active_revalidation_interval_minutes = saved.get(
                "active_revalidation_interval_minutes",
                self.active_revalidation_interval_minutes,
            )
            self._halted = saved.get("halted", False)
            self._halt_reason = saved.get("halt_reason", "")
            self.mode = saved.get("mode", self.mode)
            self._apply_challenge_mode(saved.get("challenge_mode", self.challenge_mode))
            if not self.drawdown_guard_enabled:
                self._halted = False
                self._halt_reason = ""
            logger.info(
                "Lifecycle restored: balance=$%.2f, hwm=$%.2f, halted=%s",
                self.balance, self.high_water_mark, self._halted,
            )
        else:
            logger.info("Lifecycle fresh start: balance=$%.2f", self.balance)

        # FIX §7.3: Restore active trades that survived restart
        await self.restore_active_trades()
        await self.restore_pending_setups()

    async def save_state(self) -> None:
        """Persist lifecycle state to DB."""
        await self._repo.set_setting_json("lifecycle_state", {
            "mode": self.mode,
            "balance": self.balance,
            "initial_balance": self.initial_balance,
            "high_water_mark": self.high_water_mark,
            "daily_start_balance": self.daily_start_balance,
            "risk_per_trade": self.risk_per_trade,
            "position_sizing_mode": self.position_sizing_mode,
            "fixed_lot_size": self.fixed_lot_size,
            "drawdown_guard_enabled": self.drawdown_guard_enabled,
            "max_daily_drawdown": self.max_daily_drawdown,
            "max_total_drawdown": self.max_total_drawdown,
            "max_concurrent_trades": self.max_concurrent_trades,
            "challenge_mode": self.challenge_mode,
            "active_revalidation_enabled": self.active_revalidation_enabled,
            "active_revalidation_interval_minutes": self.active_revalidation_interval_minutes,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
        })

    async def save_active_trades(self) -> None:
        """Persist active trades to DB so they survive restarts."""
        active_data = []
        for pair, (trade, _mgr) in self._active.items():
            try:
                active_data.append({
                    "pair": pair,
                    "trade_id": trade.trade_id,
                    "direction": trade.direction,
                    "entry_price": trade.entry_price,
                    "stop_loss": trade.stop_loss,
                    "take_profit_1": trade.take_profit_1,
                    "take_profit_2": trade.take_profit_2,
                    "original_sl": trade.original_sl,
                    "partial_closed": trade.partial_closed,
                    "sl_moved_to_be": trade.sl_moved_to_be,
                    "trail_active": trade.trail_active,
                    "lot_size": trade.lot_size,
                    "risk_amount": trade.risk_amount,
                    "remaining_size": trade.remaining_size,
                    "realized_pnl": trade.realized_pnl,
                    "strategy_mode": trade.strategy_mode,
                    "confluence_score": trade.confluence_score,
                    "voting_confidence": trade.voting_confidence,
                    "htf_bias": trade.htf_bias,
                    "entry_zone_type": trade.entry_zone_type,
                    "entry_zone_low": trade.entry_zone_low,
                    "entry_zone_high": trade.entry_zone_high,
                    "recommended_entry": trade.recommended_entry,
                    "last_revalidation_at": (
                        trade.last_revalidation_at.isoformat()
                        if trade.last_revalidation_at
                        else None
                    ),
                    "last_revalidation_note": trade.last_revalidation_note,
                    "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                })
            except Exception as exc:
                logger.error("Failed to serialise active trade %s: %s", pair, exc)
        await self._repo.set_setting_json("active_trades", active_data)
        logger.info("Saved %d active trade(s) to DB", len(active_data))

    async def restore_active_trades(self) -> int:
        """Restore active trades from DB after restart (FIX §7.3).

        Returns the number of successfully restored trades.
        """
        saved = await self._repo.get_setting_json("active_trades")
        if not saved:
            return 0

        restored = 0
        for td in saved:
            try:
                opened_at = datetime.now(timezone.utc)
                if td.get("opened_at"):
                    try:
                        opened_at = datetime.fromisoformat(td["opened_at"])
                    except (ValueError, TypeError):
                        pass
                last_revalidation_at = None
                if td.get("last_revalidation_at"):
                    try:
                        last_revalidation_at = datetime.fromisoformat(td["last_revalidation_at"])
                    except (ValueError, TypeError):
                        last_revalidation_at = None

                trade = ActiveTrade(
                    trade_id=td["trade_id"],
                    pair=td["pair"],
                    direction=td["direction"],
                    entry_price=td["entry_price"],
                    stop_loss=td["stop_loss"],
                    take_profit_1=td["take_profit_1"],
                    take_profit_2=td.get("take_profit_2"),
                    original_sl=td.get("original_sl", 0.0),
                    partial_closed=td.get("partial_closed", False),
                    sl_moved_to_be=td.get("sl_moved_to_be", False),
                    trail_active=td.get("trail_active", False),
                    lot_size=td.get("lot_size", 0.0),
                    risk_amount=td.get("risk_amount", 0.0),
                    remaining_size=td.get("remaining_size", 1.0),
                    realized_pnl=td.get("realized_pnl", 0.0),
                    strategy_mode=td.get("strategy_mode", ""),
                    confluence_score=td.get("confluence_score", 0),
                    voting_confidence=td.get("voting_confidence", 0.0),
                    htf_bias=td.get("htf_bias", ""),
                    entry_zone_type=td.get("entry_zone_type", ""),
                    entry_zone_low=td.get("entry_zone_low", 0.0),
                    entry_zone_high=td.get("entry_zone_high", 0.0),
                    recommended_entry=td.get("recommended_entry"),
                    last_revalidation_at=last_revalidation_at,
                    last_revalidation_note=td.get("last_revalidation_note", ""),
                    opened_at=opened_at,
                )
                mgr = TradeManager(trade)
                self._active[td["pair"]] = (trade, mgr)
                if trade.last_revalidation_at:
                    self._last_revalidation[td["pair"]] = trade.last_revalidation_at
                restored += 1
                logger.info(
                    "♻️ Restored active trade %s: %s %s @ %.5f",
                    trade.trade_id, trade.pair, trade.direction, trade.entry_price,
                )
            except Exception as exc:
                logger.error("Failed to restore trade %s: %s", td.get("pair", "?"), exc)

        if restored:
            logger.info("Restored %d active trade(s) from DB", restored)
        return restored

    def set_callbacks(
        self,
        push_trade_closed: Optional[Callable] = None,
        push_state_change: Optional[Callable] = None,
        notify_trade_closed: Optional[Callable] = None,
        notify_sl_moved: Optional[Callable] = None,
        notify_trade_opened: Optional[Callable] = None,
        notify_pending_added: Optional[Callable] = None,
        notify_pending_expired: Optional[Callable] = None,
        notify_drawdown_halt: Optional[Callable] = None,
    ) -> None:
        """Wire up dashboard push and notification callbacks."""
        self._push_trade_closed = push_trade_closed
        self._push_state_change = push_state_change
        self._notify_trade_closed = notify_trade_closed
        self._notify_sl_moved = notify_sl_moved
        self._notify_trade_opened = notify_trade_opened
        self._notify_pending_added = notify_pending_added
        self._notify_pending_expired = notify_pending_expired
        self._notify_drawdown_halt = notify_drawdown_halt

    # -- Pending queue helpers -----------------------------------------------

    @property
    def pending_count(self) -> int:
        return self._pending.count

    @property
    def pending_pairs(self) -> list[str]:
        return self._pending.pending_pairs

    def get_pending_setups(self) -> list[dict]:
        """Return pending setups as dashboard-friendly dicts."""
        return self._pending.to_dashboard_list()

    async def _save_pending_setups(self) -> None:
        """Persist pending setups to DB."""
        await self._repo.set_setting_json(
            "pending_setups",
            self._pending.to_persistence_list(),
        )

    async def restore_pending_setups(self) -> int:
        """Restore pending setups from DB after restart."""
        saved = await self._repo.get_setting_json("pending_setups")
        if not saved:
            return 0
        return self._pending.restore_from_list(saved)

    async def cancel_pending_setup(self, setup_id: str) -> bool:
        """Cancel a pending setup by ID."""
        ok = self._pending.remove_by_id(setup_id)
        if ok:
            await self._save_pending_setups()
        return ok

    async def force_execute_pending(self, setup_id: str) -> Optional[ActiveTrade]:
        """Force-execute a pending setup at current market price (ignoring zone)."""
        for s in self._pending.get_pending():
            if s.setup_id == setup_id:
                # Check capacity
                ok, reason = self.can_open_trade()
                if not ok:
                    logger.warning("Cannot force-execute %s: %s", setup_id, reason)
                    return None
                # Already in trade for this pair?
                if s.pair in self._active:
                    logger.info("Cannot force-execute %s: %s already active", setup_id, s.pair)
                    return None
                trade = await self._open_trade(s.plan)
                self._pending.mark_executed(setup_id)
                await self._save_pending_setups()
                return trade
        return None

    async def check_pending_queue(self, prices: dict[str, float]) -> list[ActiveTrade]:
        """Check pending queue for zone entries, TTL expiry, and execute.

        Called from the price monitor loop every ~60 seconds.
        Returns list of newly opened ActiveTrade objects.
        """
        # 1. Expire old setups
        expired_setups = self._pending.cleanup_expired()
        if expired_setups and self._notify_pending_expired:
            for _es in expired_setups:
                try:
                    await self._notify_pending_expired(
                        pair=_es.pair,
                        direction=_es.direction,
                        ttl_hours=_es.ttl_hours,
                    )
                except Exception as _exc:
                    logger.warning("notify_pending_expired failed: %s", _exc)

        # 2. Clean up stale entries
        self._pending.cleanup_old(max_age_hours=24)

        # 3. Check zone entries
        ready = self._pending.check_zone_entries(
            prices,
            entry_zone_buffer_pips=self._entry_zone_execution_buffer_pips,
        )

        opened: list[ActiveTrade] = []
        for setup in ready:
            async with self._trade_lock:
                # Check capacity
                ok, reason = self.can_open_trade(price_cache=prices)
                if not ok:
                    logger.info(
                        "Pending %s (%s) ready but cannot open: %s",
                        setup.setup_id, setup.pair, reason,
                    )
                    continue

                # Already active for this pair?
                if setup.pair in self._active:
                    continue

                # Cooldown check
                cooldown_until = self._pair_cooldown.get(setup.pair)
                if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
                    continue

                try:
                    market_price = prices.get(setup.pair)
                    # FIX H-02: Mark as executing BEFORE open to prevent duplicate
                    self._pending.mark_executing(setup.setup_id)
                    trade = await self._open_trade(setup.plan, market_price=market_price)
                    self._pending.mark_executed(setup.setup_id)
                    opened.append(trade)
                    logger.info(
                        "Pending->Active: %s %s @ %.5f (was pending since %s)",
                        setup.pair, setup.direction, trade.entry_price,
                        setup.created_at.strftime("%H:%M"),
                    )
                except Exception as exc:
                    # FIX H-02: Revert to pending so next cycle retries
                    self._pending.revert_executing(setup.setup_id)
                    logger.error("Failed to execute pending %s: %s", setup.setup_id, exc)

        if opened or self._pending.count > 0:
            await self._save_pending_setups()

        return opened

    # -- Properties ---------------------------------------------------------

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    async def unhalt(self) -> None:
        """Publicly unhalt the system (FIX H-16: avoid private attr mutation)."""
        self._halted = False
        self._halt_reason = ""
        await self.save_state()

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_pairs(self) -> list[str]:
        return list(self._active.keys())

    def _pip_value_per_lot(
        self,
        pair: str,
        *,
        pair_price: float | None = None,
        usd_jpy: float | None = None,
    ) -> float:
        """Approximate pip value (USD) for 1.0 lot on USD account.

        Handles all pair categories:
          - Metals (XAUUSD, XAGUSD)
          - XXXUSD (quote=USD, e.g. EURUSD): always $10/pip/lot
          - USDXXX (base=USD, e.g. USDJPY): convert via pair price
          - XXX/JPY crosses (e.g. GBPJPY): convert via USDJPY
          - Non-USD non-JPY crosses (e.g. EURGBP, AUDCHF): convert
            via quote currency's USD pair
        """
        pair = pair.upper()
        point = PAIR_POINT.get(pair, 0.0001)

        # Gold: 100 oz contract, 0.1 pip ~= $10 per lot
        if pair == "XAUUSD":
            return 10.0

        # Silver: 5000 oz contract, 0.01 pip ~= $50 per lot
        if pair == "XAGUSD":
            return 50.0

        # XXXUSD quote pairs (EURUSD, GBPUSD, AUDUSD, NZDUSD): $10 per pip per lot
        if pair.endswith("USD") and not pair.startswith("USD"):
            return 10.0

        # USDXXX base pairs (USDJPY/USDCHF/USDCAD): convert quote pip to USD
        if pair.startswith("USD"):
            px = pair_price if pair_price and pair_price > 0 else get_current_price(pair)
            return (100_000 * point) / px if px > 0 else 10.0

        # Crosses ending JPY (e.g. GBPJPY, EURJPY, CADJPY): convert via USDJPY
        if pair.endswith("JPY"):
            uj = usd_jpy if usd_jpy and usd_jpy > 0 else get_current_price("USDJPY")
            return (100_000 * point) / uj if uj > 0 else 10.0

        # Non-USD non-JPY crosses (e.g. EURGBP, GBPCHF, AUDNZD, EURAUD)
        # Pip value = (100,000 × point) / QUOTE_USD_rate
        # Quote currency is the last 3 chars of the pair.
        quote_ccy = pair[3:]  # e.g. "GBP" from "EURGBP"
        # Build the quote-vs-USD pair to get conversion rate
        quote_usd_pair = f"{quote_ccy}USD"   # e.g. GBPUSD
        usd_quote_pair = f"USD{quote_ccy}"   # e.g. USDCHF
        try:
            # Try XXXUSD first (GBPUSD, AUDUSD, NZDUSD)
            rate = get_current_price(quote_usd_pair)
            if rate and rate > 0:
                return (100_000 * point) * rate
        except Exception:
            pass
        try:
            # Try USDXXX (USDCHF, USDCAD)
            rate = get_current_price(usd_quote_pair)
            if rate and rate > 0:
                return (100_000 * point) / rate
        except Exception:
            pass

        # Conservative fallback for unknown pairs
        logger.warning("pip_value fallback $10 for unknown pair %s", pair)
        return 10.0

    def _compute_lot_and_risk(
        self,
        pair: str,
        entry_price: float,
        stop_loss: float,
    ) -> tuple[float, float]:
        """Return (lot_size, risk_amount_usd) for a new trade."""
        point = PAIR_POINT.get(pair, 0.0001)
        sl_pips = abs(entry_price - stop_loss) / point if point else 0.0
        if sl_pips <= 0:
            sl_pips = 1.0

        pip_value = self._pip_value_per_lot(pair) * self._lot_value_multiplier

        if self.position_sizing_mode == "fixed_lot":
            lot_size = max(self.fixed_lot_size, 0.0)
            risk_amount = sl_pips * pip_value * lot_size
            return lot_size, risk_amount

        # risk_percent mode
        risk_amount = max(self.balance * self.risk_per_trade, 0.0)
        denom = sl_pips * pip_value
        lot_size = (risk_amount / denom) if denom > 0 else 0.0
        # FIX L-09: enforce minimum lot 0.01 (broker minimum)
        lot_size = max(lot_size, 0.01)
        return lot_size, risk_amount

    def trade_floating_pnl(self, trade: ActiveTrade, current_price: float) -> float:
        """Floating USD for remaining open size only."""
        remaining = max(trade.remaining_size, 0.0)
        if remaining == 0.0:
            return 0.0
        # Use pip-based calculation for accuracy
        pair = trade.pair
        point = PAIR_POINT.get(pair, 0.0001)
        pip_value = self._pip_value_per_lot(pair) * self._lot_value_multiplier
        if trade.direction.lower() == "buy":
            pips_raw = (current_price - trade.entry_price) / point if point else 0.0
        else:
            pips_raw = (trade.entry_price - current_price) / point if point else 0.0
        lot_size = trade.lot_size if trade.lot_size > 0 else 0.01
        return pips_raw * pip_value * lot_size * remaining

    def _entry_zone_bounds(
        self,
        pair: str,
        entry_zone_low: float,
        entry_zone_high: float,
    ) -> tuple[float, float, float]:
        """Normalize entry zone and apply execution buffer."""
        z_low = min(float(entry_zone_low), float(entry_zone_high))
        z_high = max(float(entry_zone_low), float(entry_zone_high))
        point = PAIR_POINT.get(pair, 0.0001)
        buffer_price = self._entry_zone_execution_buffer_pips * point
        return (z_low - buffer_price, z_high + buffer_price, buffer_price)

    def _is_price_in_entry_zone(
        self,
        pair: str,
        price: float,
        entry_zone_low: float,
        entry_zone_high: float,
    ) -> tuple[bool, float, float, float]:
        """Return (in_zone, zone_low_exec, zone_high_exec, buffer_price)."""
        z_low, z_high, buffer_price = self._entry_zone_bounds(
            pair,
            entry_zone_low,
            entry_zone_high,
        )
        return (z_low <= float(price) <= z_high, z_low, z_high, buffer_price)

    def get_runtime_config(self) -> dict:
        return {
            "mode": self.mode,
            "challenge_mode": self.challenge_mode,
            "balance": round(self.balance, 2),
            "initial_balance": round(self.initial_balance, 2),
            "risk_per_trade": self.risk_per_trade,
            "position_sizing_mode": self.position_sizing_mode,
            "fixed_lot_size": self.fixed_lot_size,
            "drawdown_guard_enabled": self.drawdown_guard_enabled,
            "max_daily_drawdown": self.max_daily_drawdown,
            "max_total_drawdown": self.max_total_drawdown,
            "max_concurrent_trades": self.max_concurrent_trades,
            "active_revalidation_enabled": self.active_revalidation_enabled,
            "active_revalidation_interval_minutes": self.active_revalidation_interval_minutes,
            "lot_value_multiplier": self._lot_value_multiplier,
            "entry_zone_execution_buffer_pips": self._entry_zone_execution_buffer_pips,
            "cent_sl_multiplier": getattr(self, "_cent_sl_multiplier", CHALLENGE_CENT_SL_MULTIPLIER),
            "cent_tp_multiplier": getattr(self, "_cent_tp_multiplier", CHALLENGE_CENT_TP_MULTIPLIER),
            "pending_count": self.pending_count,
            "pending_setups": self.get_pending_setups(),
        }

    def _apply_challenge_mode(self, challenge_mode: str) -> None:
        mode = (challenge_mode or "none").strip().lower()
        if mode in {"challenge_extreme", "extreme"}:
            self.challenge_mode = "challenge_extreme"
            self.position_sizing_mode = "fixed_lot"
            self.drawdown_guard_enabled = False
            self._lot_value_multiplier = 1.0
            self._halted = False
            self._halt_reason = ""
            return
        if mode in {"challenge_cent", "cent"}:
            self.challenge_mode = "challenge_cent"
            self.position_sizing_mode = "fixed_lot"
            self.drawdown_guard_enabled = False
            self._lot_value_multiplier = CHALLENGE_CENT_LOT_MULTIPLIER
            self._cent_sl_multiplier = CHALLENGE_CENT_SL_MULTIPLIER
            self._cent_tp_multiplier = CHALLENGE_CENT_TP_MULTIPLIER
            self._halted = False
            self._halt_reason = ""
            return
        self.challenge_mode = "none"
        self._lot_value_multiplier = 1.0

    async def update_runtime_config(self, updates: dict) -> dict:
        """Apply mutable lifecycle settings and persist."""
        if "mode" in updates and updates["mode"]:
            self.mode = str(updates["mode"]).strip().lower()

        if "challenge_mode" in updates:
            self._apply_challenge_mode(str(updates["challenge_mode"]))

        if "position_sizing_mode" in updates and updates["position_sizing_mode"]:
            mode = str(updates["position_sizing_mode"]).strip().lower()
            if mode in {"risk_percent", "fixed_lot"}:
                self.position_sizing_mode = mode

        if "fixed_lot_size" in updates and updates["fixed_lot_size"] is not None:
            self.fixed_lot_size = max(float(updates["fixed_lot_size"]), 0.0)

        if "risk_per_trade" in updates and updates["risk_per_trade"] is not None:
            self.risk_per_trade = max(float(updates["risk_per_trade"]), 0.0)

        if "drawdown_guard_enabled" in updates and updates["drawdown_guard_enabled"] is not None:
            self.drawdown_guard_enabled = bool(updates["drawdown_guard_enabled"])
            if not self.drawdown_guard_enabled:
                self._halted = False
                self._halt_reason = ""

        if "max_daily_drawdown" in updates and updates["max_daily_drawdown"] is not None:
            self.max_daily_drawdown = max(float(updates["max_daily_drawdown"]), 0.0)

        if "max_total_drawdown" in updates and updates["max_total_drawdown"] is not None:
            self.max_total_drawdown = max(float(updates["max_total_drawdown"]), 0.0)

        if "max_concurrent_trades" in updates and updates["max_concurrent_trades"] is not None:
            self.max_concurrent_trades = max(int(updates["max_concurrent_trades"]), 1)

        if "active_revalidation_enabled" in updates and updates["active_revalidation_enabled"] is not None:
            self.active_revalidation_enabled = bool(updates["active_revalidation_enabled"])

        if (
            "active_revalidation_interval_minutes" in updates
            and updates["active_revalidation_interval_minutes"] is not None
        ):
            self.active_revalidation_interval_minutes = max(
                int(updates["active_revalidation_interval_minutes"]),
                15,
            )

        if "balance" in updates and updates["balance"] is not None:
            new_balance = float(updates["balance"])
            reset_hwm = bool(updates.get("reset_hwm", True))
            reset_daily = bool(updates.get("reset_daily_start", True))
            update_initial = bool(updates.get("update_initial_balance", True))
            self.set_balance(
                new_balance,
                reset_hwm=reset_hwm,
                reset_daily_start=reset_daily,
                update_initial_balance=update_initial,
            )

        # Cent mode SL/TP multipliers (configurable from dashboard)
        if "cent_sl_multiplier" in updates and updates["cent_sl_multiplier"] is not None:
            self._cent_sl_multiplier = max(float(updates["cent_sl_multiplier"]), 1.0)
        if "cent_tp_multiplier" in updates and updates["cent_tp_multiplier"] is not None:
            self._cent_tp_multiplier = max(float(updates["cent_tp_multiplier"]), 1.0)

        await self.save_state()
        return self.get_runtime_config()

    async def reset_runtime_config(self) -> dict:
        """Reset runtime config to startup defaults (non-destructive to trades)."""
        defaults = dict(self._default_config)
        self.mode = defaults["mode"]
        self.initial_balance = float(defaults["initial_balance"])
        self.risk_per_trade = float(defaults["risk_per_trade"])
        self.position_sizing_mode = str(defaults["position_sizing_mode"])
        self.fixed_lot_size = float(defaults["fixed_lot_size"])
        self.drawdown_guard_enabled = bool(defaults["drawdown_guard_enabled"])
        self.max_daily_drawdown = float(defaults["max_daily_drawdown"])
        self.max_total_drawdown = float(defaults["max_total_drawdown"])
        self.max_concurrent_trades = int(defaults["max_concurrent_trades"])
        self.active_revalidation_enabled = bool(defaults["active_revalidation_enabled"])
        self.active_revalidation_interval_minutes = int(defaults["active_revalidation_interval_minutes"])
        self._apply_challenge_mode(defaults.get("challenge_mode", "none"))
        self.set_balance(
            self.initial_balance,
            reset_hwm=True,
            reset_daily_start=True,
            update_initial_balance=True,
        )
        self._halted = False
        self._halt_reason = ""
        await self.save_state()
        return self.get_runtime_config()

    def set_balance(
        self,
        new_balance: float,
        *,
        reset_hwm: bool = True,
        reset_daily_start: bool = True,
        update_initial_balance: bool = True,
    ) -> None:
        bal = max(float(new_balance), 0.0)
        self.balance = bal
        if update_initial_balance:
            self.initial_balance = bal
        if reset_hwm:
            self.high_water_mark = bal
        if reset_daily_start:
            self.daily_start_balance = bal

    def _find_active_pair_by_trade_id(self, trade_id: str) -> str | None:
        """Return active pair for a trade_id, or None if not found."""
        tid = (trade_id or "").strip()
        if not tid:
            return None
        for pair, (trade, _mgr) in self._active.items():
            if trade.trade_id == tid:
                return pair
        return None

    async def manual_close_trade(
        self,
        trade_id: str,
        *,
        reason: str = "Manual close from dashboard",
    ) -> dict:
        """Manually close an active trade by trade_id.

        Uses the same close pipeline as auto-close so journal, DB, dashboard,
        and WA notifications stay consistent.
        """
        async with self._trade_lock:
            pair = self._find_active_pair_by_trade_id(trade_id)
            if not pair:
                raise KeyError(f"Active trade not found: {trade_id}")

            trade, _mgr = self._active[pair]
            try:
                exit_price = await get_current_price_async(pair)
            except Exception:
                # Fallback so manual close can still proceed if price endpoint is flaky.
                exit_price = trade.entry_price
                logger.warning(
                    "Manual close %s fallback to entry price due to price fetch failure",
                    trade_id,
                )

            return await self._close_trade(
                pair=pair,
                exit_price=exit_price,
                result="MANUAL_CLOSE",
                reason=reason,
            )

    # -- Drawdown -----------------------------------------------------------

    def _unrealised_pnl(self, price_cache: dict[str, float] | None = None) -> float:
        """Sum floating P/L across active trades in dollar terms.

        FIX F4-01: Drawdown check must include unrealised losses
        from open positions in the effective balance.

        Args:
            price_cache: Optional pre-fetched prices {pair: price}.
                         When provided, avoids sync HTTP calls (FIX §7.7).
        """
        total = 0.0
        for pair, (trade, _mgr) in self._active.items():
            try:
                if price_cache and pair in price_cache:
                    price = price_cache[pair]
                else:
                    price = get_current_price(pair)
                total += self.trade_floating_pnl(trade, price)
            except Exception:
                pass  # Can't get price — conservatively assume no floating P/L
        return total

    async def _prefetch_prices(self) -> dict[str, float]:
        """Pre-fetch current prices for all active pairs outside of lock (FIX §7.7).

        This prevents sync HTTP calls under _trade_lock.
        """
        prices: dict[str, float] = {}
        for pair in list(self._active.keys()):
            try:
                prices[pair] = await get_current_price_async(pair)
            except Exception as exc:
                logger.warning("Price prefetch failed for %s: %s", pair, exc)
        return prices

    async def _revalidate_trade_setup(
        self,
        pair: str,
        trade: ActiveTrade,
        current_price: float | None,
    ) -> tuple[bool, str]:
        """Periodic revalidation using Gemini Flash + heuristic fallback."""
        if not self.active_revalidation_enabled:
            return True, "Active revalidation disabled"

        now = datetime.now(timezone.utc)
        last = self._last_revalidation.get(pair)
        if last and (now - last).total_seconds() < self.active_revalidation_interval_minutes * 60:
            return True, "Interval not reached"

        self._last_revalidation[pair] = now
        try:
            analyses = await collect_multi_tf_async(pair, ["H1", "M15"], candle_count=120)
        except Exception as exc:
            note = f"Revalidation failed ({exc})"
            trade.last_revalidation_at = now
            trade.last_revalidation_note = note
            return True, note

        h1 = analyses.get("H1", {})
        m15 = analyses.get("M15", {})

        # ----- Gemini Flash structured revalidation -----
        try:
            from schemas.revalidation import RevalidationResult
            from agent.system_prompt import REVALIDATION_PROMPT_TEMPLATE

            rr_cur = trade.rr_current(current_price) if current_price else 0.0

            market_summary = []
            for tf_name, tf_data in [("H1", h1), ("M15", m15)]:
                if tf_data and "error" not in tf_data:
                    trend = tf_data.get("structure", {}).get("trend", "unknown")
                    choch_bull = tf_data.get("choch_micro_bullish", {}).get("confirmed", False)
                    choch_bear = tf_data.get("choch_micro_bearish", {}).get("confirmed", False)
                    market_summary.append(
                        f"{tf_name}: trend={trend}, choch_bull={choch_bull}, choch_bear={choch_bear}"
                    )
            market_data = "\n".join(market_summary) if market_summary else "No valid TF data"

            prompt = REVALIDATION_PROMPT_TEMPLATE.format(
                pair=pair,
                direction=trade.direction,
                entry_price=trade.entry_price,
                current_price=current_price or "N/A",
                stop_loss=trade.stop_loss,
                take_profit_1=trade.take_profit_1,
                take_profit_2=getattr(trade, "take_profit_2", "N/A"),
                rr_current=f"{rr_cur:.2f}",
                sl_moved_to_be=trade.sl_moved_to_be,
                trail_active=trade.trail_active,
                strategy_mode=trade.strategy_mode,
                confluence_score=trade.confluence_score,
                market_data=market_data,
            )

            if not self._gemini:
                raise RuntimeError("Gemini client not configured")
            resp = await self._gemini.agenerate_structured(
                state="ACTIVE",  # Maps to Flash model
                contents=prompt,
                schema=RevalidationResult,
            )
            text = resp.text
            if text:
                result = RevalidationResult.model_validate_json(text)
                action_str = f"[{result.recommended_action}]" if result.recommended_action != "hold" else ""
                note = (
                    f"Flash: valid={result.still_valid} conf={result.confidence:.0%} "
                    f"trend={result.structure_trend} {action_str} "
                    f"| {result.key_observations[:80]}"
                )
                trade.last_revalidation_at = now
                trade.last_revalidation_note = note.strip()
                logger.info("Revalidation %s: %s", pair, note)

                if not result.still_valid:
                    return False, f"Gemini Flash invalidated: {result.risk_factors[:100]}"
                return True, note

        except Exception as exc:
            logger.warning("Gemini Flash revalidation failed for %s, falling back to heuristic: %s", pair, exc)

        # ----- Heuristic fallback (original logic) -----
        primary = h1 if h1 and "error" not in h1 else m15
        if not primary or "error" in primary:
            note = f"No valid TF data for revalidation"
            trade.last_revalidation_at = now
            trade.last_revalidation_note = note
            return True, note

        trend = str(primary.get("structure", {}).get("trend", "unknown")).lower()
        direction = trade.direction.lower()

        structure_ok = True
        if direction == "buy" and trend == "bearish":
            structure_ok = False
        elif direction == "sell" and trend == "bullish":
            structure_ok = False

        if structure_ok:
            if direction == "buy":
                opp_choch = bool(primary.get("choch_micro_bearish", {}).get("confirmed", False))
            else:
                opp_choch = bool(primary.get("choch_micro_bullish", {}).get("confirmed", False))
            if opp_choch and current_price is not None and trade.rr_current(current_price) < 0:
                structure_ok = False

        note = (
            f"Heuristic: trend={trend}, rr={trade.rr_current(current_price):.2f}"
            if current_price is not None
            else f"Heuristic: trend={trend}"
        )
        trade.last_revalidation_at = now
        trade.last_revalidation_note = note
        if structure_ok:
            return True, note
        return False, f"Setup invalidated on periodic recheck ({note})"

    async def _prefetch_revalidations(
        self,
        prices: dict[str, float],
    ) -> dict[str, tuple[bool, str]]:
        results: dict[str, tuple[bool, str]] = {}
        for pair, (trade, _mgr) in list(self._active.items()):
            price = prices.get(pair)
            try:
                ok, reason = await self._revalidate_trade_setup(pair, trade, price)
            except Exception as exc:
                ok, reason = True, f"Revalidation error: {exc}"
            results[pair] = (ok, reason)
        return results

    def check_drawdown(self, price_cache: dict[str, float] | None = None) -> tuple[bool, str]:
        """Returns (ok, reason). Includes unrealised floating P/L (FIX F4-01)."""
        if not self.drawdown_guard_enabled:
            return (True, "Drawdown guard disabled")

        # FIX F4-01: Include unrealised P/L in drawdown calculation
        effective_balance = self.balance + self._unrealised_pnl(price_cache)

        if self.high_water_mark > 0:
            total_dd = (self.high_water_mark - effective_balance) / self.high_water_mark
            if total_dd >= self.max_total_drawdown:
                reason = (
                    f"⛔ TOTAL DRAWDOWN {total_dd:.1%} ≥ {self.max_total_drawdown:.0%}. "
                    f"Balance=${self.balance:.2f}, HWM=${self.high_water_mark:.2f}"
                )
                if not self._halted:  # first time — store for async notification
                    self._pending_halt_info = (
                        "TOTAL DRAWDOWN", total_dd, self.balance, self.high_water_mark,
                    )
                self._halted = True
                self._halt_reason = reason
                logger.warning(reason)
                return (False, reason)

        if self.daily_start_balance > 0:
            daily_dd = (self.daily_start_balance - effective_balance) / self.daily_start_balance
            if daily_dd >= self.max_daily_drawdown:
                reason = (
                    f"⛔ DAILY DRAWDOWN {daily_dd:.1%} ≥ {self.max_daily_drawdown:.0%}. "
                    f"Day start=${self.daily_start_balance:.2f}, now=${self.balance:.2f}"
                )
                if not self._halted:  # first time — store for async notification
                    self._pending_halt_info = (
                        "DAILY DRAWDOWN", daily_dd, self.balance, self.high_water_mark,
                    )
                self._halted = True
                self._halt_reason = reason
                logger.warning(reason)
                return (False, reason)

        return (True, "OK")

    def can_open_trade(self, price_cache: dict[str, float] | None = None) -> tuple[bool, str]:
        if self._halted:
            return (False, self._halt_reason)

        ok, reason = self.check_drawdown(price_cache)
        if not ok:
            return (False, reason)

        if self.active_count >= self.max_concurrent_trades:
            return (
                False,
                f"Max concurrent trades ({self.max_concurrent_trades}) reached",
            )

        return (True, "OK")

    # -- Scan callback: decides whether to open trade -----------------------

    async def on_scan_complete(
        self, pair: str, outcome: AnalysisOutcome,
    ) -> Optional[ActiveTrade]:
        """Called after orchestrator finishes scanning *pair*.

        If the outcome has a valid plan above threshold, and drawdown
        allows it, opens a new trade.

        Returns the ActiveTrade if opened, else None.
        """
        if not outcome.plan:
            return None

        s = outcome.plan.primary_setup
        if s.confluence_score < MIN_SCORE_FOR_TRADE:
            return None

        # FIX §7.7: Pre-fetch prices OUTSIDE lock to reduce contention
        prices = await self._prefetch_prices()

        # Lock prevents concurrent opens for the same pair
        async with self._trade_lock:
            # Already in a trade for this pair?
            if pair in self._active:
                logger.info("Skip %s — already in active trade", pair)
                return None

            # Cooldown: prevent reopening right after a close
            cooldown_until = self._pair_cooldown.get(pair)
            if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
                remaining = (cooldown_until - datetime.now(timezone.utc)).seconds
                logger.info("⏳ Skip %s — cooldown %ds remaining", pair, remaining)
                return None

            # Open trade with real market price (FIX §7.1: async price fetch)
            try:
                market_price = await get_current_price_async(pair)
            except Exception as exc:
                logger.warning(
                    "Skip %s - unable to fetch live price for entry check: %s",
                    pair,
                    exc,
                )
                return None

            in_zone, z_low, z_high, z_buf = self._is_price_in_entry_zone(
                pair,
                market_price,
                s.entry_zone_low,
                s.entry_zone_high,
            )

            # Helper: add setup to pending queue; returns PendingSetup if added
            def _add_to_pending(reason_log: str) -> "PendingSetup | None":
                direction = s.direction.value if hasattr(s.direction, "value") else s.direction
                rec_entry = compute_recommended_entry(
                    direction, s.entry_zone_low, s.entry_zone_high,
                )
                import uuid as _uuid
                pending = PendingSetup(
                    setup_id=f"PQ-{_uuid.uuid4().hex[:8]}",
                    pair=pair,
                    plan=outcome.plan,
                    direction=direction,
                    entry_zone_low=s.entry_zone_low,
                    entry_zone_high=s.entry_zone_high,
                    recommended_entry=rec_entry,
                    stop_loss=s.stop_loss,
                    take_profit_1=s.take_profit_1,
                    take_profit_2=s.take_profit_2,
                    confluence_score=s.confluence_score,
                    ttl_hours=getattr(s, 'ttl_hours', 0) if getattr(s, 'ttl_hours', 0) > 0 else PENDING_SETUP_DEFAULT_TTL_HOURS,
                )
                added = self._pending.add(pending)
                logger.info("%s — adding %s to pending queue", reason_log, pair)
                return pending if added else None

            if not in_zone:
                # FIX H-05: If halted due to drawdown, don't add to pending
                if self._halted:
                    logger.warning(
                        "⛔ BLOCKED %s: drawdown halt active, price outside zone — %s",
                        pair, self._halt_reason,
                    )
                    return None
                point = PAIR_POINT.get(pair, 0.0001)
                z_buf_pips = (z_buf / point) if point else 0.0
                _ps = _add_to_pending(
                    f"Price {market_price:.5f} outside entry zone "
                    f"{z_low:.5f}-{z_high:.5f} (buf={z_buf_pips:.2f}p)"
                )
                await self._save_pending_setups()
                if _ps and self._notify_pending_added:
                    try:
                        await self._notify_pending_added(
                            pair=_ps.pair, direction=_ps.direction,
                            zone_low=_ps.entry_zone_low, zone_high=_ps.entry_zone_high,
                            rec_entry=_ps.recommended_entry, ttl_hours=_ps.ttl_hours,
                        )
                    except Exception as _exc:
                        logger.warning("notify_pending_added failed: %s", _exc)
                return None

            # Price IS in zone — check if we can actually open
            ok, reason = self.can_open_trade(price_cache=prices)
            if not ok:
                # FIX H-05: If halted due to drawdown, BLOCK (don't add to pending
                # queue — pending will never execute while halted, creating false hope)
                if self._halted:
                    logger.warning(
                        "⛔ BLOCKED %s: drawdown halt active — %s", pair, reason,
                    )
                    return None
                # Non-halt reason (max concurrent etc.) → pending queue is appropriate
                _ps = _add_to_pending(
                    f"Price in zone but cannot open ({reason})"
                )
                await self._save_pending_setups()
                logger.warning("Cannot open trade for %s: %s — queued as pending", pair, reason)
                if _ps and self._notify_pending_added:
                    try:
                        await self._notify_pending_added(
                            pair=_ps.pair, direction=_ps.direction,
                            zone_low=_ps.entry_zone_low, zone_high=_ps.entry_zone_high,
                            rec_entry=_ps.recommended_entry, ttl_hours=_ps.ttl_hours,
                        )
                    except Exception as _exc:
                        logger.warning("notify_pending_added failed: %s", _exc)
                return None

            return await self._open_trade(outcome.plan, market_price=market_price)

    async def _open_trade(
        self,
        plan: TradingPlan,
        market_price: float | None = None,
    ) -> ActiveTrade:
        """Create ActiveTrade + TradeManager from a TradingPlan."""
        s = plan.primary_setup
        direction = s.direction.value if hasattr(s.direction, "value") else s.direction
        strategy = s.strategy_mode.value if hasattr(s.strategy_mode, "value") else s.strategy_mode

        # Use real market price for entry (Gemini plans may hallucinate)
        # FIX §7.1: Use async price fetch
        if market_price is not None:
            real_price = float(market_price)
        else:
            try:
                real_price = await get_current_price_async(plan.pair)
            except Exception:
                real_price = (s.entry_zone_low + s.entry_zone_high) / 2

        plan_entry = (s.entry_zone_low + s.entry_zone_high) / 2
        plan_sl = s.stop_loss
        plan_tp1 = s.take_profit_1
        plan_tp2 = s.take_profit_2 if s.take_profit_2 else s.take_profit_1

        # FIX F4-03: Per-pair price sanity threshold (was 5% for all)
        sanity_threshold = PRICE_SANITY_THRESHOLDS.get(plan.pair, PRICE_SANITY_DEFAULT)
        if plan_entry > 0 and abs(real_price - plan_entry) / plan_entry > sanity_threshold:
            logger.warning(
                "Price sanity fix %s: plan=%.5f real=%.5f → using real price",
                plan.pair, plan_entry, real_price,
            )
            # Keep the same risk/reward ratios from the plan
            risk = abs(plan_entry - plan_sl)
            reward1 = abs(plan_tp1 - plan_entry)
            reward2 = abs(plan_tp2 - plan_entry)

            if direction.lower() == "buy":
                entry_price = real_price
                stop_loss = real_price - risk
                tp1 = real_price + reward1
                tp2 = real_price + reward2
            else:
                entry_price = real_price
                stop_loss = real_price + risk
                tp1 = real_price - reward1
                tp2 = real_price - reward2
        else:
            entry_price = real_price  # Still use real price
            stop_loss = plan_sl
            tp1 = plan_tp1
            tp2 = plan_tp2

        # -- Minimum distance enforcement --
        # TradeManager checks "within 0.5×ATR of TP1" where ATR=initial_risk.
        # So TP1 must be > 0.5×risk from entry to prevent immediate trigger.
        # We enforce TP1 >= 1.0×risk for a sensible 1:1 R:R minimum.
        risk = abs(entry_price - stop_loss)
        min_tp_dist = risk  # TP1 at least 1:1 R:R from entry

        if direction.lower() == "buy":
            if tp1 - entry_price < min_tp_dist:
                old_tp1 = tp1
                tp1 = entry_price + min_tp_dist
                logger.warning(
                    "TP1 too close (%.1f < %.1f risk) — bumped %.5f → %.5f",
                    old_tp1 - entry_price, min_tp_dist, old_tp1, tp1,
                )
            if tp2 - entry_price < min_tp_dist * 2:
                tp2 = entry_price + min_tp_dist * 2

        else:
            if entry_price - tp1 < min_tp_dist:
                old_tp1 = tp1
                tp1 = entry_price - min_tp_dist
                logger.warning(
                    "TP1 too close (%.1f < %.1f risk) — bumped %.5f → %.5f",
                    entry_price - old_tp1, min_tp_dist, old_tp1, tp1,
                )
            if entry_price - tp2 < min_tp_dist * 2:
                tp2 = entry_price - min_tp_dist * 2

        # -- Cent mode SL/TP widening (wider spreads on cent accounts) --
        if self.challenge_mode == "challenge_cent":
            sl_mult = getattr(self, "_cent_sl_multiplier", CHALLENGE_CENT_SL_MULTIPLIER)
            tp_mult = getattr(self, "_cent_tp_multiplier", CHALLENGE_CENT_TP_MULTIPLIER)
            sl_dist = abs(entry_price - stop_loss)
            tp1_dist = abs(tp1 - entry_price)
            tp2_dist = abs(tp2 - entry_price)
            if direction.lower() == "buy":
                stop_loss = entry_price - sl_dist * sl_mult
                tp1 = entry_price + tp1_dist * tp_mult
                tp2 = entry_price + tp2_dist * tp_mult
            else:
                stop_loss = entry_price + sl_dist * sl_mult
                tp1 = entry_price - tp1_dist * tp_mult
                tp2 = entry_price - tp2_dist * tp_mult
            logger.info(
                "Cent mode SL/TP widened ×%.1f/×%.1f — SL=%.5f TP1=%.5f TP2=%.5f",
                sl_mult, tp_mult, stop_loss, tp1, tp2,
            )

        lot_size, risk_amount = self._compute_lot_and_risk(
            plan.pair,
            entry_price,
            stop_loss,
        )

        trade = ActiveTrade(
            trade_id=f"T-{uuid.uuid4().hex[:8]}",
            pair=plan.pair,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            lot_size=lot_size,
            risk_amount=risk_amount,
            # FIX F4-08/F4-09: Store setup context for PostMortem
            strategy_mode=strategy,
            confluence_score=s.confluence_score,
            voting_confidence=getattr(plan, "confidence", 0.0),
            htf_bias=getattr(plan, "htf_bias", ""),
            entry_zone_low=s.entry_zone_low,
            entry_zone_high=s.entry_zone_high,
            recommended_entry=getattr(s, "recommended_entry", None),
        )
        mgr = TradeManager(trade)

        self._active[plan.pair] = (trade, mgr)
        # FIX: Set revalidation timestamp at open so first revalidation
        # is deferred by the full interval (default 90 min).
        # Without this, the very first price_monitor_loop cycle (60s after open)
        # would immediately revalidate and potentially MANUAL_CLOSE the trade.
        self._last_revalidation[plan.pair] = datetime.now(timezone.utc)
        logger.info(
            "🔓 TRADE OPENED %s: %s %s @ %.5f  SL=%.5f  TP1=%.5f  TP2=%.5f",
            trade.trade_id, plan.pair, direction,
            trade.entry_price, trade.stop_loss,
            trade.take_profit_1, trade.take_profit_2,
        )
        logger.info(
            "Trade size locked %s: lot=%.3f risk=$%.2f mode=%s",
            trade.trade_id,
            trade.lot_size,
            trade.risk_amount,
            self.position_sizing_mode,
        )
        # Persist immediately so trades survive crashes
        await self.save_active_trades()
        # Notify: trade opened
        if self._notify_trade_opened:
            try:
                await self._notify_trade_opened(
                    pair=plan.pair,
                    direction=direction,
                    entry_price=trade.entry_price,
                    stop_loss=trade.stop_loss,
                    take_profit_1=trade.take_profit_1,
                    take_profit_2=trade.take_profit_2,
                    lot_size=trade.lot_size,
                    risk_usd=trade.risk_amount,
                )
            except Exception as _exc:
                logger.warning("notify_trade_opened failed: %s", _exc)
        return trade

    # -- Price monitoring loop ----------------------------------------------

    async def check_active_trades(self) -> list[dict]:
        """Check all active trades against current market price.

        Fetches M1 close for each active pair, runs TradeManager.evaluate(),
        applies actions, and handles closes.

        Returns list of close_result dicts for any trades that closed.
        """
        closed_results: list[dict] = []
        _trades_changed = False

        # Fire deferred drawdown halt notification (check_drawdown is sync)
        if self._pending_halt_info and not self._halt_notified and self._notify_drawdown_halt:
            try:
                halt_type, dd_pct, bal, hwm = self._pending_halt_info
                await self._notify_drawdown_halt(
                    halt_type=halt_type,
                    drawdown_pct=dd_pct,
                    balance=bal,
                    high_water_mark=hwm,
                )
                self._halt_notified = True
                self._pending_halt_info = None
            except Exception as _exc:
                logger.warning("notify_drawdown_halt failed: %s", _exc)

        # FIX §7.7: Pre-fetch prices OUTSIDE lock to avoid blocking
        prices = await self._prefetch_prices()
        revalidations = await self._prefetch_revalidations(prices)

        # FIX F4-06: Acquire trade lock to prevent race with on_scan_complete
        async with self._trade_lock:
            for pair in list(self._active.keys()):
                # FIX H-01: Guard against race where pair was closed between
                # iteration start and this point (e.g. concurrent manual_close).
                if pair not in self._active:
                    continue
                trade, mgr = self._active[pair]
                price = prices.get(pair)
                if price is None:
                    logger.warning("No pre-fetched price for %s — skip", pair)
                    continue

                structure_ok, reval_reason = revalidations.get(
                    pair, (True, "No revalidation data"),
                )
                if not structure_ok:
                    action = TradeAction(
                        action=ActionType.CLOSE_MANUAL,
                        reason=reval_reason,
                        close_percent=1.0,
                        urgency="high",
                    )
                else:
                    # Evaluate using the full TradeManager logic
                    # TODO (L-08): pass news_imminent from a calendar service
                    #   once _check_news_calendar() is implemented (masterplan §13 rules 6-7).
                    action = mgr.evaluate(price, atr=trade.initial_risk)

                if action.action in (
                    ActionType.SL_HIT,
                    ActionType.FULL_CLOSE,
                    ActionType.CLOSE_MANUAL,
                ):
                    # Trade is done
                    result_str = action.action.value
                    if action.action == ActionType.FULL_CLOSE:
                        result_str = "TP2_HIT"
                    elif action.action == ActionType.CLOSE_MANUAL:
                        result_str = "MANUAL_CLOSE"
                    close_result = await self._close_trade(
                        pair, price, result_str, action.reason,
                    )
                    closed_results.append(close_result)
                    _trades_changed = True

                elif action.action == ActionType.PARTIAL_TP1:
                    # --- FIX F4-04: Real 50% partial close at TP1 ---------------
                    # Previously did full close here "for simplicity".
                    # Now: close 50%, move SL to breakeven, keep remainder open.
                    close_fraction = min(max(action.close_percent, 0.0), trade.remaining_size)
                    if close_fraction <= 0:
                        continue
                    trade.partial_closed = True
                    tp1_fill = trade.take_profit_1
                    # Phase 4 Fix: Use pip-value-based P&L instead of R:R-based
                    point = PAIR_POINT.get(pair, 0.0001)
                    pip_val_lot = self._pip_value_per_lot(pair) * self._lot_value_multiplier
                    tp1_pips = (trade.floating_pnl(tp1_fill) / point) if point else 0.0
                    partial_pnl = tp1_pips * pip_val_lot * trade.lot_size * close_fraction
                    trade.remaining_size = max(trade.remaining_size - close_fraction, 0.0)
                    trade.realized_pnl += partial_pnl
                    self.balance += partial_pnl
                    if self.balance > self.high_water_mark:
                        self.high_water_mark = self.balance
                    _trades_changed = True

                    # Move SL to breakeven for the remaining 50%
                    if not trade.sl_moved_to_be:
                        old_sl = trade.stop_loss
                        trade.stop_loss = trade.entry_price
                        trade.sl_moved_to_be = True
                        logger.info(
                            "TP1 partial 50%% %s: pnl=$%.2f, SL→BE %.5f→%.5f",
                            pair, partial_pnl, old_sl, trade.stop_loss,
                        )
                    else:
                        logger.info(
                            "TP1 partial 50%% %s: pnl=$%.2f (SL already at BE)",
                            pair, partial_pnl,
                        )
                    # ---------------------------------------------------------------

                elif action.action == ActionType.SL_PLUS_BE:
                    old_sl = trade.stop_loss
                    mgr.apply_action(action)
                    logger.info("SL+ %s: %.5f → %.5f", pair, old_sl, trade.stop_loss)
                    _trades_changed = True
                    if self._notify_sl_moved:
                        try:
                            await self._notify_sl_moved(pair, old_sl, trade.stop_loss)
                        except Exception:
                            pass

                elif action.action == ActionType.TRAIL:
                    old_sl = trade.stop_loss
                    mgr.apply_action(action)
                    logger.info("TRAIL %s: %.5f → %.5f", pair, old_sl, trade.stop_loss)
                    _trades_changed = True

                # ActionType.HOLD → do nothing

        # Persist trade state changes (SL moves, partial closes, removes)
        if _trades_changed:
            await self.save_active_trades()
            await self.save_state()

        return closed_results

    # -- Close trade (full pipeline) ----------------------------------------

    async def _close_trade(
        self, pair: str, exit_price: float, result: str, reason: str,
    ) -> dict:
        """Close a trade with full pipeline: P/L, PostMortem, DB, Dashboard, WA."""
        trade, _mgr = self._active.pop(pair)
        self._last_revalidation.pop(pair, None)

        # Set cooldown: prevent immediate reopen of this pair
        self._pair_cooldown[pair] = datetime.now(timezone.utc) + timedelta(
            minutes=self._cooldown_minutes
        )

        # Calculate P/L
        pips_raw = trade.floating_pnl(exit_price)
        point = PAIR_POINT.get(pair, 0.0001)
        pips = pips_raw / point if point else pips_raw
        rr = trade.rr_current(exit_price)
        # FIX L-04: Guard against None opened_at (corrupt restore edge case)
        if trade.opened_at:
            duration = int(
                (datetime.now(timezone.utc) - trade.opened_at).total_seconds() / 60
            )
        else:
            duration = 0

        remaining_size = max(trade.remaining_size, 0.0)

        # --- Phase 4 P&L Fix: Robust final_leg_pnl calculation --------
        # Compute actual dollar P&L from price movement for the remaining leg.
        # This replaces the old R:R-based approach that could produce
        # wildly wrong numbers when exit_price drifted from expected level.
        pip_val_lot = self._pip_value_per_lot(pair) * self._lot_value_multiplier
        point = PAIR_POINT.get(pair, 0.0001)
        price_pnl_pips = (trade.floating_pnl(exit_price) / point) if point else 0.0
        actual_dollar_pnl = price_pnl_pips * pip_val_lot * trade.lot_size * remaining_size

        if result in ("TP1_HIT", "TP2_HIT"):
            # Use the TP level as exit, not the monitoring-loop price which
            # may have overshot.  Recalculate from the planned TP.
            tp_price = (
                trade.take_profit_1 if result == "TP1_HIT"
                else (trade.take_profit_2 or trade.take_profit_1)
            )
            tp_pnl_pips = (trade.floating_pnl(tp_price) / point) if point else 0.0
            final_leg_pnl = tp_pnl_pips * pip_val_lot * trade.lot_size * remaining_size
        elif result == "BE_HIT":
            final_leg_pnl = 0.0
        elif result == "SL_HIT":
            # Distinguish trailing-SL-above-BE from original SL and true BE hit.
            if trade.trail_active and trade.sl_moved_to_be:
                # Trailing stop was above breakeven → real profit locked in
                final_leg_pnl = actual_dollar_pnl
                if actual_dollar_pnl > 0:
                    result = "TRAIL_PROFIT"
                    logger.info(
                        "Trailing SL hit in profit %s — pnl=$%.2f", pair, actual_dollar_pnl
                    )
                else:
                    result = "BE_HIT"
                    final_leg_pnl = 0.0
                    logger.info(
                        "SL hit on BE position %s — reclassified to BE_HIT", pair
                    )
            elif trade.sl_moved_to_be:
                # SL was moved to BE (not trailed further) → ~$0
                final_leg_pnl = 0.0
                result = "BE_HIT"
                logger.info(
                    "SL hit on BE position %s — reclassified to BE_HIT", pair
                )
            else:
                # Original SL hit → use actual price-based loss
                final_leg_pnl = actual_dollar_pnl  # will be negative
        else:  # MANUAL_CLOSE, CANCELLED
            final_leg_pnl = actual_dollar_pnl
        # --- End Phase 4 P&L Fix ------------------------------------

        pnl = trade.realized_pnl + final_leg_pnl
        self.balance += final_leg_pnl
        if self.balance > self.high_water_mark:
            self.high_water_mark = self.balance

        close_result = {
            "trade_id": trade.trade_id,
            "pair": pair,
            "direction": trade.direction,
            # FIX F4-09: Use actual values from trade context (was hardcoded)
            "strategy_mode": trade.strategy_mode or "sniper_confluence",
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "stop_loss": trade.original_sl,
            "take_profit_1": trade.take_profit_1,
            "take_profit_2": trade.take_profit_2,
            "final_sl": trade.stop_loss,
            "result": result,
            "reason": reason,
            "pips": round(pips, 1),
            "rr_achieved": round(rr, 2),
            "duration_minutes": duration,
            "pnl": round(pnl, 2),
            "pnl_total": round(pnl, 2),
            "realized_partial_pnl": round(trade.realized_pnl, 2),
            "final_leg_pnl": round(final_leg_pnl, 2),
            "lot_size": round(trade.lot_size, 4),
            "risk_amount": round(trade.risk_amount, 2),
            "remaining_size_at_close": round(remaining_size, 4),
            "balance_after": round(self.balance, 2),
            "confluence_score": trade.confluence_score,
            "voting_confidence": trade.voting_confidence,
            "sl_was_moved_be": trade.sl_moved_to_be,
            "sl_trail_applied": trade.trail_active,
            "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
        }

        self._closed_today.append(close_result)

        logger.info(
            "🔒 TRADE CLOSED %s: %s %s result=%s pips=%.1f pnl=$%.2f bal=$%.2f (%s)",
            trade.trade_id, pair, trade.direction,
            result, round(pips, 1), round(pnl, 2), round(self.balance, 2), reason,
        )

        # 1. PostMortem — FIX F4-08: Populate MarketContext more fully
        is_structure_break = (result == "MANUAL_CLOSE" and "CHOCH" in reason.upper())
        market_ctx = MarketContext(
            atr_at_entry=trade.initial_risk,  # Best available approximation
            sl_was_moved_be=trade.sl_moved_to_be,
            sl_trail_applied=trade.trail_active,
            structure_intact=not is_structure_break,
            choch_occurred=is_structure_break,
            htf_bias_at_entry=trade.htf_bias,
            entry_zone_type=trade.entry_zone_type,
        )
        post_mortem = self._post_mortem.generate(
            trade_id=trade.trade_id,
            pair=pair,
            direction=trade.direction,
            entry_price=trade.entry_price,
            exit_price=exit_price,
            stop_loss=trade.original_sl,
            take_profit_1=trade.take_profit_1,
            result=result,
            pips=pips,
            duration_minutes=duration,
            # FIX F4-09: Pass actual strategy/scores (was omitted → defaulted 0)
            strategy_mode=trade.strategy_mode,
            confluence_score=trade.confluence_score,
            voting_confidence=trade.voting_confidence,
            context=market_ctx,
        )
        close_result["post_mortem"] = post_mortem.to_dict()

        logger.info(
            "📝 PostMortem: %s — what_worked=%s, lessons=%s",
            trade.trade_id,
            post_mortem.what_worked[:2],
            post_mortem.lessons[:2],
        )

        # 2. DB persistence
        try:
            db_trade = Trade(
                trade_id=trade.trade_id,
                pair=pair,
                direction=trade.direction,
                strategy_mode=close_result["strategy_mode"],
                mode=self.mode,
                entry_price=trade.entry_price,
                stop_loss=trade.original_sl,
                take_profit_1=trade.take_profit_1,
                take_profit_2=trade.take_profit_2,
                exit_price=exit_price,
                result=result,
                pips=round(pips, 1),
                rr_achieved=round(rr, 2),
                duration_minutes=duration,
                sl_was_moved_be=trade.sl_moved_to_be,
                sl_trail_applied=trade.trail_active,
                final_sl=trade.stop_loss,
                demo_pnl=round(pnl, 2),
                demo_balance_after=round(self.balance, 2),
                post_mortem_json=post_mortem.to_json(),
                opened_at=trade.opened_at,
                closed_at=datetime.now(timezone.utc),
            )
            await self._repo.save_trade(db_trade)
            logger.info("💾 Trade saved to DB: %s", trade.trade_id)
        except Exception as exc:
            logger.error("DB save failed: %s", exc)

        # 3. Dashboard push
        if self._push_trade_closed:
            try:
                await self._push_trade_closed(close_result)
            except Exception as exc:
                logger.error("Dashboard push failed: %s", exc)

        # 4. WhatsApp notification
        if self._notify_trade_closed:
            try:
                await self._notify_trade_closed(
                    pair=pair,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    exit_price=exit_price,
                    pips=round(pips, 1),
                    duration_minutes=duration,
                    strategy_mode=close_result["strategy_mode"],
                    lesson=post_mortem.lessons[0] if post_mortem.lessons else "N/A",
                )
            except Exception as exc:
                logger.error("WA notification failed: %s", exc)

        # 5. Check drawdown
        self.check_drawdown()

        # 6. Persist state
        await self.save_state()

        return close_result

    # -- Daily wrapup -------------------------------------------------------

    async def daily_wrapup(self) -> dict:
        """End-of-day: compute real stats, persist, return summary."""
        summary = self.daily_summary()

        # FIX H-04: Persist active trades BEFORE any state changes
        # Ensures no data loss if crash occurs during wrapup
        await self.save_active_trades()

        # Persist current state
        await self.save_state()

        # Get DB stats for 30-day rolling
        try:
            db_stats = await self._repo.trade_stats(mode=self.mode)
        except Exception:
            db_stats = {"winrate": 0.0, "avg_pips": 0.0}

        summary["winrate_30d"] = db_stats.get("winrate", 0.0)
        summary["avg_pips_30d"] = db_stats.get("avg_pips", 0.0)

        logger.info(
            "📊 Daily summary: trades=%d pnl=$%.2f balance=$%.2f halted=%s",
            summary["trades_today"], summary["daily_pnl"],
            summary["balance"], summary["halted"],
        )
        return summary

    def daily_summary(self) -> dict:
        """Compute today's summary stats."""
        wins = sum(1 for t in self._closed_today if t.get("pnl", 0) > 0)
        losses = sum(1 for t in self._closed_today if t.get("pnl", 0) < 0)
        total_pips = sum(t.get("pips", 0) for t in self._closed_today)
        total_pnl = sum(t.get("pnl", 0) for t in self._closed_today)

        return {
            "date": datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d"),
            "trades_today": len(self._closed_today),
            "wins": wins,
            "losses": losses,
            "winrate": wins / len(self._closed_today) if self._closed_today else 0.0,
            "total_pips": round(total_pips, 1),
            "daily_pnl": round(total_pnl, 2),
            "balance": round(self.balance, 2),
            "high_water_mark": round(self.high_water_mark, 2),
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "active_trades": list(self._active.keys()),
            "closed_trades": [
                {
                    "trade_id": t["trade_id"],
                    "pair": t["pair"],
                    "direction": t["direction"],
                    "result": t["result"],
                    "pips": t["pips"],
                    "pnl": t["pnl"],
                    "post_mortem_lessons": t.get("post_mortem", {}).get("lessons", []),
                }
                for t in self._closed_today
            ],
        }

    def reset_daily(self) -> None:
        """Reset for new trading day.

        FIX F4-02: Added forex market hours awareness.
        - Forex runs 24/5 (Sun 17:00 ET → Fri 17:00 ET).
        - "New day" is at 00:00 UTC (17:00 ET rollover).
        - Skip reset on Saturday/Sunday when market is closed.
        - Always reset _closed_today and daily drawdown tracking.
        """
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # 0=Mon, 5=Sat, 6=Sun

        # Don't reset on weekends — market is closed, no new trading day
        if weekday in (5, 6):  # Saturday or Sunday
            logger.info(
                "📅 Skip daily reset — weekend (day=%d). "
                "Market closed. Preserving drawdown state.", weekday,
            )
            return

        self.daily_start_balance = self.balance
        self._closed_today.clear()
        if self._halted and "DAILY" in self._halt_reason.upper():
            self._halted = False
            self._halt_reason = ""
            logger.info("🔓 Daily halt lifted — new trading day")

        # FIX D-05: Reset Gemini daily cost counters so budget_exceeded
        # doesn't persist across trading days.
        if self._gemini and hasattr(self._gemini, "reset_daily_cost"):
            self._gemini.reset_daily_cost()

        logger.info(
            "📅 Daily reset: balance=$%.2f (weekday=%d)", self.balance, weekday
        )

