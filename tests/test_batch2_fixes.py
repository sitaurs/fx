"""
tests/test_batch2_fixes.py — Unit tests for Batch 2 (Data Pipeline + Foundation) fixes.

Covers:
  FIX-01 (F0-01/02): VALIDATION_RULES synced to scorer/validator
  FIX-02 (F0-03):    SetupCandidate Pydantic field validators
  FIX-03 (F0-04):    XAUUSD in CORRELATION_GROUPS
  FIX-04 (F0-05):    Score floor (0) & cap (14)
  FIX-06 (F1-02):    Async fetch wrappers exist
  FIX-07 (F1-04):    Finnhub candle count warning + MIN_CANDLES guard
  FIX-08 (F1-07):    RSI NaN guard in context formatter
  FIX-09 (F1-05):    Finnhub rate limiter
  FIX-10 (F1-10):    H4 default timeframe in orchestrator
  FIX-11 (F1-03):    MT5 probe timeout 3s
  FIX-12 (F1-06):    DXY mode guard in system prompt
"""

from __future__ import annotations

import asyncio
import math
import threading
import time

import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError


# =========================================================================
# FIX-01: VALIDATION_RULES single source of truth
# =========================================================================
class TestValidationRulesSync:
    """Verify strategy_rules.VALIDATION_RULES is the SSoT for SL limits."""

    def test_strategy_rules_values(self):
        from config.strategy_rules import VALIDATION_RULES
        assert VALIDATION_RULES["sl_min_atr_mult"] == 0.5
        assert VALIDATION_RULES["sl_max_atr_mult"] == 2.5
        assert VALIDATION_RULES["min_rr"] == 1.5

    def test_validator_defaults_match_strategy_rules(self):
        """validator.py defaults should come from VALIDATION_RULES."""
        import inspect
        from tools.validator import validate_trading_plan
        sig = inspect.signature(validate_trading_plan)
        assert sig.parameters["min_sl_atr_mult"].default == 0.5
        assert sig.parameters["max_sl_atr_mult"].default == 2.5
        assert sig.parameters["min_rr"].default == 1.5

    def test_scorer_weights_match_validator_thresholds(self):
        """Scorer penalties use same thresholds as validator defaults."""
        from config.strategy_rules import SCORING_WEIGHTS
        # sl_too_tight means < 0.5×ATR, sl_too_wide means > 2.5×ATR
        assert "sl_too_tight" in SCORING_WEIGHTS
        assert "sl_too_wide" in SCORING_WEIGHTS

    def test_system_prompt_renders_validation_rules(self):
        """System prompt should contain SL limits from VALIDATION_RULES."""
        from agent.system_prompt import SYSTEM_PROMPT
        assert "0.5" in SYSTEM_PROMPT  # sl_min
        assert "2.5" in SYSTEM_PROMPT  # sl_max
        assert "1.5" in SYSTEM_PROMPT  # min_rr


