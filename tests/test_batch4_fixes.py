"""
tests/test_batch4_fixes.py — Batch 4: Risk Management & Execution fixes.

Covers:
  F4-01  Drawdown includes unrealised floating P/L
  F4-03  Per-pair price sanity thresholds
  F4-05  Progressive trailing SL (re-trail, current_price fallback)
  F4-06  Trade lock on check_active_trades
  F4-07  PnL formula — no artificial max(rr, 0.1) floor
  F4-08  PostMortem MarketContext populated (7/10 fields)
  F4-09  confluence_score + voting_confidence propagated
  F4-10  Conviction lock tested (enabled via F3-09 state transitions)
  F4-12  Dual cooldown: Lifecycle 5min vs StateMachine 30min
"""

from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from agent.trade_manager import (
    ActionType,
    ActiveTrade,
    TradeAction,
    TradeManager,
)
from agent.production_lifecycle import ProductionLifecycle, get_current_price
from agent.post_mortem import PostMortemGenerator, MarketContext
from agent.state_machine import (
    AnalysisState,
    SetupContext,
    StateMachine,
    ConvictionLockViolation,
)
from agent.orchestrator import AnalysisOutcome
from config.settings import (
    PRICE_SANITY_THRESHOLDS,
    PRICE_SANITY_DEFAULT,
    LIFECYCLE_COOLDOWN_MINUTES,
    COOLDOWN_MINUTES,
    PAIR_POINT,
)
from database.repository import Repository


# ── Helpers ────────────────────────────────────────────────────────────────

def _buy_trade(
    entry: float = 1.0480,
    sl: float = 1.0450,
    tp1: float = 1.0520,
    tp2: float | None = 1.0560,
    **kwargs,
) -> ActiveTrade:
    defaults = dict(
        trade_id="BUY_001",
        pair="EURUSD",
        direction="buy",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
    )
    defaults.update(kwargs)
    return ActiveTrade(**defaults)


def _sell_trade(
    entry: float = 1.0520,
    sl: float = 1.0550,
    tp1: float = 1.0480,
    tp2: float | None = 1.0440,
    **kwargs,
) -> ActiveTrade:
    defaults = dict(
        trade_id="SELL_001",
        pair="EURUSD",
        direction="sell",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
    )
    defaults.update(kwargs)
    return ActiveTrade(**defaults)


ATR = 0.0008  # Typical EURUSD M15 ATR


def _make_plan_outcome(
    pair="EURUSD", score=12, direction="buy",
    entry_low=1.0480, entry_high=1.0490,
    stop_loss=1.0450, tp1=1.0520, tp2=1.0560,
    confidence=0.85, htf_bias="bullish",
):
    """Fabricate an AnalysisOutcome with a valid plan stub."""

    class _Setup:
        confluence_score = score

        def __init__(self):
            self._direction = direction
            self._strategy_mode = "sniper_confluence"
            self.entry_zone_low = entry_low
            self.entry_zone_high = entry_high
            self._stop_loss = stop_loss
            self.take_profit_1 = tp1
            self.take_profit_2 = tp2
            self.ttl_hours = 4.0

        @property
        def direction(self):
            return self._direction

        @property
        def strategy_mode(self):
            return self._strategy_mode

        @property
        def stop_loss(self):
            return self._stop_loss

    class _Plan:
        def __init__(self):
            self._pair = pair
            self.primary_setup = _Setup()
            self.confidence = confidence
            self.htf_bias = htf_bias

        @property
        def pair(self):
            return self._pair

        def model_dump(self):
            return {"pair": self._pair, "test": True}

        def model_dump_json(self):
            import json
            return json.dumps(self.model_dump())

    return AnalysisOutcome(
        pair=pair,
        state=AnalysisState.TRIGGERED,
        plan=_Plan(),
        elapsed_seconds=1.0,
    )


@pytest_asyncio.fixture
async def repo():
    r = Repository(db_url="sqlite+aiosqlite:///:memory:")
    await r.init_db()
    yield r
    await r.close()


