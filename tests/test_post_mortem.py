"""
tests/test_post_mortem.py — Tests for PostMortemGenerator.

Validates win/loss/BE/manual analysis and SL cause detection.
"""

from __future__ import annotations

import json
import pytest

from agent.post_mortem import (
    PostMortemGenerator,
    PostMortemReport,
    MarketContext,
    SLCauseAnalysis,
    SL_CAUSE_CODES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gen() -> PostMortemGenerator:
    return PostMortemGenerator()


_BASE_KWARGS = dict(
    trade_id="TEST_001",
    pair="EURUSD",
    direction="buy",
    entry_price=1.0480,
    stop_loss=1.0450,
    take_profit_1=1.0520,
    strategy_mode="sniper_confluence",
    confluence_score=9,
    voting_confidence=0.8,
    duration_minutes=60,
)


# ---------------------------------------------------------------------------
# Win analysis
# ---------------------------------------------------------------------------

class TestWinPostMortem:
    def test_tp1_hit(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
        )
        assert report.result == "TP1_HIT"
        assert len(report.what_worked) > 0
        assert "TP1" in " ".join(report.what_worked)

    def test_tp2_hit(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0560,
            result="TP2_HIT",
            pips=80.0,
        )
        assert "TP2" in " ".join(report.what_worked)

    def test_htf_bias_aligned(self):
        gen = _gen()
        ctx = MarketContext(htf_bias_at_entry="bullish")
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
            context=ctx,
        )
        assert any("HTF" in w for w in report.what_worked)

    def test_sweep_detected(self):
        gen = _gen()
        ctx = MarketContext(sweep_at_entry=True)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
            context=ctx,
        )
        assert any("sweep" in w.lower() for w in report.what_worked)

    def test_be_protection_noted(self):
        gen = _gen()
        ctx = MarketContext(sl_was_moved_be=True)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
            context=ctx,
        )
        assert any("SL+" in w or "breakeven" in w.lower() for w in report.what_worked)


# ---------------------------------------------------------------------------
# Loss analysis
# ---------------------------------------------------------------------------

class TestLossPostMortem:
    def test_sl_hit_basic(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
        )
        assert report.result == "SL_HIT"
        assert report.sl_cause is not None
        assert len(report.what_didnt_work) > 0

    def test_news_spike_cause(self):
        gen = _gen()
        ctx = MarketContext(news_during_trade=True)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
            context=ctx,
        )
        assert report.sl_cause.primary_cause == "news_spike"

    def test_choch_cause(self):
        gen = _gen()
        ctx = MarketContext(choch_occurred=True)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
            context=ctx,
        )
        assert report.sl_cause.primary_cause == "counter_htf_ignored"

    def test_sl_too_tight(self):
        gen = _gen()
        ctx = MarketContext(atr_at_entry=0.0040)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
            context=ctx,
        )
        assert report.sl_cause.primary_cause == "sl_too_tight"
        assert report.sl_cause.suggested_param_change is not None

    def test_counter_htf(self):
        gen = _gen()
        ctx = MarketContext(htf_bias_at_entry="bearish")  # Traded buy against bearish
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
            context=ctx,
        )
        assert any("HTF" in w or "Counter" in w for w in report.what_didnt_work)

    def test_lessons_include_mitigation(self):
        gen = _gen()
        ctx = MarketContext(news_during_trade=True)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
            context=ctx,
        )
        assert len(report.lessons) > 0
        assert any("itigation" in l.lower() for l in report.lessons)

    def test_low_confluence_lesson(self):
        gen = _gen()
        report = gen.generate(
            trade_id="TEST_LOW",
            pair="EURUSD",
            direction="buy",
            entry_price=1.0480,
            stop_loss=1.0450,
            take_profit_1=1.0520,
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
            strategy_mode="sniper_confluence",
            confluence_score=5,
            voting_confidence=0.5,
            duration_minutes=30,
        )
        assert any("confluence" in l.lower() or "confidence" in l.lower() for l in report.lessons)


# ---------------------------------------------------------------------------
# Breakeven analysis
# ---------------------------------------------------------------------------

class TestBEPostMortem:
    def test_be_hit(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0480,
            result="BE_HIT",
            pips=0.0,
        )
        assert any("breakeven" in w.lower() for w in report.what_worked)
        assert len(report.what_didnt_work) > 0


# ---------------------------------------------------------------------------
# Manual close
# ---------------------------------------------------------------------------

class TestManualClose:
    def test_manual_close_profit(self):
        gen = _gen()
        ctx = MarketContext(choch_occurred=True)
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0500,
            result="MANUAL_CLOSE",
            pips=20.0,
            context=ctx,
        )
        assert any("Manual close in profit" in w for w in report.what_worked)
        assert any("structure" in w.lower() for w in report.what_worked)

    def test_manual_close_loss(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0460,
            result="MANUAL_CLOSE",
            pips=-20.0,
        )
        assert any("loss" in w.lower() for w in report.what_didnt_work)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestReportSerialization:
    def test_to_dict(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
        )
        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["trade_id"] == "TEST_001"

    def test_to_json(self):
        gen = _gen()
        report = gen.generate(
            **_BASE_KWARGS,
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
        )
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["pair"] == "EURUSD"

    def test_sl_cause_codes_complete(self):
        # Ensure all documented cause codes exist
        expected = {
            "sweep_extended",
            "news_spike",
            "zone_weak",
            "choch_premature",
            "counter_htf_ignored",
            "sl_too_tight",
            "timing_late",
            "correlation_miss",
        }
        assert set(SL_CAUSE_CODES.keys()) == expected


# ---------------------------------------------------------------------------
# Duration analysis
# ---------------------------------------------------------------------------

class TestDurationAnalysis:
    def test_very_short_trade(self):
        gen = _gen()
        report = gen.generate(
            **{**_BASE_KWARGS, "duration_minutes": 10},
            exit_price=1.0450,
            result="SL_HIT",
            pips=-30.0,
        )
        assert any("short" in l.lower() for l in report.lessons)

    def test_very_long_trade(self):
        gen = _gen()
        report = gen.generate(
            **{**_BASE_KWARGS, "duration_minutes": 600},
            exit_price=1.0520,
            result="TP1_HIT",
            pips=40.0,
        )
        assert any("duration" in l.lower() or "long" in l.lower() for l in report.lessons)