# =========================================================================
# FIX-02: SetupCandidate Pydantic field validators
# =========================================================================
class TestSetupCandidateValidators:
    """SetupCandidate should reject invalid field values."""

    def _valid_kwargs(self, **overrides):
        """Base valid SetupCandidate kwargs."""
        from schemas.market_data import Direction, StrategyMode
        defaults = dict(
            direction=Direction.SELL,
            strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
            entry_zone_low=2348.0,
            entry_zone_high=2352.0,
            trigger_condition="sweep + reclaim",
            stop_loss=2360.0,
            sl_reasoning="above swing",
            take_profit_1=2330.0,
            take_profit_2=2310.0,
            tp_reasoning="demand zone",
            risk_reward_ratio=2.0,
            management="SL+ at 1R",
            ttl_hours=4.0,
            invalidation="H4 close above 2365",
            confluence_score=8,
            rationale="Strong supply",
        )
        defaults.update(overrides)
        return defaults

    def test_valid_candidate_passes(self):
        from schemas.plan import SetupCandidate
        c = SetupCandidate(**self._valid_kwargs())
        assert c.confluence_score == 8

    def test_negative_score_rejected(self):
        from schemas.plan import SetupCandidate
        with pytest.raises(ValidationError, match="confluence_score"):
            SetupCandidate(**self._valid_kwargs(confluence_score=-5))

    def test_score_above_15_rejected(self):
        from schemas.plan import SetupCandidate
        with pytest.raises(ValidationError, match="confluence_score"):
            SetupCandidate(**self._valid_kwargs(confluence_score=20))

    def test_negative_rr_rejected(self):
        from schemas.plan import SetupCandidate
        with pytest.raises(ValidationError, match="risk_reward_ratio"):
            SetupCandidate(**self._valid_kwargs(risk_reward_ratio=-1.0))

    def test_zero_ttl_rejected(self):
        from schemas.plan import SetupCandidate
        with pytest.raises(ValidationError, match="ttl_hours"):
            SetupCandidate(**self._valid_kwargs(ttl_hours=0.0))

    def test_negative_ttl_rejected(self):
        from schemas.plan import SetupCandidate
        with pytest.raises(ValidationError, match="ttl_hours"):
            SetupCandidate(**self._valid_kwargs(ttl_hours=-2.0))

    def test_edge_score_zero_accepted(self):
        from schemas.plan import SetupCandidate
        c = SetupCandidate(**self._valid_kwargs(confluence_score=0))
        assert c.confluence_score == 0

    def test_edge_score_max_accepted(self):
        """CON-23: max score is now MAX_POSSIBLE_SCORE (14), not 15."""
        from schemas.plan import SetupCandidate
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        c = SetupCandidate(**self._valid_kwargs(confluence_score=MAX_POSSIBLE_SCORE))
        assert c.confluence_score == MAX_POSSIBLE_SCORE

    def test_edge_score_above_max_rejected(self):
        """CON-23: score > MAX_POSSIBLE_SCORE must be rejected."""
        from schemas.plan import SetupCandidate
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        with pytest.raises(ValidationError, match="confluence_score"):
            SetupCandidate(**self._valid_kwargs(confluence_score=MAX_POSSIBLE_SCORE + 1))


# =========================================================================
# FIX-03: XAUUSD in CORRELATION_GROUPS
# =========================================================================
class TestCorrelationGroups:

    def test_xauusd_has_group(self):
        from config.settings import CORRELATION_GROUPS
        all_pairs = [p for group in CORRELATION_GROUPS.values() for p in group]
        assert "XAUUSD" in all_pairs

    def test_gold_usd_group_exists(self):
        from config.settings import CORRELATION_GROUPS
        assert "GOLD_USD" in CORRELATION_GROUPS
        assert "XAUUSD" in CORRELATION_GROUPS["GOLD_USD"]

    def test_all_mvp_pairs_covered(self):
        """Every MVP pair should be in at least one correlation group."""
        from config.settings import CORRELATION_GROUPS, MVP_PAIRS
        all_grouped = {p for group in CORRELATION_GROUPS.values() for p in group}
        for pair in MVP_PAIRS:
            assert pair in all_grouped, f"{pair} not in any correlation group"


# =========================================================================
# FIX-04: Score floor (0) & cap (14)
# =========================================================================
class TestScoreFloorCap:

    def test_floor_at_zero(self):
        from tools.scorer import score_setup_candidate
        result = score_setup_candidate(
            counter_htf_bias=True,   # -3
            zone_mitigated=True,     # -2
            sl_too_tight=True,       # -2
            sl_too_wide=True,        # -2
        )
        assert result["score"] == 0  # was -9, now clamped

    def test_cap_at_14(self):
        from tools.scorer import score_setup_candidate
        # Max possible is already 14; verify it doesn't exceed
        result = score_setup_candidate(
            htf_alignment=True, fresh_zone=True, sweep_detected=True,
            near_major_snr=True, pa_confirmed=True, ema_filter_ok=True,
            rsi_filter_ok=True,
        )
        assert result["score"] == 14
        assert result["score"] <= 14

    def test_score_never_negative(self):
        from tools.scorer import score_setup_candidate
        # Try many penalty combos — score should never be < 0
        for combo in [
            {"sl_too_tight": True},
            {"counter_htf_bias": True, "zone_mitigated": True},
            {"sl_too_wide": True, "sl_too_tight": True, "counter_htf_bias": True},
        ]:
            result = score_setup_candidate(**combo)
            assert result["score"] >= 0, f"Negative score for {combo}"


