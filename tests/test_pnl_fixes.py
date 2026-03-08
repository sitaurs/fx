"""
tests/test_pnl_fixes.py — Phase 4 Step 1: P&L/Balance bug fixes.

Tests:
  - Bug A: Trailing SL above BE should show real profit, not $0
  - Bug B: TP_HIT should use TP price, not overshot exit_price
  - Bug C: SL_HIT original should use actual price-based loss
  - Partial TP1 + BE_HIT total P&L breakdown
  - MANUAL_CLOSE uses actual price-based P&L
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from agent.production_lifecycle import ProductionLifecycle
from agent.trade_manager import ActiveTrade, TradeManager


def _make_lifecycle(balance=10000.0) -> ProductionLifecycle:
    """Create a fresh lifecycle with mocked repo."""
    repo = MagicMock()
    repo.init_db = AsyncMock()
    repo.get_setting_json = AsyncMock(return_value=None)
    repo.set_setting_json = AsyncMock()
    repo.save_trade = AsyncMock()
    lc = ProductionLifecycle(repo=repo, initial_balance=balance)
    lc._push_trade_closed = AsyncMock()
    lc._notify_trade_closed = AsyncMock()
    return lc


def _make_trade(
    pair="EURUSD",
    direction="buy",
    entry=1.08000,
    sl=1.07500,
    tp1=1.08500,
    tp2=1.09000,
    lot_size=0.10,
    risk_amount=50.0,
    sl_moved_to_be=False,
    trail_active=False,
    partial_closed=False,
    remaining_size=1.0,
    realized_pnl=0.0,
) -> ActiveTrade:
    trade = ActiveTrade(
        trade_id="T-test0001",
        pair=pair,
        direction=direction,
        entry_price=entry,
        stop_loss=sl if sl_moved_to_be else sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
        lot_size=lot_size,
        risk_amount=risk_amount,
        sl_moved_to_be=sl_moved_to_be,
        trail_active=trail_active,
        partial_closed=partial_closed,
        remaining_size=remaining_size,
        realized_pnl=realized_pnl,
        opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    # Set original_sl to the real original
    if sl_moved_to_be:
        trade.original_sl = 1.07500
    return trade


class TestPnLFixes(unittest.TestCase):
    """Phase 4 Step 1 P&L fixes."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    # --- Bug A: Trailing SL above BE should show real profit ---

    def test_trailing_sl_above_be_shows_profit(self):
        """Trailing SL hit at 1.08300 (above entry 1.08000) = profit, not $0."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            sl_moved_to_be=True,
            trail_active=True,
        )
        # SL trailed to 1.08300 (30 pips above entry)
        trade.stop_loss = 1.08300
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08300,  # SL hit here
            result="SL_HIT",
            reason="Trailing SL hit",
        ))

        # Should be reclassified to TRAIL_PROFIT
        self.assertEqual(result["result"], "TRAIL_PROFIT")
        # P&L should be positive (30 pips * pip_value * lot)
        self.assertGreater(result["pnl"], 0)
        # Balance should increase
        self.assertGreater(lc.balance, 10000.0)

    def test_trailing_sl_at_exact_be_is_zero(self):
        """Trailing SL hit exactly at entry = BE_HIT, $0."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            sl_moved_to_be=True,
            trail_active=True,
        )
        trade.stop_loss = 1.08000  # exactly at entry
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08000,
            result="SL_HIT",
            reason="SL hit at BE",
        ))

        self.assertEqual(result["result"], "BE_HIT")
        self.assertEqual(result["final_leg_pnl"], 0.0)
        self.assertEqual(lc.balance, 10000.0)

    # --- Bug A variant: BE-only (not trailed) still $0 ---

    def test_be_sl_hit_no_trail_is_zero(self):
        """SL moved to BE but not trailed further → $0 on SL hit."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            sl_moved_to_be=True,
            trail_active=False,
        )
        trade.stop_loss = 1.08002  # BE + tiny buffer
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08002,
            result="SL_HIT",
            reason="SL hit at breakeven",
        ))

        self.assertEqual(result["result"], "BE_HIT")
        self.assertEqual(result["final_leg_pnl"], 0.0)

    # --- Bug B: TP_HIT should use TP price ---

    def test_tp1_hit_uses_tp_price_not_overshot(self):
        """TP1 at 1.08500 but exit detected at 1.08600 (overshot).
        P&L should be based on 1.08500, not 1.08600."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade()
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08600,  # overshot TP1 by 10 pips
            result="TP1_HIT",
            reason="TP1 reached",
        ))

        # Expected: 50 pips (1.08500-1.08000) * $10/pip * 0.10 lot * 1.0 remaining = $50
        # NOT 60 pips from exit_price 1.08600
        expected_pnl_tp1 = 50 * 10.0 * 0.10 * 1.0  # $50.0
        self.assertAlmostEqual(result["final_leg_pnl"], expected_pnl_tp1, places=1)

    def test_tp2_hit_uses_tp2_price(self):
        """TP2 hit should use TP2 level for P&L."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            partial_closed=True,
            remaining_size=0.5,
            realized_pnl=25.0,  # from TP1 partial close
        )
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.09050,  # slight overshoot of TP2 1.09000
            result="TP2_HIT",
            reason="TP2 hit",
        ))

        # Expected final leg: 100 pips (1.09000-1.08000) * $10 * 0.10 lot * 0.5 remaining = $50
        expected_final = 100 * 10.0 * 0.10 * 0.5  # $50.0
        self.assertAlmostEqual(result["final_leg_pnl"], expected_final, places=1)
        # Total pnl = realized_partial + final_leg
        self.assertAlmostEqual(result["pnl"], 25.0 + expected_final, places=1)

    # --- Bug C: Original SL hit = actual price-based loss ---

    def test_original_sl_hit_actual_loss(self):
        """Original SL hit. Loss should be based on actual price movement."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade()
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.07500,  # SL level
            result="SL_HIT",
            reason="Stop loss hit",
        ))

        self.assertEqual(result["result"], "SL_HIT")
        # 50 pips loss * $10/pip * 0.10 lot = -$50.0
        expected_loss = -50 * 10.0 * 0.10 * 1.0  # -$50.0
        self.assertAlmostEqual(result["final_leg_pnl"], expected_loss, places=1)
        self.assertAlmostEqual(lc.balance, 10000.0 + expected_loss, places=1)

    # --- MANUAL_CLOSE uses actual P&L ---

    def test_manual_close_actual_pnl(self):
        """Manual close at a loss uses actual price-based P&L."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade()
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.07800,  # 20 pip loss
            result="MANUAL_CLOSE",
            reason="Manual close",
        ))

        expected_pnl = -20 * 10.0 * 0.10 * 1.0  # -$20.0
        self.assertAlmostEqual(result["final_leg_pnl"], expected_pnl, places=1)

    def test_manual_close_in_profit(self):
        """Manual close in profit."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade()
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08200,  # 20 pip profit
            result="MANUAL_CLOSE",
            reason="Manual close",
        ))

        expected_pnl = 20 * 10.0 * 0.10 * 1.0  # $20.0
        self.assertAlmostEqual(result["final_leg_pnl"], expected_pnl, places=1)

    # --- SELL direction tests ---

    def test_sell_tp_hit_correct_pnl(self):
        """Sell trade TP1 hit: entry 1.08000, TP1 1.07500 = 50 pips profit."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            direction="sell",
            entry=1.08000,
            sl=1.08500,
            tp1=1.07500,
            tp2=1.07000,
        )
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.07490,  # slight overshoot
            result="TP1_HIT",
            reason="TP1 hit",
        ))

        # 50 pips (1.08000-1.07500) * $10 * 0.10 lot = $50
        expected = 50 * 10.0 * 0.10
        self.assertAlmostEqual(result["final_leg_pnl"], expected, places=1)

    def test_sell_sl_hit_loss(self):
        """Sell trade SL hit: should be actual loss from price movement."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            direction="sell",
            entry=1.08000,
            sl=1.08500,
            tp1=1.07500,
            tp2=1.07000,
        )
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08500,  # SL hit
            result="SL_HIT",
            reason="SL hit",
        ))

        self.assertEqual(result["result"], "SL_HIT")
        # 50 pips loss * $10 * 0.10 = -$50
        expected = -50 * 10.0 * 0.10
        self.assertAlmostEqual(result["final_leg_pnl"], expected, places=1)

    # --- XAUUSD (Gold) pip value test ---

    def test_gold_tp_hit_pnl(self):
        """Gold TP1 hit: different pip value ($10/pip/lot, point=0.1)."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            pair="XAUUSD",
            direction="buy",
            entry=2800.0,
            sl=2790.0,
            tp1=2815.0,
            tp2=2830.0,
            lot_size=0.01,
            risk_amount=10.0,
        )
        mgr = TradeManager(trade)
        lc._active["XAUUSD"] = (trade, mgr)

        result = self._run(lc._close_trade(
            pair="XAUUSD",
            exit_price=2816.0,  # slight overshoot
            result="TP1_HIT",
            reason="TP1 hit",
        ))

        # TP1=2815 → 150 pips from 2800 (point=0.1) * $10/pip * 0.01 lot = $15
        expected = 150 * 10.0 * 0.01 * 1.0
        self.assertAlmostEqual(result["final_leg_pnl"], expected, places=1)

    # --- Partial close + BE = correct breakdown ---

    def test_partial_tp1_then_be_hit_breakdown(self):
        """TP1 partial close ($25 realized) then BE hit → total = $25 + $0."""
        lc = _make_lifecycle(balance=10000.0)
        trade = _make_trade(
            sl_moved_to_be=True,
            trail_active=False,
            partial_closed=True,
            remaining_size=0.5,
            realized_pnl=25.0,
        )
        trade.stop_loss = trade.entry_price  # at BE
        mgr = TradeManager(trade)
        lc._active["EURUSD"] = (trade, mgr)

        # Balance already had $25 from partial close
        lc.balance = 10025.0

        result = self._run(lc._close_trade(
            pair="EURUSD",
            exit_price=1.08000,  # at entry = BE
            result="SL_HIT",
            reason="SL hit",
        ))

        self.assertEqual(result["result"], "BE_HIT")
        self.assertEqual(result["final_leg_pnl"], 0.0)
        self.assertEqual(result["realized_partial_pnl"], 25.0)
        self.assertAlmostEqual(result["pnl"], 25.0, places=2)
        # Balance: 10025 + 0 = 10025
        self.assertAlmostEqual(lc.balance, 10025.0, places=2)


if __name__ == "__main__":
    unittest.main()
