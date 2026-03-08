"""
tests/test_demo_tracker.py — Tests for DemoTracker & ModeManager.

Validates virtual balance tracking, graduation criteria,
drawdown protection, and mode switching.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent.demo_tracker import (
    DemoTracker,
    DemoTradeRecord,
    ModeManager,
    MaxDrawdownExceeded,
    GraduationNotReady,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _win_trade(trade_id: str = "W1", pips: float = 20.0, rr: float = 1.5) -> DemoTradeRecord:
    return DemoTradeRecord(
        trade_id=trade_id,
        pair="EURUSD",
        direction="buy",
        entry_price=1.0480,
        stop_loss=1.0450,
        take_profit_1=1.0520,
        exit_price=1.0510,
        result="TP1_HIT",
        pips=pips,
        rr_achieved=rr,
    )


def _loss_trade(trade_id: str = "L1", pips: float = -25.0) -> DemoTradeRecord:
    return DemoTradeRecord(
        trade_id=trade_id,
        pair="EURUSD",
        direction="buy",
        entry_price=1.0480,
        stop_loss=1.0450,
        take_profit_1=1.0520,
        exit_price=1.0455,
        result="SL_HIT",
        pips=pips,
        rr_achieved=0.0,
    )


# ---------------------------------------------------------------------------
# DemoTracker basics
# ---------------------------------------------------------------------------

class TestDemoTracker:
    def test_initial_state(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        assert tracker.balance == 10_000.0
        assert tracker.high_water_mark == 10_000.0
        assert len(tracker.trades) == 0

    def test_record_win(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        trade = _win_trade(rr=1.5)
        result = tracker.record_trade(trade)

        assert result.demo_pnl > 0
        assert tracker.balance > 10_000.0
        assert result.demo_balance_after == tracker.balance
        assert len(tracker.trades) == 1

    def test_record_loss(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        trade = _loss_trade()
        result = tracker.record_trade(trade)

        assert result.demo_pnl < 0
        assert tracker.balance < 10_000.0
        assert len(tracker.trades) == 1

    def test_balance_tracks_correctly(self):
        tracker = DemoTracker(initial_balance=10_000.0, risk_per_trade=0.01)
        # Win: +1.5% of 1% risk = +$150
        tracker.record_trade(_win_trade("W1", rr=1.5))
        assert tracker.balance == pytest.approx(10_150.0, rel=0.01)

        # Loss: -$101.5 (1% of current balance)
        tracker.record_trade(_loss_trade("L1"))
        assert tracker.balance < 10_150.0

    def test_high_water_mark_updates(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        tracker.record_trade(_win_trade("W1", rr=2.0))
        hwm_after_win = tracker.high_water_mark
        assert hwm_after_win > 10_000.0

        tracker.record_trade(_loss_trade("L1"))
        # HWM should NOT decrease
        assert tracker.high_water_mark == hwm_after_win

    def test_be_hit_zero_pnl(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        trade = DemoTradeRecord(
            trade_id="BE1",
            pair="EURUSD",
            direction="buy",
            entry_price=1.0480,
            stop_loss=1.0480,
            take_profit_1=1.0520,
            exit_price=1.0480,
            result="BE_HIT",
            pips=0.0,
            rr_achieved=0.0,
        )
        tracker.record_trade(trade)
        assert trade.demo_pnl == 0.0
        assert tracker.balance == 10_000.0


# ---------------------------------------------------------------------------
# Graduation checks
# ---------------------------------------------------------------------------

class TestGraduation:
    def test_not_enough_trades(self):
        tracker = DemoTracker(graduation_min_trades=30)
        for i in range(10):
            tracker.record_trade(_win_trade(f"W{i}"))
        grad = tracker.check_graduation()
        assert grad["ready"] is False
        assert "30 trades" in grad["reason"]

    def test_low_winrate(self):
        tracker = DemoTracker(
            graduation_min_trades=10,
            graduation_min_winrate=0.60,
        )
        # 4 wins, 6 losses = 40% winrate
        for i in range(4):
            tracker.record_trade(_win_trade(f"W{i}", pips=20))
        for i in range(6):
            tracker.record_trade(_loss_trade(f"L{i}", pips=-20))

        grad = tracker.check_graduation()
        assert grad["ready"] is False
        assert "Win rate" in grad["reason"]

    def test_low_expectancy(self):
        tracker = DemoTracker(
            graduation_min_trades=5,
            graduation_min_winrate=0.50,
            graduation_min_expectancy=10.0,
        )
        # All wins but very small pips → low expectancy
        for i in range(5):
            tracker.record_trade(_win_trade(f"W{i}", pips=3.0, rr=0.3))

        grad = tracker.check_graduation()
        assert grad["ready"] is False
        assert "expectancy" in grad["reason"].lower()

    def test_graduation_passes(self):
        tracker = DemoTracker(
            graduation_min_trades=5,
            graduation_min_winrate=0.50,
            graduation_min_expectancy=5.0,
        )
        # 4 wins, 1 loss
        for i in range(4):
            tracker.record_trade(_win_trade(f"W{i}", pips=20.0, rr=1.5))
        tracker.record_trade(_loss_trade("L1", pips=-10.0))

        grad = tracker.check_graduation()
        assert grad["ready"] is True
        assert "stats" in grad


# ---------------------------------------------------------------------------
# Drawdown protection
# ---------------------------------------------------------------------------

class TestDrawdown:
    def test_total_drawdown_raises(self):
        tracker = DemoTracker(
            initial_balance=10_000.0,
            risk_per_trade=0.10,    # 10% risk for faster testing
            max_total_drawdown=0.15,
        )
        with pytest.raises(MaxDrawdownExceeded):
            # Multiple losses should trigger drawdown
            for i in range(5):
                tracker.record_trade(_loss_trade(f"L{i}"))

    def test_daily_drawdown_raises(self):
        tracker = DemoTracker(
            initial_balance=10_000.0,
            risk_per_trade=0.05,   # 5% risk to hit daily DD fast
            max_daily_drawdown=0.05,
        )
        with pytest.raises(MaxDrawdownExceeded):
            for i in range(3):
                tracker.record_trade(_loss_trade(f"L{i}"))

    def test_daily_reset(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        tracker.record_trade(_win_trade("W1", rr=2.0))
        new_bal = tracker.balance
        tracker.reset_daily()
        assert tracker.daily_start_balance == new_bal


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        tracker.record_trade(_win_trade("W1", rr=1.5))
        d = tracker.to_dict()
        assert "balance" in d
        assert "trade_count" in d
        assert d["trade_count"] == 1

    def test_from_dict(self):
        data = {
            "initial_balance": 10_000.0,
            "balance": 10_500.0,
            "high_water_mark": 10_500.0,
            "daily_start_balance": 10_500.0,
        }
        tracker = DemoTracker.from_dict(data)
        assert tracker.balance == 10_500.0
        assert tracker.high_water_mark == 10_500.0


# ---------------------------------------------------------------------------
# ModeManager
# ---------------------------------------------------------------------------

class TestModeManager:
    def test_initial_mode(self):
        mgr = ModeManager()
        # From TRADING_MODE env var or default
        assert mgr.mode in ("demo", "real")

    def test_demo_trade_records(self):
        tracker = DemoTracker(initial_balance=10_000.0)
        mgr = ModeManager(tracker=tracker)
        mgr.mode = "demo"

        result = mgr.on_trade_closed(_win_trade("W1"))
        # No graduation yet (< 30 trades)
        assert result is None
        assert len(tracker.trades) == 1

    def test_real_mode_no_tracking(self):
        tracker = DemoTracker()
        mgr = ModeManager(tracker=tracker)
        mgr.mode = "real"

        result = mgr.on_trade_closed(_win_trade("W1"))
        assert result is None
        assert len(tracker.trades) == 0  # Not tracked in real mode

    def test_switch_to_real_fails_without_graduation(self):
        tracker = DemoTracker(graduation_min_trades=30)
        mgr = ModeManager(tracker=tracker)
        mgr.mode = "demo"

        with pytest.raises(GraduationNotReady):
            mgr.switch_to_real()

    def test_switch_to_real_succeeds(self):
        tracker = DemoTracker(
            graduation_min_trades=5,
            graduation_min_winrate=0.50,
            graduation_min_expectancy=5.0,
        )
        mgr = ModeManager(tracker=tracker)
        mgr.mode = "demo"

        for i in range(4):
            tracker.record_trade(_win_trade(f"W{i}", pips=20, rr=1.5))
        tracker.record_trade(_loss_trade("L1", pips=-10))

        result = mgr.switch_to_real()
        assert result["ready"] is True
        assert mgr.mode == "real"

    def test_force_real(self):
        mgr = ModeManager()
        mgr.mode = "demo"
        mgr.force_real()
        assert mgr.mode == "real"

    def test_force_demo(self):
        mgr = ModeManager()
        mgr.mode = "real"
        mgr.force_demo()
        assert mgr.mode == "demo"

    def test_auto_graduate(self):
        tracker = DemoTracker(
            graduation_min_trades=3,
            graduation_min_winrate=0.50,
            graduation_min_expectancy=5.0,
        )
        mgr = ModeManager(tracker=tracker, auto_graduate=True)
        mgr.mode = "demo"

        # Record enough winning trades
        for i in range(2):
            mgr.on_trade_closed(_win_trade(f"W{i}", pips=20, rr=1.5))

        # Third trade should trigger graduation check
        result = mgr.on_trade_closed(_win_trade("W3", pips=20, rr=1.5))
        assert result is not None
        assert result["ready"] is True