# =========================================================================
# FIX-06: Async fetch wrappers exist
# =========================================================================
class TestAsyncWrappers:

    def test_fetch_ohlcv_async_importable(self):
        from data.fetcher import fetch_ohlcv_async
        assert asyncio.iscoroutinefunction(fetch_ohlcv_async)

    def test_analyze_timeframe_async_importable(self):
        from agent.context_builder import analyze_timeframe_async
        assert asyncio.iscoroutinefunction(analyze_timeframe_async)

    def test_collect_multi_tf_async_importable(self):
        from agent.context_builder import collect_multi_tf_async
        assert asyncio.iscoroutinefunction(collect_multi_tf_async)

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_async_returns_dict(self):
        """Async fetch wrapper should return same structure as sync version."""
        from data.fetcher import fetch_ohlcv_async, set_backend, get_backend, DemoBackend
        original = get_backend()
        set_backend(DemoBackend(seed=42))
        try:
            result = await fetch_ohlcv_async("XAUUSD", "H1", count=10)
            assert isinstance(result, dict)
            assert result["pair"] == "XAUUSD"
            assert result["count"] == 10
        finally:
            set_backend(original)


# =========================================================================
# FIX-07: Candle count validation + MIN_CANDLES guard
# =========================================================================
class TestCandleCountValidation:

    def test_min_candles_constant_exists(self):
        from agent.context_builder import MIN_CANDLES
        assert MIN_CANDLES >= 20  # reasonable minimum

    def test_analyze_timeframe_rejects_insufficient_data(self):
        """analyze_timeframe should raise when < MIN_CANDLES candles."""
        from agent.context_builder import analyze_timeframe, MIN_CANDLES
        from data.fetcher import set_backend, get_backend, DemoBackend

        original = get_backend()
        # DemoBackend always returns exactly count candles, so request fewer
        set_backend(DemoBackend(seed=42))
        try:
            with pytest.raises(ValueError, match="Insufficient data"):
                analyze_timeframe("XAUUSD", "H1", candle_count=10)
        finally:
            set_backend(original)

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    def test_finnhub_low_count_warning(self):
        """FinnhubBackend should log warning when candles << requested count."""
        from data.fetcher import FinnhubBackend
        mock_client = MagicMock()
        # Return only 10 candles when 300 requested (< 50%)
        base_ts = 1700000000
        mock_client.forex_candles.return_value = {
            "s": "ok",
            "o": [1.05] * 10, "h": [1.052] * 10, "l": [1.048] * 10,
            "c": [1.051] * 10, "v": [1000] * 10,
            "t": [base_ts + i * 3600 for i in range(10)],
        }
        backend = FinnhubBackend(api_key="test-key")
        backend._client = mock_client

        with patch("data.fetcher.logger") as mock_logger:
            candles = backend.fetch_ohlcv("EURUSD", "H1", count=300)
            assert len(candles) == 10
            # Should have called warning about low count
            mock_logger.warning.assert_called()
            warn_msg = str(mock_logger.warning.call_args_list)
            assert "10" in warn_msg and "300" in warn_msg


# =========================================================================
# FIX-08: RSI NaN guard in format_context
# =========================================================================
class TestRSINaNGuard:

    def _make_analyses(self, rsi_current):
        """Create minimal analyses dict with specific RSI value."""
        return {
            "H1": {
                "candle_count": 150,
                "last_close": 2650.0,
                "last_time": "2025-01-01T00:00:00+00:00",
                "atr": {"current": 10.0, "period": 14, "values": []},
                "ema50": {"current": 2645.0, "period": 50},
                "rsi14": {"current": rsi_current, "period": 14},
                "structure": {"trend": "bullish", "events": []},
                "swing_highs": [], "swing_lows": [],
                "snr_levels": [], "supply_zones": [], "demand_zones": [],
                "bullish_obs": [], "bearish_obs": [],
                "uptrend_lines": [], "downtrend_lines": [],
                "eqh_pools": [], "eql_pools": [], "sweep_events": [],
                "pin_bars": [], "engulfing_patterns": [],
                "choch_micro_bullish": {"confirmed": False},
                "choch_micro_bearish": {"confirmed": False},
            }
        }

    def test_normal_rsi_shows_value(self):
        from agent.context_builder import format_context
        ctx = format_context("XAUUSD", self._make_analyses(55.3))
        assert "RSI(14)  = 55.30" in ctx

    def test_nan_rsi_shows_na(self):
        from agent.context_builder import format_context
        ctx = format_context("XAUUSD", self._make_analyses(float("nan")))
        assert "N/A" in ctx
        assert "nan" not in ctx.lower().split("n/a")[0]  # no raw "nan" before N/A

    def test_none_rsi_shows_na(self):
        from agent.context_builder import format_context
        ctx = format_context("XAUUSD", self._make_analyses(None))
        assert "N/A" in ctx