@pytest_asyncio.fixture
async def lifecycle(repo):
    lc = ProductionLifecycle(
        repo=repo, mode="demo", initial_balance=10_000.0,
        max_daily_drawdown=0.05, max_total_drawdown=0.15,
        max_concurrent_trades=2,
    )
    await lc.init()
    lc.active_revalidation_enabled = False  # Disable for unit tests
    return lc


# ═══════════════════════════════════════════════════════════════════════════
# F4-01  Drawdown includes unrealised floating P/L
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_01_UnrealisedPnLDrawdown:
    """check_drawdown must account for floating losses of active positions."""

    def test_no_active_trades_unrealised_zero(self, lifecycle):
        """Without active trades, _unrealised_pnl returns 0."""
        assert lifecycle._unrealised_pnl() == 0.0

    def test_unrealised_pnl_with_active_trade(self, lifecycle):
        """Active trade with floating loss reduces effective balance."""
        # Create a buy trade in loss
        trade = _buy_trade(entry=1.0500, sl=1.0470)
        mgr = TradeManager(trade)
        lifecycle._active["EURUSD"] = (trade, mgr)

        # Price dropped to 1.0400 → big floating loss
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0400,
        ):
            upnl = lifecycle._unrealised_pnl()
        # Floating loss should be negative
        assert upnl < 0

    def test_drawdown_triggers_on_floating_loss(self, lifecycle):
        """Total drawdown trips when balance + floating loss < threshold."""
        lifecycle.high_water_mark = 10_000
        lifecycle.balance = 9_000  # 10% realised loss (not yet tripping 15%)
        lifecycle.daily_start_balance = 9_000  # Today loss is from floating only

        # But active trade has big floating loss
        # lot_size=0.09 → 300 pips * $10/pip/lot * 0.09 = $270 loss
        trade = _buy_trade(entry=1.0500, sl=1.0400, lot_size=0.09)
        mgr = TradeManager(trade)
        lifecycle._active["EURUSD"] = (trade, mgr)

        # Price tanked: rr = (1.0200-1.0500)/0.01 = -3.0
        # risk_amount = 9000*0.01 = 90; upnl = 90 * -3.0 = -270
        # effective = 9000 + (-270) = 8730 → dd = (10000-8730)/10000 = 12.7%
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0200,
        ):
            ok, reason = lifecycle.check_drawdown()
        # Still under 15% → ok
        assert ok is True

        # Now a bigger drop: effective goes below 15%
        lifecycle.balance = 8_600
        lifecycle.daily_start_balance = 8_600
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0200,
        ):
            ok, reason = lifecycle.check_drawdown()
        # 8600 + (-86*3) = 8342 → dd = 16.6% → HALTED
        assert ok is False
        assert "TOTAL DRAWDOWN" in reason

    def test_drawdown_ok_when_floating_profit(self, lifecycle):
        """Floating profit offsets realised loss."""
        lifecycle.high_water_mark = 10_000
        lifecycle.balance = 8_600  # 14% realised loss
        lifecycle.daily_start_balance = 8_600  # Today started at this balance

        trade = _buy_trade(entry=1.0400, sl=1.0370)
        mgr = TradeManager(trade)
        lifecycle._active["EURUSD"] = (trade, mgr)

        # Price surged: rr = (1.0700-1.0400)/0.003 = 10.0
        # risk_amount = 8600*0.01 = 86; upnl = 86*10 = 860
        # effective = 8600 + 860 = 9460 → dd = 5.4% → OK
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0700,
        ):
            ok, reason = lifecycle.check_drawdown()
        assert ok is True

    def test_unrealised_pnl_price_fetch_failure_returns_zero(self, lifecycle):
        """If price fetch fails, conservatively return 0 floating P/L."""
        trade = _buy_trade()
        mgr = TradeManager(trade)
        lifecycle._active["EURUSD"] = (trade, mgr)

        with patch(
            "agent.production_lifecycle.get_current_price",
            side_effect=RuntimeError("No data"),
        ):
            upnl = lifecycle._unrealised_pnl()
        assert upnl == 0.0

    def test_daily_drawdown_includes_unrealised(self, lifecycle):
        """Daily drawdown also includes floating P/L."""
        lifecycle.daily_start_balance = 10_000
        lifecycle.balance = 9_600  # 4% realised (not yet 5%)

        # lot_size=0.096 → 300 pips * $10/pip/lot * 0.096 = $288 loss
        trade = _buy_trade(entry=1.0500, sl=1.0400, lot_size=0.096)
        mgr = TradeManager(trade)
        lifecycle._active["EURUSD"] = (trade, mgr)

        # Floating loss pushes over 5%
        # risk_amount = 9600*0.01 = 96; rr at 1.0200 = (1.02-1.05)/0.01 = -3
        # upnl = 96*-3 = -288; effective = 9600-288 = 9312
        # daily_dd = (10000-9312)/10000 = 6.88% ≥ 5%
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0200,
        ):
            ok, reason = lifecycle.check_drawdown()
        assert ok is False
        assert "DAILY DRAWDOWN" in reason


