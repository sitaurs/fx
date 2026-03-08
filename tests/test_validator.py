"""
tests/test_validator.py — Tests for trading plan validator.

Reference: masterplan.md §5.3 (Anti-Rungkad), §13
"""

from __future__ import annotations

import pytest

from tools.validator import validate_trading_plan
from tools.scorer import score_setup_candidate


class TestValidateTradingPlan:

    def test_valid_buy_setup_passes(self):
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bullish", zone_freshness="fresh"
        )
        assert result["passed"] is True
        assert result["risk_reward"] == 2.0  # (30/15)
        assert len(result["violations"]) == 0

    def test_valid_sell_setup_passes(self):
        setup = {"entry": 2050.0, "sl": 2065.0, "tp": 2020.0, "direction": "sell"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bearish", zone_freshness="fresh"
        )
        assert result["passed"] is True
        assert result["risk_reward"] == 2.0

    def test_rr_too_low_fails(self):
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2010.0, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=10.0)
        # RR = 10/15 = 0.67 < 1.5
        assert result["passed"] is False
        assert any("R:R too low" in v for v in result["violations"])

    def test_sl_too_tight_fails(self):
        setup = {"entry": 2000.0, "sl": 1998.0, "tp": 2020.0, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=10.0)
        # SL = 2/10 = 0.2 ATR < 0.5
        assert result["passed"] is False
        assert any("SL too tight" in v for v in result["violations"])

    def test_sl_too_wide_fails(self):
        setup = {"entry": 2000.0, "sl": 1970.0, "tp": 2100.0, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=10.0)
        # SL = 30/10 = 3.0 ATR > 2.5
        assert result["passed"] is False
        assert any("SL too wide" in v for v in result["violations"])

    def test_counter_trend_violates_when_config_enforced(self):
        """FP-11 H-12: When must_not_counter_htf=True, counter-trend is a violation."""
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bearish", zone_freshness="fresh"
        )
        # VALIDATION_RULES["must_not_counter_htf"] is True by default
        assert result["passed"] is False
        assert any("Counter-trend" in v for v in result["violations"])

    def test_mitigated_zone_fails(self):
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bullish", zone_freshness="mitigated"
        )
        assert result["passed"] is False
        assert any("mitigated" in v for v in result["violations"])

    def test_touched_zone_warns(self):
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bullish", zone_freshness="touched"
        )
        assert result["passed"] is True
        assert any("touched" in w for w in result["warnings"])

    def test_invalid_sl_placement(self):
        # SL above entry for a buy → invalid
        setup = {"entry": 2000.0, "sl": 2010.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=10.0)
        assert result["passed"] is False


class TestScoreSetup:

    def test_max_score(self):
        result = score_setup_candidate(
            htf_alignment=True,
            fresh_zone=True,
            sweep_detected=True,
            near_major_snr=True,
            pa_confirmed=True,
            ema_filter_ok=True,
            rsi_filter_ok=True,
        )
        assert result["score"] == 14
        assert result["tradeable"] is True

    def test_min_tradeable_score(self):
        result = score_setup_candidate(
            htf_alignment=True,
            fresh_zone=True,
        )
        assert result["score"] == 5
        assert result["tradeable"] is True

    def test_below_threshold_not_tradeable(self):
        result = score_setup_candidate(
            fresh_zone=True,
            ema_filter_ok=True,
        )
        assert result["score"] == 3
        assert result["tradeable"] is False

    def test_penalties_reduce_score(self):
        result = score_setup_candidate(
            htf_alignment=True,
            fresh_zone=True,
            sweep_detected=True,
            counter_htf_bias=True,  # -3
        )
        assert result["score"] == 8 - 3  # 5
        assert result["tradeable"] is True

    def test_heavy_penalties_below_threshold(self):
        result = score_setup_candidate(
            fresh_zone=True,
            ema_filter_ok=True,
            sl_too_wide=True,        # -2
            zone_mitigated=True,     # -2
        )
        # FIX F0-05: score clamped to 0 (floor), was -1
        assert result["score"] == 0
        assert result["tradeable"] is False