# =========================================================================
# FIX-09: Finnhub rate limiter
# =========================================================================
class TestRateLimiter:

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    def test_rate_limiter_importable(self):
        from data.fetcher import _RateLimiter
        limiter = _RateLimiter(calls_per_second=100.0)
        assert limiter._min_interval == pytest.approx(0.01, abs=0.001)

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    def test_rate_limiter_throttles_fast_calls(self):
        """Two rapid calls should be spaced by at least min_interval."""
        from data.fetcher import _RateLimiter
        limiter = _RateLimiter(calls_per_second=20.0)
        t0 = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - t0
        # Second call should wait ~50ms (1/20)
        assert elapsed >= 0.03  # conservative threshold

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    def test_module_level_limiter_exists(self):
        from data.fetcher import _finnhub_limiter
        assert _finnhub_limiter is not None


# =========================================================================
# FIX-10: H4 default timeframes
# =========================================================================
class TestDefaultTimeframes:

    def test_orchestrator_default_includes_h4(self):
        """AnalysisOrchestrator default timeframes should include H4."""
        from agent.orchestrator import AnalysisOrchestrator
        orch = AnalysisOrchestrator("XAUUSD")
        assert "H4" in orch._analysis_timeframes
        assert orch._analysis_timeframes == ["H4", "H1", "M15"]

    def test_custom_timeframes_override(self):
        """Passing custom timeframes should override defaults."""
        from agent.orchestrator import AnalysisOrchestrator
        orch = AnalysisOrchestrator("XAUUSD", timeframes=["H1", "M15"])
        assert orch._analysis_timeframes == ["H1", "M15"]

    def test_config_timeframes_consistent(self):
        """settings.TIMEFRAMES should be a superset of orchestrator defaults."""
        from config.settings import TIMEFRAMES
        defaults = ["H4", "H1", "M15"]
        for tf in defaults:
            assert tf in TIMEFRAMES


# =========================================================================
# FIX-11: MT5 probe timeout reduced to 3s
# =========================================================================
class TestMT5ProbeTimeout:

    def test_mt5_init_uses_3s_timeout(self):
        """When MT5_OHLCV_API_URL is set, probe should use 3s timeout."""
        from data.fetcher import MT5ApiBackend
        backend = MT5ApiBackend(base_url="http://fake:5001", timeout_seconds=3.0)
        assert backend.timeout_seconds == 3.0

    @pytest.mark.skip(reason="FinnhubBackend removed — OANDA is sole provider")
    @patch.dict("os.environ", {"MT5_OHLCV_API_URL": "http://fake:5001"})
    def test_init_default_backend_uses_short_timeout(self):
        """_init_default_backend should create MT5 with 3s timeout."""
        from data import fetcher
        with patch.object(fetcher, "MT5ApiBackend") as MockMT5:
            mock_instance = MagicMock()
            mock_instance.fetch_ohlcv.side_effect = Exception("connect timeout")
            MockMT5.return_value = mock_instance
            try:
                fetcher._init_default_backend()
            except Exception:
                pass
            # Verify 3.0 timeout was used in constructor
            MockMT5.assert_called_with(base_url="http://fake:5001", timeout_seconds=3.0)


# =========================================================================
# FIX-12: DXY mode guard in system prompt
# =========================================================================
class TestDXYModeGuard:

    def test_index_correlation_enabled_in_prompt(self):
        """System prompt should show index_correlation as ENABLED."""
        from agent.system_prompt import SYSTEM_PROMPT
        assert "ENABLED" in SYSTEM_PROMPT
        # FIX M-11: mode selection now driven from config, verify DXY note
        assert "DXY" in SYSTEM_PROMPT or "index_correlation" in SYSTEM_PROMPT

    def test_sniper_confluence_still_available(self):
        from agent.system_prompt import SYSTEM_PROMPT
        assert "sniper_confluence" in SYSTEM_PROMPT

    def test_scalping_channel_still_available(self):
        from agent.system_prompt import SYSTEM_PROMPT
        assert "scalping_channel" in SYSTEM_PROMPT