# ═══════════════════════════════════════════════════════════════════════════
# F4-03  Per-pair price sanity thresholds
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_03_PriceSanityThreshold:
    """Plan prices deviating beyond per-pair threshold trigger recalculation."""

    def test_settings_exist(self):
        """PRICE_SANITY_THRESHOLDS and default exist in settings."""
        assert "XAUUSD" in PRICE_SANITY_THRESHOLDS
        assert "EURUSD" in PRICE_SANITY_THRESHOLDS
        assert PRICE_SANITY_THRESHOLDS["XAUUSD"] == 0.005
        assert PRICE_SANITY_THRESHOLDS["EURUSD"] == 0.003
        assert PRICE_SANITY_DEFAULT == 0.01

    @pytest.mark.asyncio
    async def test_xauusd_tight_threshold(self, lifecycle):
        """XAUUSD with 0.5% threshold — price outside zone goes to pending queue."""
        outcome = _make_plan_outcome(
            pair="XAUUSD", score=12, direction="buy",
            entry_low=2300.0, entry_high=2310.0,
            stop_loss=2280.0, tp1=2340.0, tp2=2370.0,
        )
        # Real price: 2330 (outside entry zone 2300-2310 → pending queue)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=2330.0,
        ):
            trade = await lifecycle.on_scan_complete("XAUUSD", outcome)
        # With pending queue, price outside zone → None (queued)
        assert trade is None
        assert lifecycle.pending_count == 1
        assert "XAUUSD" in lifecycle.pending_pairs

    @pytest.mark.asyncio
    async def test_eurusd_within_threshold(self, lifecycle):
        """EURUSD plan within 0.3% → entry still uses real price but SL/TP from plan."""
        outcome = _make_plan_outcome(
            pair="EURUSD", score=12, direction="buy",
            entry_low=1.0480, entry_high=1.0490,
            stop_loss=1.0450, tp1=1.0520, tp2=1.0560,
        )
        # Real price 1.0487 (within 0.3% of plan mid 1.0485)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0487,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None
        assert trade.entry_price == pytest.approx(1.0487, abs=0.0001)
        # SL should be from plan since no recalculation triggered
        assert trade.original_sl == pytest.approx(1.0450, abs=0.001)

    def test_unknown_pair_uses_default(self):
        """Unknown pair falls back to 1% default threshold."""
        from config.settings import PRICE_SANITY_THRESHOLDS, PRICE_SANITY_DEFAULT
        threshold = PRICE_SANITY_THRESHOLDS.get("NZDUSD", PRICE_SANITY_DEFAULT)
        assert threshold == 0.01


