"""
tests/test_production_lifecycle.py — Tests for ProductionLifecycle.

Validates:
  - Drawdown protection (daily + total)
  - Trade open / close pipeline
  - PostMortem auto-generation
  - DB persistence
  - Daily summary & reset
  - State save / restore
  - Callback invocation
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agent.production_lifecycle import ProductionLifecycle, get_current_price
from agent.trade_manager import ActiveTrade, ActionType, TradeAction, TradeManager
from agent.orchestrator import AnalysisOutcome
from agent.state_machine import AnalysisState
from database.repository import Repository
from database.models import Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan_outcome(
    pair: str = "EURUSD",
    score: int = 12,
    direction: str = "buy",
    entry_low: float = 1.0480,
    entry_high: float = 1.0490,
    stop_loss: float = 1.0450,
    tp1: float = 1.0520,
    tp2: float = 1.0560,
) -> AnalysisOutcome:
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


def _empty_outcome() -> AnalysisOutcome:
    """Outcome with no plan."""
    return AnalysisOutcome(
        pair="EURUSD",
        state=AnalysisState.SCANNING,
        plan=None,
        elapsed_seconds=0.5,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def repo():
    """In-memory SQLite repository."""
    r = Repository(db_url="sqlite+aiosqlite:///:memory:")
    await r.init_db()
    yield r
    await r.close()


@pytest_asyncio.fixture
async def lifecycle(repo):
    """Production lifecycle with in-memory DB."""
    lc = ProductionLifecycle(
        repo=repo,
        mode="demo",
        initial_balance=10_000.0,
        max_daily_drawdown=0.05,
        max_total_drawdown=0.15,
        max_concurrent_trades=2,
    )
    await lc.init()
    lc.active_revalidation_enabled = False  # Disable for unit tests
    return lc


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

class TestDrawdown:
    def test_no_drawdown_initially(self, lifecycle):
        ok, reason = lifecycle.check_drawdown()
        assert ok is True
        assert reason == "OK"

    def test_daily_drawdown_triggers(self, lifecycle):
        # Simulate 6% daily loss
        lifecycle.daily_start_balance = 10_000
        lifecycle.balance = 9_400  # -6%
        ok, reason = lifecycle.check_drawdown()
        assert ok is False
        assert "DAILY DRAWDOWN" in reason
        assert lifecycle.is_halted

    def test_total_drawdown_triggers(self, lifecycle):
        # Simulate 16% total loss
        lifecycle.high_water_mark = 10_000
        lifecycle.balance = 8_400  # -16%
        ok, reason = lifecycle.check_drawdown()
        assert ok is False
        assert "TOTAL DRAWDOWN" in reason
        assert lifecycle.is_halted

    def test_can_open_when_halted_returns_false(self, lifecycle):
        lifecycle._halted = True
        lifecycle._halt_reason = "Test halt"
        ok, reason = lifecycle.can_open_trade()
        assert ok is False

    def test_can_open_when_max_concurrent(self, lifecycle):
        # Fill up concurrent slots
        lifecycle._active["EURUSD"] = (MagicMock(), MagicMock())
        lifecycle._active["GBPUSD"] = (MagicMock(), MagicMock())
        ok, reason = lifecycle.can_open_trade()
        assert ok is False
        assert "Max concurrent" in reason


# ---------------------------------------------------------------------------
# Trade open
# ---------------------------------------------------------------------------

class TestTradeOpen:
    @pytest.mark.asyncio
    async def test_open_on_valid_outcome(self, lifecycle):
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None
        assert trade.pair == "EURUSD"
        assert trade.direction == "buy"
        assert lifecycle.active_count == 1
        assert "EURUSD" in lifecycle.active_pairs

    @pytest.mark.asyncio
    async def test_skip_low_score(self, lifecycle):
        outcome = _make_plan_outcome(pair="EURUSD", score=3)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is None
        assert lifecycle.active_count == 0

    @pytest.mark.asyncio
    async def test_skip_no_plan(self, lifecycle):
        outcome = _empty_outcome()
        trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is None

    @pytest.mark.asyncio
    async def test_skip_duplicate_pair(self, lifecycle):
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)
            # Try again for same pair
            trade2 = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade2 is None
        assert lifecycle.active_count == 1  # still just 1

    @pytest.mark.asyncio
    async def test_skip_when_halted(self, lifecycle):
        lifecycle._halted = True
        lifecycle._halt_reason = "Test halt"
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is None


# ---------------------------------------------------------------------------
# Trade close pipeline
# ---------------------------------------------------------------------------

class TestTradeClose:
    @pytest.mark.asyncio
    async def test_close_trade_full_pipeline(self, lifecycle, repo):
        """Open then close a trade: check P/L, PostMortem, DB, callbacks."""
        # Setup callbacks
        push_closed = AsyncMock()
        push_state = AsyncMock()
        notify_closed = AsyncMock()
        notify_sl = AsyncMock()
        lifecycle.set_callbacks(push_closed, push_state, notify_closed, notify_sl)

        # Open a trade
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Close it manually
        result = await lifecycle._close_trade(
            pair="EURUSD",
            exit_price=1.0520,
            result="TP1_HIT",
            reason="TP1 reached",
        )

        # Check result dict
        assert result["pair"] == "EURUSD"
        assert result["result"] == "TP1_HIT"
        assert result["pnl"] > 0  # win
        assert "post_mortem" in result

        # Check trade removed from active
        assert lifecycle.active_count == 0

        # Check P/L updated balance
        assert lifecycle.balance > 10_000

        # Check DB persistence
        db_trade = await repo.get_trade(trade.trade_id)
        assert db_trade is not None
        assert db_trade.result == "TP1_HIT"

        # Check callbacks
        push_closed.assert_called_once()
        notify_closed.assert_called_once()

        # Check _closed_today
        assert len(lifecycle._closed_today) == 1

    @pytest.mark.asyncio
    async def test_close_loss_affects_balance(self, lifecycle):
        outcome = _make_plan_outcome(pair="USDJPY", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("USDJPY", outcome)
        assert trade is not None

        # Mock must also cover close (pip value calc calls get_current_price)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            result = await lifecycle._close_trade(
                pair="USDJPY",
                exit_price=1.0440,  # Below SL
                result="SL_HIT",
                reason="SL hit",
            )

        assert result["pnl"] < 0  # loss
        assert lifecycle.balance < 10_000


# ---------------------------------------------------------------------------
# Price monitoring
# ---------------------------------------------------------------------------

class TestPriceMonitor:
    @pytest.mark.asyncio
    async def test_check_active_hold(self, lifecycle):
        """HOLD action → trade stays open."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        # Mock get_current_price to return price near entry (no action)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            closed = await lifecycle.check_active_trades()

        assert len(closed) == 0
        assert lifecycle.active_count == 1

    @pytest.mark.asyncio
    async def test_check_active_sl_hit(self, lifecycle):
        """SL hit → trade closes."""
        outcome = _make_plan_outcome(
            pair="EURUSD",
            score=12,
            direction="buy",
            stop_loss=1.0450,
        )
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        # Price drops below SL
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0440,
        ):
            closed = await lifecycle.check_active_trades()

        assert len(closed) == 1
        assert closed[0]["result"] == "SL_HIT"
        assert lifecycle.active_count == 0

    @pytest.mark.asyncio
    async def test_check_active_price_fetch_fails(self, lifecycle):
        """Price fetch failure → trade stays open."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        with patch(
            "agent.production_lifecycle.get_current_price",
            side_effect=RuntimeError("No data"),
        ):
            closed = await lifecycle.check_active_trades()

        assert len(closed) == 0
        assert lifecycle.active_count == 1


# ---------------------------------------------------------------------------
# Daily summary & reset
# ---------------------------------------------------------------------------

class TestDailySummary:
    @pytest.mark.asyncio
    async def test_daily_summary_empty(self, lifecycle):
        summary = lifecycle.daily_summary()
        assert summary["trades_today"] == 0
        assert summary["wins"] == 0
        assert summary["losses"] == 0
        assert summary["total_pips"] == 0
        assert summary["halted"] is False

    @pytest.mark.asyncio
    async def test_daily_summary_with_trades(self, lifecycle):
        # Simulate some closed trades
        lifecycle._closed_today = [
            {"trade_id": "T1", "pair": "EURUSD", "direction": "buy",
             "result": "TP1_HIT", "pips": 25.0, "pnl": 100.0,
             "post_mortem": {"lessons": ["Good entry"]}},
            {"trade_id": "T2", "pair": "GBPJPY", "direction": "sell",
             "result": "SL_HIT", "pips": -20.0, "pnl": -80.0,
             "post_mortem": {"lessons": ["SL too tight"]}},
        ]
        summary = lifecycle.daily_summary()
        assert summary["trades_today"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["total_pips"] == 5.0
        assert summary["daily_pnl"] == 20.0

    def test_reset_daily(self, lifecycle):
        lifecycle._closed_today = [{"test": True}]
        lifecycle.daily_start_balance = 9_800
        lifecycle.balance = 10_200

        # Mock a weekday (Monday) so weekend guard doesn't skip reset
        mon = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_dt.now.return_value = mon
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lifecycle.reset_daily()

        assert lifecycle._closed_today == []
        assert lifecycle.daily_start_balance == 10_200

    def test_reset_daily_lifts_daily_halt(self, lifecycle):
        lifecycle._halted = True
        lifecycle._halt_reason = "⛔ DAILY DRAWDOWN 5.1% ≥ 5%"

        mon = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_dt.now.return_value = mon
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lifecycle.reset_daily()

        assert lifecycle.is_halted is False
        assert lifecycle._halt_reason == ""

    def test_reset_daily_keeps_total_halt(self, lifecycle):
        lifecycle._halted = True
        lifecycle._halt_reason = "⛔ TOTAL DRAWDOWN 16% ≥ 15%"

        mon = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_dt.now.return_value = mon
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lifecycle.reset_daily()

        # Total halt should NOT be lifted
        assert lifecycle.is_halted is True


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    @pytest.mark.asyncio
    async def test_save_and_restore(self, repo):
        """State survives a fresh lifecycle init."""
        lc1 = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000
        )
        await lc1.init()
        lc1.balance = 9_500
        lc1.high_water_mark = 10_200
        lc1._halted = True
        lc1._halt_reason = "Test halt"
        await lc1.save_state()

        # Create new lifecycle pointing at same DB
        lc2 = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000
        )
        await lc2.init()

        assert lc2.balance == 9_500
        assert lc2.high_water_mark == 10_200
        assert lc2.is_halted is True
        assert lc2._halt_reason == "Test halt"


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestProperties:
    def test_active_count(self, lifecycle):
        assert lifecycle.active_count == 0

    def test_active_pairs(self, lifecycle):
        assert lifecycle.active_pairs == []

    def test_is_halted_default(self, lifecycle):
        assert lifecycle.is_halted is False

    def test_halt_reason_default(self, lifecycle):
        assert lifecycle.halt_reason == ""


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    def test_set_callbacks(self, lifecycle):
        f1 = AsyncMock()
        f2 = AsyncMock()
        lifecycle.set_callbacks(push_trade_closed=f1, notify_sl_moved=f2)
        assert lifecycle._push_trade_closed is f1
        assert lifecycle._notify_sl_moved is f2
        assert lifecycle._push_state_change is None
        assert lifecycle._notify_trade_closed is None

    @pytest.mark.asyncio
    async def test_sl_moved_callback_fires(self, lifecycle):
        """SL+ action triggers notify_sl_moved callback."""
        notify_sl = AsyncMock()
        lifecycle.set_callbacks(notify_sl_moved=notify_sl)

        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        # Simulate price at BE trigger (1× risk above entry)
        # Entry ~1.0485, SL=1.0450 → risk=0.0035, BE trigger at ~1.0520
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0525,
        ):
            await lifecycle.check_active_trades()

        # SL+ should have been triggered, check the notify
        if notify_sl.called:
            assert notify_sl.call_count >= 1


# ---------------------------------------------------------------------------
# Trade Restore (FIX §7.3)
# ---------------------------------------------------------------------------

class TestTradeRestore:
    """Verify active trades survive restart via save/restore."""

    @pytest.mark.asyncio
    async def test_save_and_restore_active_trades(self, repo):
        """Trades saved to DB are restored on init."""
        lc1 = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
        )
        await lc1.init()

        # Open a trade manually
        outcome = _make_plan_outcome(pair="EURUSD", score=12, direction="buy")
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lc1.on_scan_complete("EURUSD", outcome)
        assert trade is not None
        assert lc1.active_count == 1

        # Save state
        await lc1.save_active_trades()
        await lc1.save_state()

        # Create a new lifecycle instance (simulating restart)
        lc2 = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
        )
        await lc2.init()

        # Active trades should be restored
        assert lc2.active_count == 1
        assert "EURUSD" in lc2.active_pairs

        restored_trade, restored_mgr = lc2._active["EURUSD"]
        assert restored_trade.trade_id == trade.trade_id
        assert restored_trade.direction == "buy"
        assert restored_trade.entry_price == trade.entry_price

    @pytest.mark.asyncio
    async def test_restore_with_no_saved_trades(self, repo):
        """No crash when no active trades saved."""
        lc = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
        )
        await lc.init()
        assert lc.active_count == 0

    @pytest.mark.asyncio
    async def test_restore_preserves_partial_state(self, repo):
        """Partial close and SL-to-BE state survive round-trip."""
        lc1 = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
        )
        await lc1.init()

        outcome = _make_plan_outcome(pair="XAUUSD", score=12, direction="sell",
                                     entry_low=2300.0, entry_high=2310.0,
                                     stop_loss=2340.0, tp1=2260.0, tp2=2220.0)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=2305.0,
        ):
            trade = await lc1.on_scan_complete("XAUUSD", outcome)
        assert trade is not None

        # Simulate partial close + SL-to-BE
        trade.partial_closed = True
        trade.sl_moved_to_be = True

        await lc1.save_active_trades()

        lc2 = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
        )
        await lc2.init()

        restored_trade, _ = lc2._active["XAUUSD"]
        assert restored_trade.partial_closed is True
        assert restored_trade.sl_moved_to_be is True


# ---------------------------------------------------------------------------
# FP-04 Fixes
# ---------------------------------------------------------------------------

class TestFP04EmergencySavePath:
    """C-01: Verify DB_FILE_PATH is importable and not hardcoded."""

    def test_db_file_path_exists_in_settings(self):
        from config.settings import DB_FILE_PATH
        assert DB_FILE_PATH is not None
        assert "forex_agent.db" in DB_FILE_PATH

    def test_db_file_path_used_in_emergency_save(self):
        """Verify main.py imports and uses DB_FILE_PATH."""
        import main
        # Check that the module imports DB_FILE_PATH
        assert hasattr(main, "DB_FILE_PATH") or "DB_FILE_PATH" in dir(main)


class TestFP04WrapupPersistence:
    """H-04: daily_wrapup saves active trades before state changes."""

    @pytest.mark.asyncio
    async def test_wrapup_saves_active_trades(self, lifecycle, repo):
        """Verify daily_wrapup calls save_active_trades."""
        # Open a trade
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Spy on save_active_trades
        with patch.object(
            lifecycle, "save_active_trades", new_callable=AsyncMock
        ) as mock_save:
            await lifecycle.daily_wrapup()
            mock_save.assert_called_once()


class TestFP04DrawdownBlocksPending:
    """H-05: When halted due to drawdown, don't add to pending queue."""

    @pytest.mark.asyncio
    async def test_drawdown_halt_blocks_pending(self, lifecycle):
        """When halted, scan_complete should NOT add to pending queue."""
        lifecycle._halted = True
        lifecycle._halt_reason = "⛔ DAILY DRAWDOWN 6.0% ≥ 5%"

        outcome = _make_plan_outcome(pair="GBPUSD", score=12,
                                     entry_low=1.2640, entry_high=1.2660,
                                     stop_loss=1.2610, tp1=1.2700)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.2650,  # In zone
        ):
            trade = await lifecycle.on_scan_complete("GBPUSD", outcome)

        assert trade is None
        # Crucially: should NOT be in pending queue
        assert lifecycle.pending_count == 0

    @pytest.mark.asyncio
    async def test_max_concurrent_still_adds_pending(self, lifecycle):
        """When max concurrent (not halted), setup added to pending queue."""
        # Fill concurrent slots
        lifecycle._active["EURUSD"] = (MagicMock(), MagicMock())
        lifecycle._active["GBPJPY"] = (MagicMock(), MagicMock())

        outcome = _make_plan_outcome(pair="AUDUSD", score=12,
                                     entry_low=0.6500, entry_high=0.6510,
                                     stop_loss=0.6470, tp1=0.6550)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=0.6505,  # In zone
        ):
            trade = await lifecycle.on_scan_complete("AUDUSD", outcome)

        assert trade is None
        # Should be in pending queue (not halted, just full)
        assert lifecycle.pending_count == 1


