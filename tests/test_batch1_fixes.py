"""
tests/test_batch1_fixes.py — Unit tests for Batch 1 critical bug fixes.

Covers:
  FIX F2-09:  CHoCH ATR-based threshold (tested in test_choch_dxy.py too)
  FIX F3-01/02: Local score verification override
  FIX F3-07:  Gemini retry with exponential backoff
  FIX F3-09:  State machine transitions in run_scan()
  FIX F4-04:  Partial TP1 close (50%, not full)
  FIX F4-11:  BE-SL reclassification
  FIX F4-02:  Daily reset weekend guard
  FIX F0-03:  DemoBackend production guard
"""

from __future__ import annotations

import asyncio
import os
import time
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# =========================================================================
# FIX F3-01/F3-02 — Local score verification
# =========================================================================

class TestLocalScoreVerification:
    """Orchestrator must override Gemini's hallucinated score with local scorer."""

    def test_extract_score_flags_bullish(self):
        """_extract_score_flags extracts booleans from tool analyses."""
        from agent.orchestrator import AnalysisOrchestrator

        orch = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        orch.pair = "EURUSD"

        # Mock a candidate
        candidate = MagicMock()
        candidate.direction = "buy"
        candidate.strategy_mode = "sniper_confluence"
        candidate.entry_zone_low = 1.0480
        candidate.entry_zone_high = 1.0490
        candidate.stop_loss = 1.0450

        # Mock analyses with realistic tool data
        analyses = {
            "H1": {
                "structure": {"trend": "bullish"},
                "supply_zones": [],
                "demand_zones": [{"mitigated": False, "zone_low": 1.0470}],
                "sweep_events": [{"type": "eql_sweep"}],
                "snr_levels": [{"price": 1.0485}],
                "pin_bars": [{"index": 148}],
                "engulfing_patterns": [],
                "candle_count": 150,
                "last_close": 1.0490,
                "ema50": {"current": 1.0470},
                "rsi14": {"current": 55},
                "atr": {"current": 0.0030},
            }
        }

        flags = orch._extract_score_flags(candidate, analyses)

        assert flags["htf_alignment"] is True   # bullish trend + buy
        assert flags["fresh_zone"] is True       # demand zone not mitigated
        assert flags["sweep_detected"] is True   # sweep event exists
        assert flags["ema_filter_ok"] is True    # price > ema50
        assert flags["rsi_filter_ok"] is True    # RSI < 70 for buy

    def test_extract_score_flags_bearish(self):
        from agent.orchestrator import AnalysisOrchestrator

        orch = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        orch.pair = "EURUSD"

        candidate = MagicMock()
        candidate.direction = "sell"
        candidate.strategy_mode = "sniper_confluence"
        candidate.entry_zone_low = 1.0510
        candidate.entry_zone_high = 1.0520
        candidate.stop_loss = 1.0550

        analyses = {
            "H1": {
                "structure": {"trend": "bearish"},
                "supply_zones": [{"mitigated": False}],
                "demand_zones": [],
                "sweep_events": [],
                "snr_levels": [],
                "pin_bars": [],
                "engulfing_patterns": [],
                "candle_count": 150,
                "last_close": 1.0500,
                "ema50": {"current": 1.0520},
                "rsi14": {"current": 45},
                "atr": {"current": 0.0030},
            }
        }

        flags = orch._extract_score_flags(candidate, analyses)

        assert flags["htf_alignment"] is True     # bearish + sell
        assert flags["fresh_zone"] is True         # supply zone not mitigated
        assert flags["sweep_detected"] is False    # no sweep
        assert flags["ema_filter_ok"] is True      # price < ema50 for sell
        assert flags["rsi_filter_ok"] is True      # RSI > 30 for sell

    def test_score_override_when_hallucinated(self):
        """scorer.score_setup_candidate produces deterministic result."""
        from tools.scorer import score_setup_candidate

        # All positive flags true, no penalties
        result = score_setup_candidate(
            htf_alignment=True,
            fresh_zone=True,
            sweep_detected=True,
            near_major_snr=True,
            pa_confirmed=True,
            ema_filter_ok=True,
            rsi_filter_ok=True,
        )
        assert result["score"] == 14  # max possible
        assert result["tradeable"] is True

        # All penalties
        result2 = score_setup_candidate(
            counter_htf_bias=True,
            zone_mitigated=True,
            sl_too_tight=True,
            sl_too_wide=True,
        )
        assert result2["score"] == 0  # all penalties, clamped to floor (FIX F0-05)
        assert result2["tradeable"] is False