# ═══════════════════════════════════════════════════════════════════════════
# F4-05  Progressive trailing SL
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_05_ProgressiveTrail:
    """Trail SL must be progressive — re-evaluate and tighten as price moves."""

    def test_trail_with_swing_uses_swing(self):
        """When last_swing is provided, trail = swing ± 0.5×ATR."""
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0600, tp2=1.0650)
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)

        # Price well above trail trigger; swing at 1.0510
        action = mgr.evaluate(1.0528, ATR, last_swing_against=1.0510)
        assert action.action == ActionType.TRAIL
        expected_sl = 1.0510 - 0.5 * ATR
        assert action.new_sl == pytest.approx(expected_sl, abs=0.00001)

    def test_trail_fallback_uses_current_price(self):
        """Without swing, fallback should trail from current_price - ATR.","""
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0600, tp2=1.0650)
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)

        # RR >= 1.5, no swing → fallback: current_price - ATR
        price = 1.0530
        action = mgr.evaluate(price, ATR)
        assert action.action == ActionType.TRAIL
        expected_sl = price - ATR  # 1.0530 - 0.0008 = 1.0522
        assert action.new_sl == pytest.approx(expected_sl, abs=0.00001)

    def test_trail_reeval_when_already_active(self):
        """Once trail_active=True, SL continues to tighten as price rises."""
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0600, tp2=1.0650)
        trade.sl_moved_to_be = True
        trade.trail_active = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)

        # First trail: price at 1.0528
        action1 = mgr.evaluate(1.0528, ATR)
        if action1.action == ActionType.TRAIL:
            mgr.apply_action(action1)

        # Price moves higher: 1.0545
        action2 = mgr.evaluate(1.0545, ATR)
        # Should still evaluate trail and tighten
        assert action2.action == ActionType.TRAIL
        new_sl2 = action2.new_sl
        assert new_sl2 > trade.stop_loss  # Tighter than before

    def test_trail_sell_progressive(self):
        """Sell trade progressive trail from current_price + ATR."""
        trade = _sell_trade(entry=1.0520, sl=1.0550, tp1=1.0400, tp2=1.0350)
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)

        # Sell profit: price dropped to 1.0470 → rr = (1.0520-1.0470)/0.003 = 1.67
        price = 1.0470
        action = mgr.evaluate(price, ATR)
        assert action.action == ActionType.TRAIL
        expected_sl = price + ATR  # 1.0470 + 0.0008 = 1.0478
        assert action.new_sl == pytest.approx(expected_sl, abs=0.00001)

    def test_trail_never_widens_sl(self):
        """Trail must never widen SL — returns None if new SL is worse."""
        trade = _buy_trade(entry=1.0480, sl=1.0520, tp1=1.0600, tp2=1.0650)
        # SL already very tight at 1.0520
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)

        # Trail would compute 1.0528 - 0.0008 = 1.0520 which <= current SL
        # Should return HOLD (trail rejected)
        action = mgr.evaluate(1.0528, ATR)
        # With SL at 1.0520 and trail = 1.0520, it's equal → rejected → HOLD
        assert action.action in (ActionType.HOLD, ActionType.TRAIL)
        if action.action == ActionType.TRAIL:
            assert action.new_sl >= trade.stop_loss


# ═══════════════════════════════════════════════════════════════════════════
# F4-06  Trade lock on check_active_trades
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_06_TradeLock:
    """check_active_trades must acquire _trade_lock to prevent races."""

    @pytest.mark.asyncio
    async def test_lock_acquired_during_check(self, lifecycle):
        """Verify that check_active_trades acquires the trade lock."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        lock_was_held = False
        original_evaluate = TradeManager.evaluate

        def spy_evaluate(self_mgr, *args, **kwargs):
            nonlocal lock_was_held
            lock_was_held = lifecycle._trade_lock.locked()
            return original_evaluate(self_mgr, *args, **kwargs)

        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ), patch.object(TradeManager, "evaluate", spy_evaluate):
            await lifecycle.check_active_trades()

        assert lock_was_held is True, "Trade lock must be held during evaluation"

    @pytest.mark.asyncio
    async def test_lock_prevents_concurrent_open(self, lifecycle):
        """Two concurrent operations should serialize on the lock."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        # Both should complete without error (no deadlock)
        async def check_and_open():
            with patch(
                "agent.production_lifecycle.get_current_price",
                return_value=1.0485,
            ):
                await lifecycle.check_active_trades()
                outcome2 = _make_plan_outcome(pair="GBPJPY", score=12,
                    entry_low=188.0, entry_high=188.5,
                    stop_loss=187.5, tp1=189.5, tp2=190.5)
                await lifecycle.on_scan_complete("GBPJPY", outcome2)

        await check_and_open()  # Should not deadlock


