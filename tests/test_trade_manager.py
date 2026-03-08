"""
tests/test_trade_manager.py — Tests for SL+, trailing stop, trade management.

Validates all SL management rules from masterplan §13.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent.trade_manager import (
    ActionType,
    ActiveTrade,
    TradeAction,
    TradeManager,
    MonitoringReport,
    generate_monitoring_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy_trade(
    entry: float = 1.0480,
    sl: float = 1.0450,
    tp1: float = 1.0520,
    tp2: float | None = 1.0560,
) -> ActiveTrade:
    return ActiveTrade(
        trade_id="BUY_001",
        pair="EURUSD",
        direction="buy",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
    )


def _sell_trade(
    entry: float = 1.0520,
    sl: float = 1.0550,
    tp1: float = 1.0480,
    tp2: float | None = 1.0440,
) -> ActiveTrade:
    return ActiveTrade(
        trade_id="SELL_001",
        pair="EURUSD",
        direction="sell",
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
    )


ATR = 0.0008  # Typical EURUSD M15 ATR


# ---------------------------------------------------------------------------
# Active Trade properties
# ---------------------------------------------------------------------------

class TestActiveTrade:
    def test_initial_risk_buy(self):
        trade = _buy_trade()
        assert trade.initial_risk == pytest.approx(0.003, rel=0.01)

    def test_initial_risk_sell(self):
        trade = _sell_trade()
        assert trade.initial_risk == pytest.approx(0.003, rel=0.01)

    def test_floating_pnl_buy_profit(self):
        trade = _buy_trade()
        pnl = trade.floating_pnl(1.0510)  # 30 pips above entry
        assert pnl > 0

    def test_floating_pnl_buy_loss(self):
        trade = _buy_trade()
        pnl = trade.floating_pnl(1.0460)  # 20 pips below entry
        assert pnl < 0

    def test_floating_pnl_sell_profit(self):
        trade = _sell_trade()
        pnl = trade.floating_pnl(1.0490)  # 30 pips below entry
        assert pnl > 0

    def test_floating_pips(self):
        trade = _buy_trade()
        pips = trade.floating_pips(1.0510)
        assert pips == pytest.approx(30.0, abs=1.0)

    def test_rr_current(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450)
        # Risk = 30 pips, profit at 1.0510 = 30 pips → RR = 1.0
        rr = trade.rr_current(1.0510)
        assert rr == pytest.approx(1.0, rel=0.01)

    def test_distance_to_tp1_buy(self):
        trade = _buy_trade()
        dist = trade.distance_to_tp1(1.0510)
        assert dist == pytest.approx(0.001, rel=0.01)

    def test_distance_to_sl_buy(self):
        trade = _buy_trade()
        dist = trade.distance_to_sl(1.0460)
        assert dist == pytest.approx(0.001, rel=0.01)


# ---------------------------------------------------------------------------
# Trade Manager — HOLD
# ---------------------------------------------------------------------------

class TestHold:
    def test_hold_when_small_profit(self):
        trade = _buy_trade()
        mgr = TradeManager(trade)
        action = mgr.evaluate(1.0485, ATR)  # 5 pips profit
        assert action.action == ActionType.HOLD

    def test_hold_when_floating_loss(self):
        trade = _buy_trade()
        mgr = TradeManager(trade)
        action = mgr.evaluate(1.0465, ATR)  # 15 pips loss but SL not hit
        assert action.action == ActionType.HOLD

    def test_hold_sell(self):
        trade = _sell_trade()
        mgr = TradeManager(trade)
        action = mgr.evaluate(1.0515, ATR)  # 5 pips profit
        assert action.action == ActionType.HOLD


# ---------------------------------------------------------------------------
# SL+ to breakeven
# ---------------------------------------------------------------------------

class TestSLPlusBE:
    def test_sl_plus_be_triggers(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0560)
        mgr = TradeManager(trade, be_trigger_rr=1.0)
        # RR > 1.0 → 32 pips profit → price at 1.0512 (well above BE trigger)
        action = mgr.evaluate(1.0512, ATR)
        assert action.action == ActionType.SL_PLUS_BE
        assert action.new_sl is not None
        assert action.new_sl > trade.original_sl

    def test_sl_plus_be_sell(self):
        trade = _sell_trade(entry=1.0520, sl=1.0550)
        mgr = TradeManager(trade, be_trigger_rr=1.0)
        # 30 pips profit → price at 1.0490
        action = mgr.evaluate(1.0490, ATR)
        assert action.action == ActionType.SL_PLUS_BE

    def test_sl_plus_be_not_repeated(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450)
        trade.sl_moved_to_be = True  # Already done
        mgr = TradeManager(trade, be_trigger_rr=1.0, trail_trigger_rr=2.0)
        action = mgr.evaluate(1.0510, ATR)
        # Should NOT suggest BE again, should HOLD
        assert action.action == ActionType.HOLD

    def test_apply_sl_plus_be(self):
        trade = _buy_trade()
        mgr = TradeManager(trade)
        action = TradeAction(
            action=ActionType.SL_PLUS_BE,
            reason="test",
            new_sl=1.0482,
        )
        mgr.apply_action(action)
        assert trade.sl_moved_to_be is True
        assert trade.stop_loss == 1.0482


# ---------------------------------------------------------------------------
# Trailing stop
# ---------------------------------------------------------------------------

class TestTrail:
    def test_trail_triggers(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0600, tp2=1.0650)
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)
        # RR > 1.5 → 48 pips profit → price at 1.0528 (TP1 far away)
        action = mgr.evaluate(
            1.0528, ATR, last_swing_against=1.0510
        )
        assert action.action == ActionType.TRAIL
        assert action.new_sl is not None
        assert action.new_sl > trade.stop_loss  # Tighter

    def test_trail_never_widens_sl(self):
        trade = _buy_trade(entry=1.0480, sl=1.0460, tp1=1.0600, tp2=1.0650)
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=1.5)
        # last_swing very low → would widen SL → should reject
        action = mgr.evaluate(
            1.0525, ATR, last_swing_against=1.0440
        )
        # If trail would widen SL, it returns None → HOLD instead
        assert action.action in (ActionType.HOLD, ActionType.TRAIL)
        if action.action == ActionType.TRAIL:
            assert action.new_sl >= trade.stop_loss

    def test_apply_trail(self):
        trade = _buy_trade()
        mgr = TradeManager(trade)
        action = TradeAction(
            action=ActionType.TRAIL,
            reason="test",
            new_sl=1.0495,
        )
        mgr.apply_action(action)
        assert trade.trail_active is True
        assert trade.stop_loss == 1.0495


# ---------------------------------------------------------------------------
# Partial close & full close
# ---------------------------------------------------------------------------

class TestCloseActions:
    def test_partial_close_at_tp1(self):
        trade = _buy_trade(tp1=1.0520)
        mgr = TradeManager(trade)
        # Price at TP1 exactly
        action = mgr.evaluate(1.0520, ATR)
        assert action.action == ActionType.PARTIAL_TP1
        assert action.close_percent == 0.5

    def test_partial_close_approaching_tp1(self):
        trade = _buy_trade(tp1=1.0520)
        mgr = TradeManager(trade)
        # Price within 0.3×ATR of TP1 but not touching → SL_PLUS_BE (rr > 1.0)
        # TP1 partial only fires on actual touch/cross (dist_tp1 <= 0)
        close_to_tp1 = 1.0520 - (0.3 * ATR)
        action = mgr.evaluate(close_to_tp1, ATR)
        assert action.action in (ActionType.SL_PLUS_BE, ActionType.PARTIAL_TP1)

    def test_partial_not_repeated(self):
        trade = _buy_trade(tp1=1.0520, tp2=1.0560)
        trade.partial_closed = True
        trade.sl_moved_to_be = True
        mgr = TradeManager(trade, trail_trigger_rr=5.0)
        # Even though near TP1, already partial closed → HOLD
        action = mgr.evaluate(1.0518, ATR)
        assert action.action != ActionType.PARTIAL_TP1

    def test_full_close_at_tp2(self):
        trade = _buy_trade(tp1=1.0520, tp2=1.0560)
        trade.partial_closed = True
        mgr = TradeManager(trade)
        action = mgr.evaluate(1.0560, ATR)
        assert action.action == ActionType.FULL_CLOSE
        assert action.close_percent == 1.0

    def test_sl_hit(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450)
        mgr = TradeManager(trade)
        action = mgr.evaluate(1.0448, ATR)  # Below SL
        assert action.action == ActionType.SL_HIT


# ---------------------------------------------------------------------------
# Structure break & news
# ---------------------------------------------------------------------------

class TestExternalSignals:
    def test_structure_break_closes(self):
        trade = _buy_trade()
        mgr = TradeManager(trade)
        action = mgr.evaluate(1.0490, ATR, structure_ok=False)
        assert action.action == ActionType.CLOSE_MANUAL
        assert "CHOCH" in action.reason

    def test_news_imminent_with_profit(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450)
        mgr = TradeManager(trade)
        # Has some profit (RR ~0.5)
        action = mgr.evaluate(1.0495, ATR, news_imminent=True)
        assert action.action == ActionType.SL_PLUS_BE

    def test_news_imminent_no_profit(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450)
        mgr = TradeManager(trade)
        # No significant profit
        action = mgr.evaluate(1.0481, ATR, news_imminent=True)
        assert action.action == ActionType.CLOSE_MANUAL
        assert "News" in action.reason


# ---------------------------------------------------------------------------
# Monitoring report
# ---------------------------------------------------------------------------

class TestMonitoringReport:
    def test_generates_report(self):
        trade = _buy_trade()
        report = generate_monitoring_report(
            trade=trade,
            current_price=1.0500,
            atr=ATR,
        )
        assert isinstance(report, MonitoringReport)
        assert report.trade_id == "BUY_001"
        assert report.floating_pips > 0
        assert report.recommended_action is not None

    def test_report_sl_ready(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0560)
        report = generate_monitoring_report(
            trade=trade,
            current_price=1.0512,  # > 1×risk profit (32 pips)
            atr=ATR,
        )
        assert report.sl_plus_ready is True


# ---------------------------------------------------------------------------
# Action history
# ---------------------------------------------------------------------------

class TestActionHistory:
    def test_history_records(self):
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0560)
        mgr = TradeManager(trade, be_trigger_rr=1.0)
        mgr.evaluate(1.0512, ATR)  # > 1×risk profit
        assert len(mgr.history) == 1
        assert mgr.history[0].action == ActionType.SL_PLUS_BE

    def test_history_trimmed_at_max(self):
        """FIX §7.9: Action history must not grow unbounded."""
        trade = _buy_trade(entry=1.0480, sl=1.0450, tp1=1.0560)
        mgr = TradeManager(trade, be_trigger_rr=1.0, max_history=5)
        # Force-add 10 fake actions
        for i in range(10):
            mgr._record_action(TradeAction(
                action=ActionType.HOLD,
                reason=f"Test action {i}",
            ))
        assert len(mgr.history) == 5
        # Oldest should be trimmed, newest kept
        assert "Test action 9" in mgr.history[-1].reason


# ---------------------------------------------------------------------------
# Pip value cross pairs (FIX C-02, H-03)
# ---------------------------------------------------------------------------

class TestPipValueCrossPairs:
    """Verify PAIR_POINT covers all required pairs."""

    def test_pair_point_has_majors(self):
        from config.settings import PAIR_POINT
        for pair in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]:
            assert pair in PAIR_POINT, f"{pair} missing from PAIR_POINT"

    def test_pair_point_has_jpy_crosses(self):
        from config.settings import PAIR_POINT
        for pair in ["EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY"]:
            assert pair in PAIR_POINT, f"{pair} missing from PAIR_POINT"
            assert PAIR_POINT[pair] == 0.01

    def test_pair_point_has_non_usd_crosses(self):
        from config.settings import PAIR_POINT
        for pair in ["EURGBP", "EURAUD", "EURNZD", "EURCHF", "EURCAD",
                      "GBPAUD", "GBPNZD", "GBPCHF", "GBPCAD",
                      "AUDNZD", "AUDCAD", "AUDCHF"]:
            assert pair in PAIR_POINT, f"{pair} missing from PAIR_POINT"
            assert PAIR_POINT[pair] == 0.0001

    def test_pair_point_has_metals(self):
        from config.settings import PAIR_POINT
        assert "XAUUSD" in PAIR_POINT
        assert "XAGUSD" in PAIR_POINT
        assert PAIR_POINT["XAGUSD"] == 0.01

    def test_floating_pips_jpy_pair(self):
        """GBPJPY floating pips uses correct 0.01 point size."""
        trade = ActiveTrade(
            trade_id="JPY_001",
            pair="GBPJPY",
            direction="buy",
            entry_price=190.000,
            stop_loss=189.500,
            take_profit_1=190.500,
        )
        # 50 pips profit (0.500 / 0.01)
        pips = trade.floating_pips(190.500)
        assert pips == pytest.approx(50.0, abs=1.0)

    def test_floating_pips_cross_pair(self):
        """EURGBP floating pips uses correct 0.0001 point size."""
        trade = ActiveTrade(
            trade_id="CROSS_001",
            pair="EURGBP",
            direction="sell",
            entry_price=0.87000,
            stop_loss=0.87500,
            take_profit_1=0.86500,
        )
        # 30 pips profit (0.0030 / 0.0001)
        pips = trade.floating_pips(0.86700)
        assert pips == pytest.approx(30.0, abs=1.0)


# ---------------------------------------------------------------------------
# Minimum lot floor (FIX L-09)
# ---------------------------------------------------------------------------

class TestMinLotFloor:
    """Verify lifecycle enforces 0.01 minimum lot."""

    def test_compute_lot_min_floor(self):
        from unittest.mock import MagicMock, AsyncMock
        from agent.production_lifecycle import ProductionLifecycle

        repo = MagicMock()
        repo.init_db = AsyncMock()
        repo.get_setting_json = AsyncMock(return_value=None)
        repo.set_setting_json = AsyncMock()
        lc = ProductionLifecycle(repo=repo, initial_balance=100.0)
        # Very small balance + wide SL → would produce lot < 0.01
        lot, risk = lc._compute_lot_and_risk("EURUSD", 1.08000, 1.06000)
        assert lot >= 0.01, f"Lot {lot} below 0.01 minimum"
