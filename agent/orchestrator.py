"""
agent/orchestrator.py — Main analysis pipeline.

Coordinates:
  1. GeminiClient (hybrid Flash/Pro model switching)
  2. StateMachine (6+1 state lifecycle)
  3. VotingEngine (anti-hallucination ensemble)
  4. All 17 Python tools (via automatic function calling)

The Gemini SDK handles tool invocation automatically — we register Python
functions in ``tool_registry.ALL_TOOLS`` and the API calls them under the
hood.  The orchestrator manages *state*, *voting*, and the overall lifecycle.

Reference: masterplan.md §3 (Architecture), §2.1 (Hybrid Strategy),
         §8/§12 (Voting, Anti-Flip-Flop).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from config.settings import (
    VOTING_RUNS,
    MIN_SCORE_FOR_TRADE,
    MIN_CONFIDENCE,
    COOLDOWN_MINUTES,
    ANALYSIS_TIMEFRAMES,
)
from schemas.plan import SetupCandidate, TradingPlan
from agent.gemini_client import GeminiClient, BudgetExceededError, model_for_state
from agent.state_machine import (
    AnalysisState,
    SetupContext,
    StateMachine,
    IllegalTransition,
    ConvictionLockViolation,
)
from agent.voting import VotingDecision, VotingEngine, VotingResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class AnalysisOutcome:
    """Outcome of a single orchestrated analysis run."""

    pair: str
    state: AnalysisState
    plan: Optional[TradingPlan] = None
    voting_result: Optional[VotingResult] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AnalysisOrchestrator:
    """Main analysis pipeline for a single trading pair.

    Usage::

        orch = AnalysisOrchestrator(pair="XAUUSD")
        outcome = await orch.run_scan()
        if outcome.plan:
            print(outcome.plan.model_dump_json(indent=2))

    The orchestrator follows these phases (masterplan §3):

        SCANNING  → tool-calling analysis (Flash)
        WATCHING  → monitor updates (Flash)
        APPROACHING → deep analysis (Pro + thinking=high)
        TRIGGERED → voting ensemble (Pro), structured output
        ACTIVE   → trade management updates (Flash)
    """

    def __init__(
        self,
        pair: str,
        *,
        client: GeminiClient | None = None,
        timeframes: list[str] | None = None,
    ) -> None:
        self.pair = pair
        self._client = client or GeminiClient()
        self._sm = StateMachine()
        self._voting = VotingEngine()
        self._last_plan: Optional[TradingPlan] = None
        self._analysis_timeframes = timeframes or ANALYSIS_TIMEFRAMES
        self._last_context: Optional[str] = None      # raw text sent to Gemini
        self._last_analyses: Optional[dict] = None     # raw tool outputs
        # FIX M-07: Per-pair Gemini call timing tracker
        self._phase_timings: dict[str, float] = {}     # phase → elapsed_seconds

    # -- Properties ---------------------------------------------------------

    @property
    def state(self) -> AnalysisState:
        return self._sm.state

    @property
    def last_plan(self) -> Optional[TradingPlan]:
        return self._last_plan

    @property
    def phase_timings(self) -> dict[str, float]:
        """Per-phase timing data from the last run_scan (FIX M-07)."""
        return dict(self._phase_timings)

    # -- Main entry: full scan → plan or None --------------------------------

    async def run_scan(self) -> AnalysisOutcome:
        """Execute a full scan cycle: SCANNING → (WATCHING →) TradingPlan.

        Returns an ``AnalysisOutcome`` containing the plan if a valid
        setup is found, or ``None`` + reason if not.
        """
        t0 = time.time()

        # Guard: cooldown check
        if self._sm.is_in_cooldown():
            return AnalysisOutcome(
                pair=self.pair,
                state=self._sm.state,
                error="Pair is in cooldown after cancellation",
                elapsed_seconds=time.time() - t0,
            )

        try:
            # Phase 1-6: Gemini analyses pair via auto-function-calling.
            # The LLM calls tools (indicators, swing, zone, etc.) and
            # reasons about results.  We ask for a SetupCandidate back.
            candidate = await self._phase_analyze()

            if candidate is None:
                return AnalysisOutcome(
                    pair=self.pair,
                    state=self._sm.state,
                    error="No valid setup found during analysis",
                    elapsed_seconds=time.time() - t0,
                )

            # Decide voting strategy based on initial score.
            score = candidate.confluence_score
            decision = self._voting.decide(score)
            logger.info(
                "%s  score=%d  decision=%s", self.pair, score, decision.value
            )

            # --- FIX F3-09: Drive state transitions based on score ----------
            # Without this, state stays SCANNING forever and Pro model
            # is never used for high-score setups.
            direction = (
                candidate.direction.value
                if hasattr(candidate.direction, "value")
                else candidate.direction
            )
            strategy_mode = (
                candidate.strategy_mode.value
                if hasattr(candidate.strategy_mode, "value")
                else candidate.strategy_mode
            )
            entry_mid = (candidate.entry_zone_low + candidate.entry_zone_high) / 2
            htf_bias = "bullish" if direction == "buy" else "bearish"

            def _try_transition(target: AnalysisState) -> None:
                """Attempt transition, ignore if illegal (idempotent)."""
                try:
                    self.transition_to(
                        target,
                        score=score,
                        confidence=0.0,
                        direction=direction,
                        strategy_mode=strategy_mode,
                        entry_zone_mid=entry_mid,
                        htf_bias=htf_bias,
                    )
                except (IllegalTransition, ConvictionLockViolation) as exc:
                    logger.debug("Transition to %s skipped: %s", target.value, exc)

            if decision == VotingDecision.REJECT:
                # Stay at SCANNING — no transition needed
                pass
            elif score >= MIN_SCORE_FOR_TRADE:
                # score >= 5: advance through WATCHING → APPROACHING → TRIGGERED
                _try_transition(AnalysisState.WATCHING)
                _try_transition(AnalysisState.APPROACHING)
                _try_transition(AnalysisState.TRIGGERED)
            else:
                # score > 0 but < threshold: advance to WATCHING
                _try_transition(AnalysisState.WATCHING)
            # ---------------------------------------------------------------

            if decision == VotingDecision.REJECT:
                vr = self._voting.reject_result(score)
                return AnalysisOutcome(
                    pair=self.pair,
                    state=self._sm.state,
                    voting_result=vr,
                    error=vr.reason,
                    elapsed_seconds=time.time() - t0,
                )

            if decision == VotingDecision.SKIP:
                # High-confidence shortcut — no ensemble needed.
                vr = self._voting.quick_result(candidate)
                plan = await self._phase_output(candidate)
                self._last_plan = plan
                return AnalysisOutcome(
                    pair=self.pair,
                    state=self._sm.state,
                    plan=plan,
                    voting_result=vr,
                    elapsed_seconds=time.time() - t0,
                )

            # VotingDecision.VOTE — run ensemble (VOTING_RUNS times).
            vr = await self._phase_vote(candidate)

            if not vr.consensus or vr.setup is None:
                return AnalysisOutcome(
                    pair=self.pair,
                    state=self._sm.state,
                    voting_result=vr,
                    error=vr.reason,
                    elapsed_seconds=time.time() - t0,
                )

            # Successful vote → produce final output.
            plan = await self._phase_output(vr.setup)
            self._last_plan = plan
            return AnalysisOutcome(
                pair=self.pair,
                state=self._sm.state,
                plan=plan,
                voting_result=vr,
                elapsed_seconds=time.time() - t0,
            )

        except (IllegalTransition, ConvictionLockViolation) as exc:
            logger.error("State error: %s", exc)
            return AnalysisOutcome(
                pair=self.pair,
                state=self._sm.state,
                error=str(exc),
                elapsed_seconds=time.time() - t0,
            )
        except Exception as exc:
            logger.exception("Unexpected error in run_scan")
            return AnalysisOutcome(
                pair=self.pair,
                state=self._sm.state,
                error=f"Unexpected: {exc}",
                elapsed_seconds=time.time() - t0,
            )

    # -- State transitions --------------------------------------------------

    def transition_to(
        self,
        target: AnalysisState,
        *,
        score: int = 0,
        confidence: float = 0.0,
        direction: str = "sell",
        strategy_mode: str = "sniper_confluence",
        entry_zone_mid: float = 0.0,
        htf_bias: str = "bearish",
    ) -> None:
        """Convenience wrapper to transition the state machine.

        Builds a ``SetupContext`` from kwargs and delegates to
        ``StateMachine.transition()``.
        """
        ctx = SetupContext(
            pair=self.pair,
            direction=direction,
            strategy_mode=strategy_mode,
            entry_zone_mid=entry_zone_mid,
            score=score,
            confidence=confidence,
            htf_bias=htf_bias,
        )
        self._sm.transition(target, ctx)

    def cancel(self, reason: str) -> None:
        """Cancel current setup with *reason*."""
        self._sm.cancel(reason)

    def reset(self) -> None:
        """Reset back to SCANNING."""
        self._sm.reset()
        self._last_plan = None

    # -- Internal phases ----------------------------------------------------

    async def _phase_analyze(self) -> Optional[SetupCandidate]:
        """Phase 1-6: Collect live data from ALL tools, then send to Gemini.

        Instead of relying on Gemini auto-function-calling (which is
        stripped in structured-output mode), we run every Python tool
        locally and inject the results into the prompt.  This guarantees
        Gemini receives real, deterministic market data.
        """
        from agent.context_builder import collect_multi_tf_async, format_context

        t_phase = time.time()
        state = self._sm.state.value
        logger.info("Phase ANALYZE  pair=%s  state=%s", self.pair, state)

        # 1. Collect live tool data across timeframes (async — FIX §7.1)
        logger.info("Collecting tool data: %s %s …",
                    self.pair, self._analysis_timeframes)
        analyses = await collect_multi_tf_async(
            self.pair, self._analysis_timeframes,
        )
        self._last_analyses = analyses

        context_str = format_context(self.pair, analyses)
        self._last_context = context_str
        logger.info("Context built: %d chars, %d timeframes",
                    len(context_str), len(analyses))

        # 2. Ask Gemini to reason about the data → SetupCandidate
        prompt = (
            f"{context_str}\n\n"
            f"Based on the LIVE MARKET DATA above, analyze {self.pair} "
            f"for SMC trading setups.\n"
            f"Current orchestrator state: {state}.\n\n"
            f"Instructions:\n"
            f"- Determine HTF bias from H1 structure (BOS/CHoCH trend).\n"
            f"- Identify the best entry zone from SnD zones or Order Blocks.\n"
            f"- Check trendline confluence (ray support/resistance).\n"
            f"- Check liquidity sweep events.\n"
            f"- Apply scoring weights (Section 4 of your system prompt).\n"
            f"- If confluence_score ≥ {MIN_SCORE_FOR_TRADE}, return a full "
            f"SetupCandidate.\n"
            f"- If no valid setup exists, return a SetupCandidate with "
            f"confluence_score=0 and rationale explaining why."
        )

        try:
            resp = await self._client.agenerate_structured(
                state=state,
                contents=prompt,
                schema=SetupCandidate,
            )
            text = resp.text
            if not text or text.strip() in ("null", "{}"):
                return None
            candidate = SetupCandidate.model_validate_json(text)
            if candidate.confluence_score == 0:
                logger.info("Gemini returned score=0 → no valid setup")
                return None

            # --- FIX F3-01/F3-02: Local score verification -----------------
            # Gemini may hallucinate the confluence_score.  Re-calculate
            # locally using the deterministic scorer and override.
            # Extract boolean flags from the real tool outputs.
            from tools.scorer import score_setup_candidate

            score_flags = self._extract_score_flags(candidate, analyses)
            local_result = score_setup_candidate(**score_flags)
            local_score = local_result["score"]
            if local_score != candidate.confluence_score:
                logger.warning(
                    "Score override %s: Gemini=%d → local=%d",
                    self.pair, candidate.confluence_score, local_score,
                )
                candidate.confluence_score = local_score
            if candidate.confluence_score == 0:
                logger.info("Local scorer returned 0 → no valid setup")
                return None
            # ---------------------------------------------------------------

            return candidate
        except BudgetExceededError:
            raise  # FIX H-07: Let budget errors propagate — caller must handle
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Phase ANALYZE parse error for %s: %s (response text: %.200s)",
                self.pair, exc, text if 'text' in dir() else '<no response>',
            )
            return None
        except Exception as exc:
            logger.warning("Phase ANALYZE failed for %s: %s", self.pair, exc)
            return None
        finally:
            # FIX M-07: Track per-pair phase timing
            elapsed = time.time() - t_phase
            self._phase_timings["analyze"] = elapsed
            logger.info("Phase ANALYZE %s completed in %.1fs", self.pair, elapsed)

    # -- FIX F3-01/F3-02 helper: extract score flags from tool data ---------

    def _extract_score_flags(
        self,
        candidate: SetupCandidate,
        analyses: dict,
    ) -> dict[str, bool]:
        """Derive scorer boolean flags from real tool outputs.

        Uses the H1 timeframe data (primary) to determine each flag.
        This is a deterministic, non-hallucinatable calculation.

        FIX L-07: Each flag is computed defensively — a failure in one
        flag doesn't prevent the others from being calculated.
        """
        h1 = analyses.get("H1", {})
        m15 = analyses.get("M15", {})
        primary = h1 if h1 and "error" not in h1 else m15

        direction = (
            candidate.direction.value
            if hasattr(candidate.direction, "value")
            else candidate.direction
        )

        # Default flags (conservative: assume false/no penalty)
        flags = {
            "htf_alignment": False,
            "fresh_zone": False,
            "sweep_detected": False,
            "near_major_snr": False,
            "pa_confirmed": False,
            "ema_filter_ok": False,
            "rsi_filter_ok": True,   # default: no penalty
            "sl_too_tight": False,
            "sl_too_wide": False,
            "counter_htf_bias": True,
            "zone_mitigated": True,
        }

        try:
            # HTF alignment: structure trend matches trade direction
            trend = primary.get("structure", {}).get("trend", "")
            flags["htf_alignment"] = (
                (direction == "buy" and trend == "bullish")
                or (direction == "sell" and trend == "bearish")
            )
            flags["counter_htf_bias"] = not flags["htf_alignment"]
        except Exception as exc:
            logger.debug("Score flag htf_alignment failed: %s", exc)

        try:
            # Fresh zone: at least one un-mitigated supply/demand zone exists
            supply_zones = primary.get("supply_zones", [])
            demand_zones = primary.get("demand_zones", [])
            zones = demand_zones if direction == "buy" else supply_zones
            flags["fresh_zone"] = any(not z.get("mitigated", True) for z in zones)
            flags["zone_mitigated"] = not flags["fresh_zone"]
        except Exception as exc:
            logger.debug("Score flag fresh_zone failed: %s", exc)

        try:
            # Sweep detected
            sweep_events = primary.get("sweep_events", [])
            flags["sweep_detected"] = len(sweep_events) > 0
        except Exception as exc:
            logger.debug("Score flag sweep_detected failed: %s", exc)

        entry_mid = (candidate.entry_zone_low + candidate.entry_zone_high) / 2
        atr_raw = primary.get("atr", None)
        atr_val = atr_raw.get("current", 1.0) if isinstance(atr_raw, dict) else 1.0

        try:
            # Near major SNR
            snr_levels = primary.get("snr_levels", [])
            flags["near_major_snr"] = any(
                abs(lv.get("price", 0) - entry_mid) < 1.5 * atr_val
                for lv in snr_levels
            )
        except Exception as exc:
            logger.debug("Score flag near_major_snr failed: %s", exc)

        try:
            # PA confirmed: pin bar or engulfing in last 5 bars
            pin_bars = primary.get("pin_bars", [])
            engulfing = primary.get("engulfing_patterns", [])
            n_candles = primary.get("candle_count", 0)
            flags["pa_confirmed"] = any(
                p.get("index", 0) >= n_candles - 5 for p in pin_bars
            ) or any(
                e.get("index", 0) >= n_candles - 5 for e in engulfing
            )
        except Exception as exc:
            logger.debug("Score flag pa_confirmed failed: %s", exc)

        try:
            # EMA filter: price on correct side of EMA50
            last_close = primary.get("last_close", 0)
            ema50_val = primary.get("ema50", {}).get("current", 0)
            if direction == "buy":
                flags["ema_filter_ok"] = last_close >= ema50_val
            else:
                flags["ema_filter_ok"] = last_close <= ema50_val
        except Exception as exc:
            logger.debug("Score flag ema_filter_ok failed: %s", exc)

        try:
            # RSI filter: RSI supports direction (not overbought for buy, etc.)
            rsi_val = primary.get("rsi14", {}).get("current", 50)
            flags["rsi_filter_ok"] = (
                (direction == "buy" and rsi_val < 70)
                or (direction == "sell" and rsi_val > 30)
            )
        except Exception as exc:
            logger.debug("Score flag rsi_filter_ok failed: %s", exc)

        try:
            # SL distance penalties
            sl_dist = abs(entry_mid - candidate.stop_loss)
            flags["sl_too_tight"] = (atr_val > 0 and sl_dist < 0.5 * atr_val)
            flags["sl_too_wide"] = (atr_val > 0 and sl_dist > 2.5 * atr_val)
        except Exception as exc:
            logger.debug("Score flag sl_distance failed: %s", exc)

        return flags

    async def _phase_vote(
        self,
        initial: SetupCandidate,
    ) -> VotingResult:
        """Phase 9: Ensemble voting with VOTING_RUNS (default 3) runs.

        The first run is *initial* (already obtained).  We run
        ``VOTING_RUNS - 1`` additional runs with slight thinking-level
        variation to sample diverse outputs, all using the *same*
        market-data context so votes are data-consistent.
        """
        t_phase = time.time()
        candidates: list[SetupCandidate] = [initial]
        # FIX L-21: Meaningful voter names for logging/debugging
        # Each voting run uses a distinct thinking level for diversity.
        voter_profiles = [
            {"name": "conservative", "thinking": "high"},
            {"name": "aggressive",   "thinking": "low"},
            {"name": "balanced",     "thinking": "high"},
        ]

        # Reuse the same context so every vote sees identical data.
        ctx = self._last_context or ""

        for i in range(VOTING_RUNS - 1):
            profile = voter_profiles[i % len(voter_profiles)]
            voter_name = profile["name"]
            level = profile["thinking"]
            try:
                resp = await self._client.agenerate_structured(
                    state="TRIGGERED",
                    contents=(
                        f"{ctx}\n\n"
                        f"Re-analyze {self.pair} for trading setups.  "
                        f"Voting run {i + 2}/{VOTING_RUNS} "
                        f"(voter: {voter_name}).  "
                        f"Provide your independent SetupCandidate."
                    ),
                    schema=SetupCandidate,
                    thinking_level=level,
                )
                text = resp.text
                if text and text.strip() not in ("null", "{}"):
                    candidates.append(
                        SetupCandidate.model_validate_json(text)
                    )
            except BudgetExceededError:
                logger.warning("Voting run %d (%s) blocked: budget exceeded",
                              i + 2, voter_name)
                break  # Stop further voting runs if budget exhausted
            except Exception as exc:
                logger.warning("Voting run %d (%s) failed: %s",
                              i + 2, voter_name, exc)

        # Compute ATR tolerance — use entry zone width as proxy if needed.
        atr_proxy = abs(initial.entry_zone_high - initial.entry_zone_low) / 0.3
        atr_proxy = max(atr_proxy, 1.0)

        # FIX M-07: Track per-pair phase timing
        elapsed = time.time() - t_phase
        self._phase_timings["vote"] = elapsed
        logger.info("Phase VOTE %s completed in %.1fs (%d candidates)",
                    self.pair, elapsed, len(candidates))

        return self._voting.vote(candidates, atr=atr_proxy)

    async def _phase_output(
        self,
        candidate: SetupCandidate,
    ) -> TradingPlan:
        """Phase 10: Generate final TradingPlan via structured output.

        Wraps the winning candidate into a full ``TradingPlan`` using
        Gemini's structured-output mode.  Falls back to local construction
        if the API call fails.
        """
        state = self._sm.state.value
        ctx = self._last_context or ""
        prompt = (
            f"{ctx}\n\n"
            f"Produce a complete TradingPlan for {self.pair} based on "
            f"this setup:\n{candidate.model_dump_json()}\n"
            f"Include htf_bias reasoning based on the H1 structure data, "
            f"risk warnings from any zone/liquidity concerns, and DXY note "
            f"if applicable."
        )
        logger.info("Phase OUTPUT  pair=%s", self.pair)

        try:
            resp = await self._client.agenerate_structured(
                state=state,
                contents=prompt,
                schema=TradingPlan,
            )
            text = resp.text
            if text:
                plan = TradingPlan.model_validate_json(text)
                self._enrich_recommended_entry(plan)
                return plan
        except BudgetExceededError:
            raise  # Let caller handle budget exhaustion
        except Exception as exc:
            logger.warning("Phase OUTPUT API failed, building locally: %s", exc)

        # Fallback: construct plan from candidate directly.
        from datetime import datetime, timezone

        plan = TradingPlan(
            pair=self.pair,
            analysis_time=datetime.now(timezone.utc).isoformat(),
            htf_bias="bearish" if candidate.direction == "sell" else "bullish",
            htf_bias_reasoning="Derived from setup direction",
            strategy_mode=candidate.strategy_mode,
            primary_setup=candidate,
            confidence=MIN_CONFIDENCE,
            valid_until=(
                datetime.now(timezone.utc).isoformat()
            ),
        )
        self._enrich_recommended_entry(plan)
        return plan

    @staticmethod
    def _enrich_recommended_entry(plan: TradingPlan) -> None:
        """Populate recommended_entry on primary & alternative setups."""
        from agent.pending_manager import compute_recommended_entry
        for setup in [plan.primary_setup, plan.alternative_setup]:
            if setup and setup.recommended_entry is None:
                direction = setup.direction.value if hasattr(setup.direction, "value") else setup.direction
                setup.recommended_entry = compute_recommended_entry(
                    direction, setup.entry_zone_low, setup.entry_zone_high,
                )