# =========================================================================
# FIX F3-07 — Gemini retry with exponential backoff
# =========================================================================

class TestGeminiRetry:
    """Test retry logic in gemini_client."""

    @pytest.mark.asyncio
    async def test_async_retry_succeeds_on_third_attempt(self):
        from agent.gemini_client import _async_retry

        call_count = 0

        async def flaky_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"

        result = await _async_retry(flaky_call, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_retry_exhausts_all_retries(self):
        from agent.gemini_client import _async_retry

        async def always_fail():
            raise ConnectionError("Permanent failure")

        with pytest.raises(ConnectionError, match="Permanent"):
            await _async_retry(always_fail, max_retries=3, base_delay=0.01)

    def test_sync_retry_succeeds_on_second_attempt(self):
        from agent.gemini_client import _sync_retry

        call_count = 0

        def flaky_call():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Temp")
            return "ok"

        result = _sync_retry(flaky_call, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert call_count == 2

    def test_sync_retry_raises_after_exhaustion(self):
        from agent.gemini_client import _sync_retry

        def always_fail():
            raise ValueError("Nope")

        with pytest.raises(ValueError, match="Nope"):
            _sync_retry(always_fail, max_retries=2, base_delay=0.01)

    @pytest.mark.asyncio
    async def test_async_retry_succeeds_first_try(self):
        from agent.gemini_client import _async_retry

        async def ok_call():
            return 42

        result = await _async_retry(ok_call, max_retries=3, base_delay=0.01)
        assert result == 42


# =========================================================================
# FIX F3-09 — State transitions in orchestrator
# =========================================================================

class TestStateTransitions:
    """run_scan() must advance state machine based on score."""

    def test_transition_to_triggered_for_high_score(self):
        """Score >= MIN_SCORE_FOR_TRADE should advance to TRIGGERED."""
        from agent.orchestrator import AnalysisOrchestrator
        from agent.state_machine import AnalysisState

        orch = AnalysisOrchestrator(pair="EURUSD")
        assert orch.state == AnalysisState.SCANNING

        # Manually drive transitions like run_scan does
        orch.transition_to(
            AnalysisState.WATCHING,
            score=7,
            direction="buy",
            strategy_mode="sniper_confluence",
            htf_bias="bullish",
        )
        assert orch.state == AnalysisState.WATCHING

        orch.transition_to(
            AnalysisState.APPROACHING,
            score=7,
            direction="buy",
            strategy_mode="sniper_confluence",
            htf_bias="bullish",
        )
        assert orch.state == AnalysisState.APPROACHING

        orch.transition_to(
            AnalysisState.TRIGGERED,
            score=7,
            direction="buy",
            strategy_mode="sniper_confluence",
            htf_bias="bullish",
        )
        assert orch.state == AnalysisState.TRIGGERED

    def test_conviction_lock_prevents_direction_change(self):
        """After WATCHING, direction is locked."""
        from agent.orchestrator import AnalysisOrchestrator
        from agent.state_machine import AnalysisState, ConvictionLockViolation

        orch = AnalysisOrchestrator(pair="EURUSD")
        orch.transition_to(
            AnalysisState.WATCHING,
            score=6, direction="buy", strategy_mode="sniper_confluence",
            htf_bias="bullish",
        )

        # Try to flip direction
        with pytest.raises(ConvictionLockViolation):
            orch.transition_to(
                AnalysisState.APPROACHING,
                score=6, direction="sell", strategy_mode="sniper_confluence",
                htf_bias="bullish",
            )


# =========================================================================
# FIX F4-04 — Partial TP1 close (50%, not full)
# =========================================================================

class TestPartialTP1Close:
    """TP1 hit should close 50% and move SL to breakeven, NOT full close."""

    @pytest_asyncio.fixture
    async def lifecycle(self):
        from agent.production_lifecycle import ProductionLifecycle
        from database.repository import Repository

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        lc = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
            max_concurrent_trades=2,
        )
        await lc.init()
        lc.active_revalidation_enabled = False  # Disable for unit tests
        yield lc
        await repo.close()

    @pytest.mark.asyncio
    async def test_tp1_keeps_trade_open(self, lifecycle):
        """After TP1 hit, trade should still be in _active (not closed)."""
        from tests.test_production_lifecycle import _make_plan_outcome

        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Price hits TP1 area
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0525,  # near TP1
        ):
            closed = await lifecycle.check_active_trades()

        # Trade should NOT be fully closed — it's partial
        # The trade stays in _active with partial_closed=True
        assert lifecycle.active_count == 1, "Trade should remain open after TP1 partial"

        # Check the trade was marked partial
        trade_obj, _ = lifecycle._active["EURUSD"]
        assert trade_obj.partial_closed is True
        assert trade_obj.sl_moved_to_be is True

    @pytest.mark.asyncio
    async def test_tp1_partial_adds_profit(self, lifecycle):
        """Balance should increase by partial (50%) of the TP1 profit."""
        from tests.test_production_lifecycle import _make_plan_outcome

        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            await lifecycle.on_scan_complete("EURUSD", outcome)

        initial_balance = lifecycle.balance
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0525,
        ):
            await lifecycle.check_active_trades()

        assert lifecycle.balance > initial_balance, "Balance should increase on TP1 partial"

    @pytest.mark.asyncio
    async def test_tp1_moves_sl_to_breakeven(self, lifecycle):
        """After TP1 partial, SL should be moved to entry (breakeven)."""
        from tests.test_production_lifecycle import _make_plan_outcome

        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)

        entry_price = trade.entry_price

        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0525,
        ):
            await lifecycle.check_active_trades()

        trade_obj, _ = lifecycle._active["EURUSD"]
        assert trade_obj.stop_loss == entry_price, "SL should be at entry after TP1 partial"