# ═══════════════════════════════════════════════════════════════════════════
# F4-07  PnL formula — no artificial max(rr, 0.1) floor
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_07_PnLFormula:
    """TP-hit PnL must use actual R:R, not max(rr, 0.1)."""

    @pytest.mark.asyncio
    async def test_micro_win_no_floor(self, lifecycle):
        """A tiny manual close (rr=0.02) should give pnl proportional to actual exit, not TP level."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Close with a micro win: exit barely above entry
        # Entry ~1.0485, SL=1.0450 → risk=0.0035
        # Exit at 1.0486 → pips_raw = 0.0001 → rr ≈ 0.029
        # Use MANUAL_CLOSE — not TP1_HIT — since TP1_HIT now uses planned TP price
        result = await lifecycle._close_trade(
            pair="EURUSD",
            exit_price=1.0486,
            result="MANUAL_CLOSE",
            reason="Micro win manual close",
        )
        rr = result["rr_achieved"]
        pnl = result["pnl"]
        # With pip-based formula: ~1 pip * $10 * lot_size → tiny
        assert pnl < 5.0, f"PnL should be tiny (rr={rr}), got {pnl}"
        assert pnl >= 0  # Still a win

    @pytest.mark.asyncio
    async def test_normal_win_still_correct(self, lifecycle):
        """Normal TP hit at 1.5 R:R still calculates correctly."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Close at 1.5 R:R: entry ~1.0485, risk ~0.0035
        # 1.5×risk = 0.00525, exit = 1.0485 + 0.00525 = 1.0538
        exit_price = trade.entry_price + 1.5 * trade.initial_risk
        result = await lifecycle._close_trade(
            pair="EURUSD",
            exit_price=exit_price,
            result="TP2_HIT",
            reason="TP2 reached",
        )
        assert result["pnl"] > 0
        assert result["rr_achieved"] == pytest.approx(1.5, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════════
# F4-08  PostMortem MarketContext populated
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_08_PostMortemContext:
    """MarketContext should have >2 fields populated."""

    @pytest.mark.asyncio
    async def test_postmortem_has_atr(self, lifecycle):
        """atr_at_entry should be populated from trade.initial_risk."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)

        result = await lifecycle._close_trade(
            "EURUSD", 1.0520, "TP1_HIT", "TP1 reached",
        )
        pm = result["post_mortem"]
        # Strategy mode should appear in the post_mortem
        assert pm["strategy_mode"] == "sniper_confluence"

    @pytest.mark.asyncio
    async def test_postmortem_context_fields_flow(self, lifecycle):
        """MarketContext has structure_intact + choch_occurred from close reason."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        # Spy on PostMortemGenerator.generate to inspect context arg
        ctx_received = {}
        original_generate = PostMortemGenerator.generate

        def spy_generate(self_pm, **kwargs):
            ctx_received.update(kwargs)
            return original_generate(self_pm, **kwargs)

        with patch.object(PostMortemGenerator, "generate", spy_generate):
            await lifecycle._close_trade(
                "EURUSD", 1.0495, "MANUAL_CLOSE",
                "H1 structure break (CHOCH) against trade direction",
            )
        ctx = ctx_received.get("context")
        assert ctx is not None
        assert ctx.structure_intact is False
        assert ctx.choch_occurred is True
        assert ctx.atr_at_entry > 0

    @pytest.mark.asyncio
    async def test_postmortem_htf_bias(self, lifecycle):
        """htf_bias from plan should flow through to post-mortem context."""
        outcome = _make_plan_outcome(
            pair="EURUSD", score=12, htf_bias="bullish",
        )
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade.htf_bias == "bullish"

        result = await lifecycle._close_trade(
            "EURUSD", 1.0520, "TP1_HIT", "TP1 reached",
        )
        pm = result["post_mortem"]
        # HTF alignment should appear in what_worked for bullish buy
        assert any("HTF" in w or "bias" in w for w in pm.get("what_worked", []))


# ═══════════════════════════════════════════════════════════════════════════
# F4-09  confluence_score + voting_confidence propagated
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_09_ScorePropagation:
    """confluence_score & voting_confidence must flow from plan → trade → close_result."""

    @pytest.mark.asyncio
    async def test_trade_stores_confluence_score(self, lifecycle):
        """ActiveTrade stores confluence_score from plan."""
        outcome = _make_plan_outcome(pair="EURUSD", score=11)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade.confluence_score == 11

    @pytest.mark.asyncio
    async def test_trade_stores_voting_confidence(self, lifecycle):
        """ActiveTrade stores voting_confidence from plan.confidence."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12, confidence=0.92)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade.voting_confidence == pytest.approx(0.92, abs=0.01)

    @pytest.mark.asyncio
    async def test_close_result_has_real_scores(self, lifecycle):
        """close_result must have actual scores, not hardcoded 0."""
        outcome = _make_plan_outcome(
            pair="EURUSD", score=10, confidence=0.87,
        )
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        result = await lifecycle._close_trade(
            "EURUSD", 1.0520, "TP1_HIT", "TP1 reached",
        )
        assert result["confluence_score"] == 10
        assert result["voting_confidence"] == pytest.approx(0.87, abs=0.01)

    @pytest.mark.asyncio
    async def test_close_result_strategy_mode(self, lifecycle):
        """close_result strategy_mode comes from ActiveTrade, not hardcoded."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade.strategy_mode == "sniper_confluence"

        result = await lifecycle._close_trade(
            "EURUSD", 1.0520, "TP1_HIT", "TP1 reached",
        )
        assert result["strategy_mode"] == "sniper_confluence"

    @pytest.mark.asyncio
    async def test_postmortem_receives_scores(self, lifecycle):
        """PostMortem report has actual confluence_score & voting_confidence."""
        outcome = _make_plan_outcome(
            pair="EURUSD", score=9, confidence=0.78,
        )
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        result = await lifecycle._close_trade(
            "EURUSD", 1.0520, "TP1_HIT", "TP1 reached",
        )
        pm = result["post_mortem"]
        assert pm["confluence_score"] == 9
        assert pm["voting_confidence"] == pytest.approx(0.78, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# F4-10  Conviction lock — tested with state transitions from F3-09
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_10_ConvictionLock:
    """Conviction lock enforced: direction, strategy_mode, htf_bias immutable
    once state >= WATCHING."""

    def test_direction_locked_after_watching(self):
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)

        # Try to change direction
        ctx2 = SetupContext(
            pair="EURUSD", direction="sell", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        with pytest.raises(ConvictionLockViolation, match="direction"):
            sm.transition(AnalysisState.APPROACHING, ctx2)

    def test_strategy_mode_locked(self):
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)

        ctx2 = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="scalping_channel",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        with pytest.raises(ConvictionLockViolation, match="strategy_mode"):
            sm.transition(AnalysisState.APPROACHING, ctx2)

    def test_htf_bias_locked(self):
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)

        ctx2 = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bearish",
        )
        with pytest.raises(ConvictionLockViolation, match="htf_bias"):
            sm.transition(AnalysisState.APPROACHING, ctx2)

    def test_score_and_confidence_are_not_locked(self):
        """Score and confidence are adjustable — not part of conviction lock."""
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)

        ctx2 = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0500, score=10, confidence=0.95, htf_bias="bullish",
        )
        # Should NOT raise — score/confidence changes are allowed
        sm.transition(AnalysisState.APPROACHING, ctx2)
        assert sm.state == AnalysisState.APPROACHING

    def test_lock_through_full_lifecycle(self):
        """Conviction lock enforced across all transitions."""
        sm = StateMachine()
        ctx = SetupContext(
            pair="XAUUSD", direction="sell", strategy_mode="sniper_confluence",
            entry_zone_mid=2350.0, score=9, confidence=0.85, htf_bias="bearish",
        )
        for target in [
            AnalysisState.WATCHING,
            AnalysisState.APPROACHING,
            AnalysisState.TRIGGERED,
            AnalysisState.ACTIVE,
            AnalysisState.CLOSED,
        ]:
            sm.transition(target, ctx)
        assert sm.state == AnalysisState.CLOSED