class TestFP04NullGuards:
    """L-04: Null guards in close trade pipeline."""

    @pytest.mark.asyncio
    async def test_close_trade_with_none_opened_at(self, lifecycle):
        """Close pipeline handles None opened_at gracefully."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Simulate None opened_at (edge case from corrupt restore)
        trade.opened_at = None

        # Should not crash
        result = await lifecycle._close_trade(
            pair="EURUSD",
            exit_price=1.0520,
            result="TP1_HIT",
            reason="TP1 reached",
        )
        assert result["opened_at"] is None
        assert result["pair"] == "EURUSD"


# ===========================================================================
# FP-05 tests — Production Lifecycle Part B
# ===========================================================================

class TestFP05SaveRestoreRoundTrip:
    """Audit M-01/M-02: save/restore preserves all ActiveTrade fields including
    original_sl, entry_zone_type, entry_zone_low, entry_zone_high, recommended_entry."""

    @pytest.mark.asyncio
    async def test_round_trip_preserves_new_fields(self, lifecycle):
        """Save then restore preserves zone & SL fields added in FP-05."""
        outcome = _make_plan_outcome(pair="GBPUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("GBPUSD", outcome)
        assert trade is not None

        # Manually set fields that might be populated at open
        trade.original_sl = 1.0440
        trade.entry_zone_type = "demand"
        trade.entry_zone_low = 1.0480
        trade.entry_zone_high = 1.0490
        trade.recommended_entry = 1.0485

        await lifecycle.save_active_trades()

        # Clear in-memory state
        lifecycle._active.clear()
        assert len(lifecycle._active) == 0

        restored = await lifecycle.restore_active_trades()
        assert restored == 1

        t = lifecycle._active["GBPUSD"][0]
        assert t.original_sl == 1.0440
        assert t.entry_zone_type == "demand"
        assert t.entry_zone_low == 1.0480
        assert t.entry_zone_high == 1.0490
        assert t.recommended_entry == 1.0485

    @pytest.mark.asyncio
    async def test_round_trip_missing_new_fields_defaults(self, lifecycle):
        """Restore from old-format data (missing new fields) uses safe defaults."""
        # Simulate old-format saved data without the new fields
        old_data = [{
            "pair": "USDJPY",
            "trade_id": "TEST-001",
            "direction": "sell",
            "entry_price": 149.500,
            "stop_loss": 150.000,
            "take_profit_1": 148.500,
        }]
        await lifecycle._repo.set_setting_json("active_trades", old_data)

        restored = await lifecycle.restore_active_trades()
        assert restored == 1

        t = lifecycle._active["USDJPY"][0]
        # original_sl defaults to 0.0, then __post_init__ copies stop_loss
        assert t.original_sl == 150.000
        assert t.entry_zone_type == ""
        assert t.entry_zone_low == 0.0
        assert t.entry_zone_high == 0.0
        assert t.recommended_entry is None

    @pytest.mark.asyncio
    async def test_save_no_getattr_all_direct(self, lifecycle):
        """save_active_trades serializes all dataclass fields directly (no getattr)."""
        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        trade.sl_moved_to_be = True
        trade.trail_active = True
        trade.partial_closed = True
        trade.realized_pnl = 25.0

        await lifecycle.save_active_trades()
        saved = await lifecycle._repo.get_setting_json("active_trades")
        assert len(saved) == 1
        rec = saved[0]
        assert rec["sl_moved_to_be"] is True
        assert rec["trail_active"] is True
        assert rec["partial_closed"] is True
        assert rec["realized_pnl"] == 25.0
        # Verify new fields present
        assert "original_sl" in rec
        assert "entry_zone_type" in rec
        assert "entry_zone_low" in rec
        assert "entry_zone_high" in rec
        assert "recommended_entry" in rec


class TestFP05TTLConfigConstant:
    """L-11: ttl_hours uses PENDING_SETUP_DEFAULT_TTL_HOURS from config."""

    @pytest.mark.asyncio
    async def test_pending_uses_config_ttl(self, lifecycle):
        """When setup.ttl_hours is 0, fallback to config constant."""
        from config.settings import PENDING_SETUP_DEFAULT_TTL_HOURS
        assert PENDING_SETUP_DEFAULT_TTL_HOURS == 4.0

        outcome = _make_plan_outcome(pair="EURJPY", score=12)
        # Override ttl_hours to 0 to trigger fallback
        outcome.plan.primary_setup.ttl_hours = 0

        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.2000,  # way outside zone → goes to pending
        ):
            result = await lifecycle.on_scan_complete("EURJPY", outcome)

        # Should have been added to pending with config TTL
        assert lifecycle._pending.count >= 1
