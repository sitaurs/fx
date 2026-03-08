"""
tests/test_pending_queue.py â€” Phase 4 Step 2: Pending Queue System.

Tests:
  - PendingSetup creation and TTL
  - PendingManager add/remove/expire
  - Zone entry detection
  - compute_recommended_entry
  - Integration with ProductionLifecycle.on_scan_complete
  - check_pending_queue execution flow
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from agent.pending_manager import (
    PendingSetup,
    PendingManager,
    compute_recommended_entry,
)
from agent.production_lifecycle import ProductionLifecycle
from agent.trade_manager import ActiveTrade
from schemas.plan import TradingPlan, SetupCandidate
from schemas.market_data import Direction, StrategyMode
from agent.orchestrator import AnalysisOutcome
from agent.state_machine import AnalysisState


def _make_plan(
    pair="EURUSD",
    direction="buy",
    entry_low=1.08000,
    entry_high=1.08200,
    sl=1.07500,
    tp1=1.08700,
    tp2=1.09200,
    score=8,
    ttl=4.0,
) -> TradingPlan:
    setup = SetupCandidate(
        direction=Direction(direction),
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        entry_zone_low=entry_low,
        entry_zone_high=entry_high,
        trigger_condition="sweep + reclaim",
        stop_loss=sl,
        sl_reasoning="Below demand zone",
        take_profit_1=tp1,
        take_profit_2=tp2,
        tp_reasoning="Next supply zone",
        risk_reward_ratio=1.8,
        management="SL+ at 1R, trail at 1.5R",
        ttl_hours=ttl,
        invalidation="CHOCH bearish on H1",
        confluence_score=score,
        rationale="Strong demand zone with sweep and bullish structure",
    )
    return TradingPlan(
        pair=pair,
        analysis_time=datetime.now(timezone.utc).isoformat(),
        htf_bias="bullish",
        htf_bias_reasoning="Higher highs on H4",
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        primary_setup=setup,
        dxy_note="DXY bearish on H4",
        confidence=0.85,
        valid_until=(datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat(),
    )


def _make_lifecycle(balance=10000.0) -> ProductionLifecycle:
    repo = MagicMock()
    repo.init_db = AsyncMock()
    repo.get_setting_json = AsyncMock(return_value=None)
    repo.set_setting_json = AsyncMock()
    repo.save_trade = AsyncMock()
    lc = ProductionLifecycle(repo=repo, initial_balance=balance)
    lc._push_trade_closed = AsyncMock()
    lc._notify_trade_closed = AsyncMock()
    return lc


class TestRecommendedEntry(unittest.TestCase):
    """Test compute_recommended_entry."""

    def test_buy_deeper_entry(self):
        """Buy: recommended entry should be in lower 30% of zone."""
        rec = compute_recommended_entry("buy", 1.08000, 1.08200)
        # 1.08000 + 0.30 * (0.00200) = 1.08060
        self.assertAlmostEqual(rec, 1.08060, places=5)

    def test_sell_deeper_entry(self):
        """Sell: recommended entry should be in upper 70% of zone."""
        rec = compute_recommended_entry("sell", 1.08000, 1.08200)
        # 1.08000 + 0.70 * (0.00200) = 1.08140
        self.assertAlmostEqual(rec, 1.08140, places=5)

    def test_zero_range(self):
        """Zero-range zone: returns midpoint."""
        rec = compute_recommended_entry("buy", 1.08000, 1.08000)
        self.assertAlmostEqual(rec, 1.08000, places=5)


class TestPendingManager(unittest.TestCase):
    """Test PendingManager operations."""

    def _make_setup(self, pair="EURUSD", ttl=4.0, score=8) -> PendingSetup:
        plan = _make_plan(pair=pair, ttl=ttl, score=score)
        s = plan.primary_setup
        return PendingSetup(
            setup_id=f"PQ-test-{pair}",
            pair=pair,
            plan=plan,
            direction="buy",
            entry_zone_low=s.entry_zone_low,
            entry_zone_high=s.entry_zone_high,
            recommended_entry=1.08060,
            stop_loss=s.stop_loss,
            take_profit_1=s.take_profit_1,
            take_profit_2=s.take_profit_2,
            confluence_score=s.confluence_score,
            ttl_hours=ttl,
        )

    def test_add_setup(self):
        pm = PendingManager()
        setup = self._make_setup()
        ok = pm.add(setup)
        self.assertTrue(ok)
        self.assertEqual(pm.count, 1)
        self.assertEqual(pm.pending_pairs, ["EURUSD"])

    def test_no_duplicate_pair(self):
        pm = PendingManager()
        pm.add(self._make_setup("EURUSD"))
        ok = pm.add(self._make_setup("EURUSD"))
        self.assertFalse(ok)
        self.assertEqual(pm.count, 1)

    def test_max_pending(self):
        pm = PendingManager(max_pending=2)
        pm.add(self._make_setup("EURUSD"))
        pm.add(self._make_setup("GBPJPY"))
        ok = pm.add(self._make_setup("XAUUSD"))
        self.assertFalse(ok)
        self.assertEqual(pm.count, 2)

    def test_expire_ttl(self):
        pm = PendingManager()
        setup = self._make_setup(ttl=0.001)
        # FIX M-05: is_expired now uses market hours, so set created_at
        # far enough in the past that market hours >> TTL regardless of weekends
        setup.created_at = datetime.now(timezone.utc) - timedelta(hours=100)
        pm._queue.append(setup)

        expired = pm.cleanup_expired()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].status, "expired")

    def test_cancel_by_id(self):
        pm = PendingManager()
        pm.add(self._make_setup("EURUSD"))
        ok = pm.remove_by_id("PQ-test-EURUSD")
        self.assertTrue(ok)
        self.assertEqual(len(pm.get_pending()), 0)

    def test_zone_entry_check(self):
        pm = PendingManager()
        pm.add(self._make_setup("EURUSD"))
        # Price inside zone 1.08000-1.08200
        ready = pm.check_zone_entries({"EURUSD": 1.08100})
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].pair, "EURUSD")

    def test_zone_not_reached(self):
        pm = PendingManager()
        pm.add(self._make_setup("EURUSD"))
        # Price outside zone
        ready = pm.check_zone_entries({"EURUSD": 1.09000})
        self.assertEqual(len(ready), 0)

    def test_zone_check_with_buffer(self):
        pm = PendingManager()
        pm.add(self._make_setup("EURUSD"))
        # Price slightly outside zone but within 5 pip buffer
        ready = pm.check_zone_entries({"EURUSD": 1.08205}, entry_zone_buffer_pips=5.0)
        self.assertEqual(len(ready), 1)

    def test_to_dashboard_list(self):
        pm = PendingManager()
        pm.add(self._make_setup("EURUSD"))
        data = pm.to_dashboard_list()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["pair"], "EURUSD")
        self.assertIn("recommended_entry", data[0])
        self.assertIn("remaining_ttl_minutes", data[0])


class TestPendingIntegration(unittest.TestCase):
    """Test pending queue integration with ProductionLifecycle."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("agent.production_lifecycle.get_current_price_async")
    def test_on_scan_adds_to_pending_when_price_outside_zone(self, mock_price):
        """When price is outside entry zone, setup should go to pending queue."""
        mock_price.return_value = 1.09000  # far from zone 1.08000-1.08200

        lc = _make_lifecycle()
        plan = _make_plan()
        outcome = AnalysisOutcome(pair="EURUSD", state=AnalysisState.TRIGGERED, plan=plan)

        result = self._run(lc.on_scan_complete("EURUSD", outcome))
        self.assertIsNone(result)  # No trade opened
        self.assertEqual(lc.pending_count, 1)
        self.assertEqual(lc.pending_pairs, ["EURUSD"])

    @patch("agent.production_lifecycle.get_current_price_async")
    def test_on_scan_opens_trade_when_price_in_zone(self, mock_price):
        """When price is inside entry zone, trade opens directly."""
        mock_price.return_value = 1.08100  # inside zone 1.08000-1.08200

        lc = _make_lifecycle()
        plan = _make_plan()
        outcome = AnalysisOutcome(pair="EURUSD", state=AnalysisState.TRIGGERED, plan=plan)

        result = self._run(lc.on_scan_complete("EURUSD", outcome))
        self.assertIsNotNone(result)
        self.assertEqual(lc.active_count, 1)
        self.assertEqual(lc.pending_count, 0)

    @patch("agent.production_lifecycle.get_current_price_async")
    def test_check_pending_executes_when_price_enters_zone(self, mock_price):
        """Pending setup should execute when monitoring loop detects price in zone."""
        mock_price.return_value = 1.09000  # initially outside

        lc = _make_lifecycle()
        plan = _make_plan()
        outcome = AnalysisOutcome(pair="EURUSD", state=AnalysisState.TRIGGERED, plan=plan)
        self._run(lc.on_scan_complete("EURUSD", outcome))
        self.assertEqual(lc.pending_count, 1)

        # Now price enters zone
        mock_price.return_value = 1.08100
        prices = {"EURUSD": 1.08100}
        opened = self._run(lc.check_pending_queue(prices))

        self.assertEqual(len(opened), 1)
        self.assertEqual(lc.active_count, 1)

    @patch("agent.production_lifecycle.get_current_price_async")
    @patch("agent.production_lifecycle.get_current_price")
    def test_max_concurrent_blocks_pending_execution(self, mock_sync_price, mock_price):
        """Pending setup should not execute if max concurrent trades reached."""
        # Per-pair prices to avoid cross-pair drawdown distortion
        async def _price_for_pair(pair):
            return {"GBPJPY": 190.200, "EURUSD": 1.09000, "USDJPY": 150.0}.get(pair, 1.0)

        def _sync_price(pair):
            return {"GBPJPY": 190.200, "EURUSD": 1.09000, "USDJPY": 150.0}.get(pair, 1.0)

        mock_price.side_effect = _price_for_pair
        mock_sync_price.side_effect = _sync_price

        lc = _make_lifecycle()
        lc.max_concurrent_trades = 1

        # Open 1 trade first
        plan1 = _make_plan(pair="GBPJPY", entry_low=190.000, entry_high=190.500,
                          sl=189.500, tp1=191.000, tp2=191.500)
        outcome1 = AnalysisOutcome(pair="GBPJPY", state=AnalysisState.TRIGGERED, plan=plan1)
        self._run(lc.on_scan_complete("GBPJPY", outcome1))
        self.assertEqual(lc.active_count, 1)

        # Add EURUSD to pending — price outside zone AND max concurrent reached
        plan2 = _make_plan()
        outcome2 = AnalysisOutcome(pair="EURUSD", state=AnalysisState.TRIGGERED, plan=plan2)
        self._run(lc.on_scan_complete("EURUSD", outcome2))
        self.assertEqual(lc.pending_count, 1)

        # Now EURUSD price enters zone â€” but max concurrent = 1
        prices = {"EURUSD": 1.08100, "GBPJPY": 190.300}
        opened = self._run(lc.check_pending_queue(prices))
        self.assertEqual(len(opened), 0)
        self.assertEqual(lc.active_count, 1)  # Still 1


if __name__ == "__main__":
    unittest.main()