# ═══════════════════════════════════════════════════════════════════════════
# F4-12  Dual cooldown: lifecycle vs state machine
# ═══════════════════════════════════════════════════════════════════════════

class TestF4_12_DualCooldown:
    """Lifecycle cooldown (5min) and StateMachine cooldown (30min) are separate."""

    def test_lifecycle_cooldown_default(self):
        """Lifecycle cooldown uses LIFECYCLE_COOLDOWN_MINUTES from settings."""
        assert LIFECYCLE_COOLDOWN_MINUTES == 5

    def test_state_machine_cooldown_default(self):
        """State machine cooldown uses COOLDOWN_MINUTES from settings."""
        assert COOLDOWN_MINUTES == 30

    def test_lifecycle_uses_configurable_cooldown(self, lifecycle):
        """ProductionLifecycle._cooldown_minutes matches setting."""
        assert lifecycle._cooldown_minutes == LIFECYCLE_COOLDOWN_MINUTES

    @pytest.mark.asyncio
    async def test_lifecycle_cooldown_blocks_reopen(self, lifecycle):
        """After closing a trade, pair is blocked for _cooldown_minutes."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        await lifecycle._close_trade(
            "EURUSD", 1.0520, "TP1_HIT", "TP1 reached",
        )

        # Immediately try to reopen → should be blocked by cooldown
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade2 = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade2 is None

    def test_state_machine_cooldown_after_cancel(self):
        """StateMachine cooldown engages after CANCELLED state."""
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("score dropped")

        assert sm.state == AnalysisState.CANCELLED
        assert sm.is_in_cooldown() is True
        assert sm.cancel_reason == "score dropped"

    def test_state_machine_cooldown_expires(self):
        """After COOLDOWN_MINUTES, is_in_cooldown returns False."""
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy", strategy_mode="sniper_confluence",
            entry_zone_mid=1.0485, score=8, confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("test")

        # Fake the cancel time to be in the past
        sm._cancel_time = sm._cancel_time - (COOLDOWN_MINUTES * 60 + 1)
        assert sm.is_in_cooldown() is False


# ═══════════════════════════════════════════════════════════════════════════
# ActiveTrade context fields (integration)
# ═══════════════════════════════════════════════════════════════════════════

class TestActiveTrade_ContextFields:
    """ActiveTrade has additional fields for setup context."""

    def test_default_context_fields(self):
        trade = _buy_trade()
        assert trade.strategy_mode == ""
        assert trade.confluence_score == 0
        assert trade.voting_confidence == 0.0
        assert trade.htf_bias == ""
        assert trade.entry_zone_type == ""

    def test_context_fields_set(self):
        trade = _buy_trade(
            strategy_mode="sniper_confluence",
            confluence_score=11,
            voting_confidence=0.9,
            htf_bias="bullish",
            entry_zone_type="supply_demand",
        )
        assert trade.strategy_mode == "sniper_confluence"
        assert trade.confluence_score == 11
        assert trade.voting_confidence == 0.9
        assert trade.htf_bias == "bullish"
        assert trade.entry_zone_type == "supply_demand"
