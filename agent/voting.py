"""
agent/voting.py — Smart Voting Engine (Anti-Hallucination).

Implements conditional voting from masterplan §2.1 + §8:
  - score >= 9  → SKIP voting, publish directly (confidence 0.9)
  - score 5-8   → run 3× ensemble, cluster + majority vote
  - score < 5   → REJECT immediately

Clustering groups candidates by (direction, entry_zone ±0.3×ATR).
Merge: entry=median, SL=most_conservative, TP=median, score=avg.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from schemas.plan import SetupCandidate
from schemas.market_data import Direction
from config.settings import (
    VOTING_THRESHOLD_HIGH,
    VOTING_THRESHOLD_LOW,
    MIN_CONFIDENCE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class VotingDecision(str, Enum):
    """Result of decide() — what to do with the initial score."""
    SKIP = "skip"       # score >= threshold_high → publish immediately
    VOTE = "vote"       # score in [low, high) → ensemble voting
    REJECT = "reject"   # score < threshold_low → no trade


@dataclass
class VotingResult:
    """Outcome of the voting / ensemble process.

    Field naming convention (FIX CON-10):
      - ``setup`` is a ``SetupCandidate`` (not a TradingPlan).  The
        orchestrator wraps it into a TradingPlan in _phase_output.
      - ``confidence`` is cluster_size / total_runs (0.0–1.0).
      - ``consensus`` is True when confidence >= MIN_CONFIDENCE.
      - ``reason`` is a human-readable explanation (maps to
        AnalysisOutcome.error on the caller side when consensus=False).
    """
    setup: Optional[SetupCandidate]
    confidence: float          # 0.0–1.0
    consensus: bool            # True if confidence >= MIN_CONFIDENCE
    cluster_size: int          # size of winning cluster
    total_runs: int            # how many candidates were evaluated
    reason: str                # human-readable explanation


# ---------------------------------------------------------------------------
# Voting Engine
# ---------------------------------------------------------------------------

class VotingEngine:
    """Stateless voting logic — call methods directly."""

    # -- Decision strategy --------------------------------------------------

    @staticmethod
    def decide(score: int) -> VotingDecision:
        """Classify *score* into skip / vote / reject.

        Uses VOTING_THRESHOLD_HIGH (default 9) and
        VOTING_THRESHOLD_LOW (default 5) from settings.
        """
        if score >= VOTING_THRESHOLD_HIGH:
            return VotingDecision.SKIP
        if score >= VOTING_THRESHOLD_LOW:
            return VotingDecision.VOTE
        return VotingDecision.REJECT

    # -- Clustering ---------------------------------------------------------

    @staticmethod
    def cluster(
        candidates: list[SetupCandidate],
        atr: float,
    ) -> list[list[SetupCandidate]]:
        """Group candidates by (direction, entry_zone midpoint ±0.3×ATR).

        Returns a list of clusters, each cluster being a list of candidates.
        """
        if not candidates:
            return []

        tolerance = 0.3 * atr
        clusters: list[list[SetupCandidate]] = []

        for cand in candidates:
            mid = (cand.entry_zone_low + cand.entry_zone_high) / 2
            placed = False
            for cluster in clusters:
                ref = cluster[0]
                ref_mid = (ref.entry_zone_low + ref.entry_zone_high) / 2
                # Must match direction AND entry zone within tolerance
                if cand.direction == ref.direction and abs(mid - ref_mid) <= tolerance:
                    cluster.append(cand)
                    placed = True
                    break
            if not placed:
                clusters.append([cand])

        return clusters

    # -- Majority vote ------------------------------------------------------

    def vote(
        self,
        candidates: list[SetupCandidate],
        atr: float,
    ) -> VotingResult:
        """Run majority vote on *candidates*.

        1. Cluster by direction + entry zone.
        2. Pick the largest cluster.
        3. Compute confidence = cluster_size / total_runs.
        4. If confidence >= MIN_CONFIDENCE → consensus, merge cluster.

        FIX M-12 — Threshold rationale:
        MIN_CONFIDENCE = 0.6 with VOTING_RUNS = 3 means 2/3 agreement
        (67%) is required for consensus.  This is slightly above the
        60% floor to account for Gemini stochasticity while avoiding
        false consensus from a single agreeing run.  With 5 runs, 3/5
        (60%) would pass — the threshold is intentionally set at 60%
        rather than a strict majority (>50%) to balance:
          • Avoiding false negatives (too strict → never trades)
          • Preventing hallucination pass-through (too loose → bad trades)
        The 60% floor was chosen per masterplan §8 as a pragmatic
        compromise; raise to 0.7+ for more conservative trading.
        """
        total = len(candidates)
        if total == 0:
            return VotingResult(
                setup=None,
                confidence=0.0,
                consensus=False,
                cluster_size=0,
                total_runs=0,
                reason="No candidates to vote on",
            )

        clusters = self.cluster(candidates, atr)

        # Sort by cluster size descending
        clusters.sort(key=len, reverse=True)
        winner = clusters[0]
        confidence = len(winner) / total

        if confidence >= MIN_CONFIDENCE:
            merged = self.merge(winner)
            return VotingResult(
                setup=merged,
                confidence=confidence,
                consensus=True,
                cluster_size=len(winner),
                total_runs=total,
                reason=(
                    f"Majority vote: {len(winner)}/{total} agree on "
                    f"{winner[0].direction.value} "
                    f"(confidence {confidence:.2f})"
                ),
            )

        return VotingResult(
            setup=None,
            confidence=confidence,
            consensus=False,
            cluster_size=len(winner),
            total_runs=total,
            reason=(
                f"No consensus: largest cluster {len(winner)}/{total} "
                f"(confidence {confidence:.2f} < {MIN_CONFIDENCE})"
            ),
        )

    # -- Merge cluster into single candidate --------------------------------

    @staticmethod
    def merge(cluster: list[SetupCandidate]) -> SetupCandidate:
        """Merge a winning cluster into one SetupCandidate.

        Rules (masterplan §8):
          - entry = median entry zone
          - SL = most conservative (farthest from entry)
          - TP = median TP
          - score = average (rounded)
        """
        if len(cluster) == 1:
            return cluster[0]

        # -- Entry: median of midpoints, median half-width (FIX CON-10) -----
        mids = [(c.entry_zone_low + c.entry_zone_high) / 2 for c in cluster]
        median_mid = statistics.median(mids)
        half_widths = [(c.entry_zone_high - c.entry_zone_low) / 2 for c in cluster]
        half_width = statistics.median(half_widths)
        entry_low = median_mid - half_width
        entry_high = median_mid + half_width

        # -- SL: most conservative -------------------------------------------
        direction = cluster[0].direction
        sls = [c.stop_loss for c in cluster]
        if direction == Direction.SELL:
            # Sell: SL is above entry → highest = most conservative
            sl = max(sls)
        else:
            # Buy: SL is below entry → lowest = most conservative
            sl = min(sls)

        # -- TP1: median ----------------------------------------------------
        tp1s = [c.take_profit_1 for c in cluster]
        tp1 = statistics.median(tp1s)

        # -- TP2: median of non-None, or None if all None -------------------
        tp2_values = [c.take_profit_2 for c in cluster if c.take_profit_2 is not None]
        tp2 = statistics.median(tp2_values) if tp2_values else None

        # -- Score: average (rounded) ----------------------------------------
        scores = [c.confluence_score for c in cluster]
        avg_score = round(statistics.mean(scores))
        avg_score = max(0, min(avg_score, 15))  # clamp for schema constraint

        # -- RR: average ----------------------------------------------------
        rrs = [c.risk_reward_ratio for c in cluster]
        avg_rr = round(statistics.mean(rrs), 2)

        # Use first candidate as template for text fields
        ref = cluster[0]
        return SetupCandidate(
            direction=direction,
            strategy_mode=ref.strategy_mode,
            entry_zone_low=round(entry_low, 5),
            entry_zone_high=round(entry_high, 5),
            trigger_condition=ref.trigger_condition,
            stop_loss=sl,
            sl_reasoning=ref.sl_reasoning,
            take_profit_1=tp1,
            take_profit_2=tp2,
            tp_reasoning=ref.tp_reasoning,
            risk_reward_ratio=avg_rr,
            management=ref.management,
            ttl_hours=ref.ttl_hours,
            invalidation=ref.invalidation,
            confluence_score=avg_score,
            rationale=f"Merged from {len(cluster)} voting runs",
        )

    # -- Convenience wrappers -----------------------------------------------

    @staticmethod
    def quick_result(candidate: SetupCandidate) -> VotingResult:
        """Wrap a single high-score candidate (skip voting path)."""
        return VotingResult(
            setup=candidate,
            confidence=0.9,
            consensus=True,
            cluster_size=1,
            total_runs=1,
            reason="High score — skip voting, publish directly",
        )

    @staticmethod
    def reject_result(score: int) -> VotingResult:
        """Create a rejection result for low-score candidates."""
        return VotingResult(
            setup=None,
            confidence=0.0,
            consensus=False,
            cluster_size=0,
            total_runs=0,
            reason=f"Rejected: score {score} below minimum threshold",
        )