# =========================================================================
# FIX F4-11 — BE-SL reclassification
# =========================================================================

class TestBESLReclassification:
    """SL hit when SL was at breakeven should be classified as BE_HIT with pnl≈0."""

    @pytest_asyncio.fixture
    async def lifecycle(self):
        from agent.production_lifecycle import ProductionLifecycle
        from database.repository import Repository

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        lc = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
            max_concurrent_trades=2,
        )
        await lc.init()
        lc.active_revalidation_enabled = False  # Disable for unit tests
        yield lc
        await repo.close()

    @pytest.mark.asyncio
    async def test_sl_hit_on_be_position_is_zero_loss(self, lifecycle):
        """When SL was moved to BE, hitting it → pnl=0, result=BE_HIT."""
        from tests.test_production_lifecycle import _make_plan_outcome

        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)
        assert trade is not None

        # Manually mark SL moved to BE
        trade_obj, _ = lifecycle._active["EURUSD"]
        trade_obj.sl_moved_to_be = True
        trade_obj.stop_loss = trade_obj.entry_price

        balance_before = lifecycle.balance

        # Close with SL_HIT
        result = await lifecycle._close_trade(
            pair="EURUSD",
            exit_price=trade_obj.entry_price,
            result="SL_HIT",
            reason="SL hit at BE level",
        )

        assert result["result"] == "BE_HIT", "Should be reclassified to BE_HIT"
        assert result["pnl"] == 0.0, "P/L should be zero for BE hit"
        assert lifecycle.balance == balance_before, "Balance unchanged on BE hit"

    @pytest.mark.asyncio
    async def test_sl_hit_original_still_full_loss(self, lifecycle):
        """When SL was NOT moved to BE, SL_HIT = full loss."""
        from tests.test_production_lifecycle import _make_plan_outcome

        outcome = _make_plan_outcome(pair="EURUSD", score=12)
        with patch(
            "agent.production_lifecycle.get_current_price",
            return_value=1.0485,
        ):
            trade = await lifecycle.on_scan_complete("EURUSD", outcome)

        balance_before = lifecycle.balance

        result = await lifecycle._close_trade(
            pair="EURUSD",
            exit_price=1.0440,
            result="SL_HIT",
            reason="Original SL hit",
        )

        assert result["result"] == "SL_HIT"
        assert result["pnl"] < 0
        assert lifecycle.balance < balance_before


