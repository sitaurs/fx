"""
tests/test_voting.py — TDD tests for agent/voting.py (Smart Voting Engine).

Written BEFORE implementation per user requirement.

Masterplan references:
  - Section 2.1: Smart Voting (conditional: score>=9 skip, 5-8 vote, <5 reject)
  - Section 8:   Voting / Ensemble System
    • Cluster by direction + entry_zone (±0.3×ATR)
    • Majority vote = largest cluster; confidence = cluster_size / total_runs
    • Merge: entry=median, SL=most_conservative, TP=median, score=avg
  - Settings: VOTING_THRESHOLD_HIGH=9, VOTING_THRESHOLD_LOW=5, VOTING_RUNS=3
"""

from __future__ import annotations

import pytest

from schemas.plan import SetupCandidate
from schemas.market_data import Direction, StrategyMode
from agent.voting import (
    VotingDecision,
    VotingResult,
    VotingEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate(
    *,
    direction: str = "sell",
    entry_low: float = 2348.0,
    entry_high: float = 2352.0,
    stop_loss: float = 2360.0,
    take_profit_1: float = 2330.0,
    take_profit_2: float | None = 2310.0,
    score: int = 8,
    rr: float = 2.0,
) -> SetupCandidate:
    """Create a minimal SetupCandidate for testing."""
    return SetupCandidate(
        direction=Direction(direction),
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        entry_zone_low=entry_low,
        entry_zone_high=entry_high,
        trigger_condition="sweep + reclaim",
        stop_loss=stop_loss,
        sl_reasoning="above recent swing high",
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        tp_reasoning="demand zone below",
        risk_reward_ratio=rr,
        management="SL+ at 1R, partial TP at TP1",
        ttl_hours=4.0,
        invalidation="H4 close above 2365",
        confluence_score=score,
        rationale="Strong supply + sweep + bearish structure",
    )


# ===========================================================================
# 1. Voting decision strategy
# ===========================================================================

class TestVotingDecision:
    """decide() classifies initial score into skip / vote / reject."""

    def test_score_9_skip(self):
        engine = VotingEngine()
        result = engine.decide(score=9)
        assert result == VotingDecision.SKIP

    def test_score_10_skip(self):
        engine = VotingEngine()
        assert engine.decide(score=10) == VotingDecision.SKIP

    def test_score_15_skip(self):
        engine = VotingEngine()
        assert engine.decide(score=15) == VotingDecision.SKIP

    def test_score_8_vote(self):
        engine = VotingEngine()
        assert engine.decide(score=8) == VotingDecision.VOTE

    def test_score_5_vote(self):
        engine = VotingEngine()
        assert engine.decide(score=5) == VotingDecision.VOTE

    def test_score_4_reject(self):
        engine = VotingEngine()
        assert engine.decide(score=4) == VotingDecision.REJECT

    def test_score_0_reject(self):
        engine = VotingEngine()
        assert engine.decide(score=0) == VotingDecision.REJECT


# ===========================================================================
# 2. Clustering logic
# ===========================================================================

class TestClustering:
    """cluster() groups candidates by direction + entry zone (±0.3×ATR)."""

    def test_same_direction_same_zone_one_cluster(self):
        """3 sells with entries within ±0.3*ATR=3.0 → 1 cluster."""
        engine = VotingEngine()
        atr = 10.0  # tolerance = 0.3 * 10 = 3.0
        c1 = _candidate(entry_low=2348, entry_high=2352)  # mid=2350
        c2 = _candidate(entry_low=2349, entry_high=2353)  # mid=2351
        c3 = _candidate(entry_low=2347, entry_high=2351)  # mid=2349
        clusters = engine.cluster([c1, c2, c3], atr=atr)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_same_direction_far_zones_two_clusters(self):
        """Sells with entries far apart → 2 clusters."""
        engine = VotingEngine()
        atr = 10.0  # tolerance = 3.0
        c1 = _candidate(entry_low=2348, entry_high=2352)  # mid=2350
        c2 = _candidate(entry_low=2349, entry_high=2353)  # mid=2351
        c3 = _candidate(entry_low=2368, entry_high=2372)  # mid=2370 → far!
        clusters = engine.cluster([c1, c2, c3], atr=atr)
        assert len(clusters) == 2

    def test_different_directions_separate_clusters(self):
        """Buy and Sell always in separate clusters."""
        engine = VotingEngine()
        atr = 10.0
        c1 = _candidate(direction="sell", entry_low=2348, entry_high=2352)
        c2 = _candidate(direction="buy",  entry_low=2348, entry_high=2352)
        clusters = engine.cluster([c1, c2], atr=atr)
        assert len(clusters) == 2

    def test_single_candidate_one_cluster(self):
        engine = VotingEngine()
        c1 = _candidate()
        clusters = engine.cluster([c1], atr=10.0)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_empty_input_no_clusters(self):
        engine = VotingEngine()
        clusters = engine.cluster([], atr=10.0)
        assert clusters == []


# ===========================================================================
# 3. Majority vote (select winning cluster)
# ===========================================================================

class TestMajorityVote:
    """vote() picks the largest cluster and computes confidence."""

    def test_3_of_3_agreement(self):
        """Perfect consensus → confidence = 1.0, consensus=True."""
        engine = VotingEngine()
        atr = 10.0
        candidates = [_candidate(score=8) for _ in range(3)]
        result = engine.vote(candidates, atr=atr)
        assert result.consensus is True
        assert result.confidence >= 0.8
        assert result.cluster_size == 3
        assert result.total_runs == 3
        assert result.setup is not None

    def test_2_of_3_majority(self):
        """2 sell, 1 buy → sell wins, confidence = 2/3 ≈ 0.67."""
        engine = VotingEngine()
        atr = 10.0
        c1 = _candidate(direction="sell")
        c2 = _candidate(direction="sell")
        c3 = _candidate(direction="buy", entry_low=2320, entry_high=2324,
                         stop_loss=2310, take_profit_1=2340)
        result = engine.vote([c1, c2, c3], atr=atr)
        assert result.setup is not None
        assert result.setup.direction == Direction.SELL
        assert result.cluster_size == 2
        assert result.confidence == pytest.approx(2 / 3, abs=0.01)

    def test_split_vote_no_trade(self):
        """No clear majority → no trade."""
        engine = VotingEngine()
        atr = 10.0
        # 1 sell zone A, 1 sell zone B (far apart), 1 buy
        c1 = _candidate(direction="sell", entry_low=2348, entry_high=2352)
        c2 = _candidate(direction="sell", entry_low=2398, entry_high=2402)
        c3 = _candidate(direction="buy",  entry_low=2320, entry_high=2324,
                         stop_loss=2310, take_profit_1=2340)
        result = engine.vote([c1, c2, c3], atr=atr)
        # All clusters size=1, no majority → no consensus
        assert result.consensus is False
        assert result.setup is None

    def test_confidence_threshold(self):
        """Confidence must be >= MIN_CONFIDENCE (0.6) for consensus."""
        engine = VotingEngine()
        atr = 10.0
        # 5 runs: 2 agree, 3 all different → largest=2, conf=2/5=0.4 < 0.6
        c1 = _candidate(direction="sell", entry_low=2348, entry_high=2352)
        c2 = _candidate(direction="sell", entry_low=2349, entry_high=2353)
        c3 = _candidate(direction="buy",  entry_low=2320, entry_high=2324,
                         stop_loss=2310, take_profit_1=2340)
        c4 = _candidate(direction="sell", entry_low=2400, entry_high=2404)
        c5 = _candidate(direction="buy",  entry_low=2280, entry_high=2284,
                         stop_loss=2270, take_profit_1=2300)
        result = engine.vote([c1, c2, c3, c4, c5], atr=atr)
        assert result.confidence < 0.6
        assert result.consensus is False


# ===========================================================================
# 4. Merge logic (winning cluster → single candidate)
# ===========================================================================

class TestMergeCluster:
    """merge() combines a winning cluster into one candidate."""

    def test_entry_is_median(self):
        """Merged entry_zone_low/high should be median of cluster."""
        engine = VotingEngine()
        c1 = _candidate(entry_low=2348, entry_high=2352)  # mid=2350
        c2 = _candidate(entry_low=2346, entry_high=2350)  # mid=2348
        c3 = _candidate(entry_low=2350, entry_high=2354)  # mid=2352
        merged = engine.merge([c1, c2, c3])
        # Median of mids: sorted=[2348, 2350, 2352] → 2350
        mid = (merged.entry_zone_low + merged.entry_zone_high) / 2
        assert mid == pytest.approx(2350.0, abs=0.5)

    def test_sl_most_conservative_sell(self):
        """For sells, most conservative SL = highest value (farthest above)."""
        engine = VotingEngine()
        c1 = _candidate(direction="sell", stop_loss=2360.0)
        c2 = _candidate(direction="sell", stop_loss=2365.0)
        c3 = _candidate(direction="sell", stop_loss=2358.0)
        merged = engine.merge([c1, c2, c3])
        assert merged.stop_loss == 2365.0  # highest = most conservative

    def test_sl_most_conservative_buy(self):
        """For buys, most conservative SL = lowest value (farthest below)."""
        engine = VotingEngine()
        c1 = _candidate(direction="buy", stop_loss=2330.0,
                         entry_low=2348, entry_high=2352,
                         take_profit_1=2370)
        c2 = _candidate(direction="buy", stop_loss=2325.0,
                         entry_low=2348, entry_high=2352,
                         take_profit_1=2370)
        c3 = _candidate(direction="buy", stop_loss=2335.0,
                         entry_low=2348, entry_high=2352,
                         take_profit_1=2370)
        merged = engine.merge([c1, c2, c3])
        assert merged.stop_loss == 2325.0  # lowest = most conservative

    def test_tp_is_median(self):
        """TP1 should be median of cluster."""
        engine = VotingEngine()
        c1 = _candidate(take_profit_1=2330.0)
        c2 = _candidate(take_profit_1=2325.0)
        c3 = _candidate(take_profit_1=2335.0)
        merged = engine.merge([c1, c2, c3])
        assert merged.take_profit_1 == pytest.approx(2330.0, abs=0.5)

    def test_score_is_average(self):
        """Merged score = average of cluster scores (rounded)."""
        engine = VotingEngine()
        c1 = _candidate(score=7)
        c2 = _candidate(score=8)
        c3 = _candidate(score=9)
        merged = engine.merge([c1, c2, c3])
        assert merged.confluence_score == 8  # avg=8.0

    def test_score_rounds_down(self):
        """Average score rounds to nearest int."""
        engine = VotingEngine()
        c1 = _candidate(score=7)
        c2 = _candidate(score=7)
        c3 = _candidate(score=8)
        merged = engine.merge([c1, c2, c3])
        # avg = 7.33 → rounds to 7
        assert merged.confluence_score == 7

    def test_merge_single_candidate(self):
        """Merging a single candidate returns it as-is."""
        engine = VotingEngine()
        c1 = _candidate(score=9, stop_loss=2360.0)
        merged = engine.merge([c1])
        assert merged.confluence_score == 9
        assert merged.stop_loss == 2360.0

    def test_tp2_is_median_when_present(self):
        """TP2 median taken from candidates that have it."""
        engine = VotingEngine()
        c1 = _candidate(take_profit_2=2310.0)
        c2 = _candidate(take_profit_2=2305.0)
        c3 = _candidate(take_profit_2=2315.0)
        merged = engine.merge([c1, c2, c3])
        assert merged.take_profit_2 == pytest.approx(2310.0, abs=0.5)

    def test_tp2_none_when_all_none(self):
        """If all candidates have no TP2, merged TP2 is None."""
        engine = VotingEngine()
        c1 = _candidate(take_profit_2=None)
        c2 = _candidate(take_profit_2=None)
        c3 = _candidate(take_profit_2=None)
        merged = engine.merge([c1, c2, c3])
        assert merged.take_profit_2 is None


# ===========================================================================
# 5. VotingResult data integrity
# ===========================================================================

class TestVotingResultIntegrity:
    """VotingResult fields must be correctly populated."""

    def test_skip_result(self):
        """When score >= 9, quick_result wraps the single candidate."""
        engine = VotingEngine()
        c = _candidate(score=10)
        result = engine.quick_result(c)
        assert result.setup is c
        assert result.confidence == pytest.approx(0.9, abs=0.05)
        assert result.consensus is True
        assert result.cluster_size == 1
        assert result.total_runs == 1
        assert "skip" in result.reason.lower() or "high" in result.reason.lower()

    def test_reject_result(self):
        """When score < 5, reject_result returns None setup."""
        engine = VotingEngine()
        result = engine.reject_result(score=3)
        assert result.setup is None
        assert result.confidence == 0.0
        assert result.consensus is False
        assert "reject" in result.reason.lower() or "low" in result.reason.lower()

    def test_vote_result_has_reason(self):
        """vote() always returns a result with a non-empty reason."""
        engine = VotingEngine()
        candidates = [_candidate(score=7) for _ in range(3)]
        result = engine.vote(candidates, atr=10.0)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0