# =========================================================================
# FIX F4-02 — Daily reset weekend guard
# =========================================================================

class TestDailyResetWeekend:
    """reset_daily() should skip on weekends (market closed)."""

    @pytest_asyncio.fixture
    async def lifecycle(self):
        from agent.production_lifecycle import ProductionLifecycle
        from database.repository import Repository

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        lc = ProductionLifecycle(
            repo=repo, mode="demo", initial_balance=10_000.0,
        )
        await lc.init()
        lc.active_revalidation_enabled = False  # Disable for unit tests
        yield lc
        await repo.close()

    def test_reset_skipped_on_saturday(self, lifecycle):
        """Saturday → reset_daily does nothing."""
        lifecycle._closed_today = [{"test": True}]
        lifecycle.daily_start_balance = 9_800
        lifecycle.balance = 10_200

        # Mock Saturday (weekday=5)
        from datetime import datetime, timezone
        sat = datetime(2025, 1, 4, 12, 0, tzinfo=timezone.utc)  # Jan 4 2025 = Saturday
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_dt.now.return_value = sat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lifecycle.reset_daily()

        # Should NOT have been reset
        assert lifecycle._closed_today == [{"test": True}], "Should not clear on Saturday"
        assert lifecycle.daily_start_balance == 9_800, "Should not update on Saturday"

    def test_reset_skipped_on_sunday(self, lifecycle):
        """Sunday → reset_daily does nothing."""
        lifecycle._closed_today = [{"test": True}]
        lifecycle.daily_start_balance = 9_800
        lifecycle.balance = 10_200

        sun = datetime(2025, 1, 5, 12, 0, tzinfo=timezone.utc)  # Jan 5 2025 = Sunday
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_dt.now.return_value = sun
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lifecycle.reset_daily()

        assert lifecycle._closed_today == [{"test": True}]
        assert lifecycle.daily_start_balance == 9_800

    def test_reset_works_on_monday(self, lifecycle):
        """Monday → reset proceeds normally."""
        lifecycle._closed_today = [{"test": True}]
        lifecycle.daily_start_balance = 9_800
        lifecycle.balance = 10_200

        mon = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)  # Jan 6 2025 = Monday
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_dt.now.return_value = mon
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lifecycle.reset_daily()

        assert lifecycle._closed_today == []
        assert lifecycle.daily_start_balance == 10_200


# =========================================================================
# FIX F0-03 — DemoBackend production guard
# =========================================================================

class TestDemoBackendGuard:
    """DemoBackend must NOT be used when TRADING_MODE=real."""

    def test_demo_mode_allows_fallback(self):
        """TRADING_MODE=demo → DemoBackend is acceptable."""
        from data.fetcher import _init_default_backend, DemoBackend

        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "",
            "OANDA_ACCOUNT_ID": "",
            "FINNHUB_API_KEY": "",
            "TRADING_MODE": "demo",
        }
        with patch.dict(os.environ, env, clear=False):
            backend = _init_default_backend()
        assert isinstance(backend, DemoBackend)

    def test_real_mode_blocks_fallback(self):
        """TRADING_MODE=real + no backends → RuntimeError."""
        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "",
            "OANDA_ACCOUNT_ID": "",
            "FINNHUB_API_KEY": "",
            "TRADING_MODE": "real",
        }
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="OANDA-only mode active|Cannot use DemoBackend"):
                from data.fetcher import _init_default_backend
                _init_default_backend()

    @pytest.mark.skip(reason="FinnhubBackend removed in OANDA-only refactor")
    def test_real_mode_with_finnhub_ok(self):
        """TRADING_MODE=real + FINNHUB_API_KEY → FinnhubBackend (no error)."""
        from data.fetcher import _init_default_backend, FinnhubBackend

        env = {
            "MT5_OHLCV_API_URL": "",
            "OANDA_API_KEY": "",
            "OANDA_ACCOUNT_ID": "",
            "FINNHUB_API_KEY": "test_key_123",
            "TRADING_MODE": "real",
        }
        with patch.dict(os.environ, env, clear=False):
            backend = _init_default_backend()
        assert isinstance(backend, FinnhubBackend)
