"""
tools/snr.py — Support/Resistance level detection via multi-TF swing clustering.

Algorithm (masterplan 6.3):
    - Input: all swing prices from multiple timeframes.
    - Cluster: swings within <= cluster_mult × ATR → same SNR level.
    - Score: touches × recency × TF_weight.
    - Major: high score + appears on H4/H1.
    - Minor: low score / only M30/M15.

Reference: masterplan.md §6.3
"""

from __future__ import annotations

import math

from config.settings import SNR_CLUSTER_ATR_MULT, SNR_MIN_TOUCHES, SNR_CLUSTER_PAIR_MULT
from config.strategy_rules import TF_WEIGHT


def detect_snr_levels(
    swings: list[dict],
    atr_value: float,
    cluster_atr_mult: float | None = None,
    min_touches: int = 1,
    pair: str = "",
) -> dict:
    """Detect SNR levels by clustering swing points.

    Args:
        swings: List of swing dicts with keys: price, index, time, type, timeframe.
        atr_value: Current ATR value for distance calculation.
        cluster_atr_mult: Max distance (ATR multiple) to merge swings.
                          If None, uses pair-adaptive value from
                          ``SNR_CLUSTER_PAIR_MULT`` config or fallback to
                          ``SNR_CLUSTER_ATR_MULT`` (0.2).  (FP-10 M-20)
        min_touches: Minimum touches to include a level (default 1).
        pair: Trading pair for pair-adaptive clustering (e.g. "XAUUSD").
              XAUUSD needs wider tolerance due to larger price swings.

    Returns:
        Dict with key:
            levels: list[dict] — sorted by score descending.
            Each level has: price, touches, score, is_major, source_tf,
                            recency_score, tf_weight.
    """
    if not swings or atr_value <= 0 or math.isnan(atr_value):
        return {"levels": []}

    # FP-10 M-20: pair-adaptive cluster multiplier
    if cluster_atr_mult is None:
        cluster_atr_mult = SNR_CLUSTER_PAIR_MULT.get(pair, SNR_CLUSTER_ATR_MULT)

    cluster_dist = cluster_atr_mult * atr_value

    # Sort swings by price for greedy clustering
    sorted_swings = sorted(swings, key=lambda s: s["price"])

    clusters: list[list[dict]] = []
    current_cluster: list[dict] = [sorted_swings[0]]

    for sw in sorted_swings[1:]:
        # FIX F2-07: Compare to ANCHOR price (first element), not drifting mean.
        # Mean-based comparison causes order-dependent cluster drift.
        anchor_price = current_cluster[0]["price"]
        if abs(sw["price"] - anchor_price) <= cluster_dist:
            current_cluster.append(sw)
        else:
            clusters.append(current_cluster)
            current_cluster = [sw]

    clusters.append(current_cluster)

    # Find max index across all swings for recency calculation
    max_idx = max(s["index"] for s in swings) if swings else 1

    # Convert clusters to SNR levels
    levels: list[dict] = []
    for cluster in clusters:
        touches = len(cluster)
        if touches < min_touches:
            continue

        avg_price = sum(s["price"] for s in cluster) / touches

        # TF weights
        tfs = [s.get("timeframe", "M15") for s in cluster]
        tf_weights = [TF_WEIGHT.get(tf, 0.5) for tf in tfs]
        max_tf_weight = max(tf_weights)

        # Recency: average index normalized to [0, 1]
        avg_idx = sum(s["index"] for s in cluster) / touches
        recency = avg_idx / max_idx if max_idx > 0 else 1.0

        # Is major? Must have HTF (H4 or H1) contribution
        htf_tfs = {"H4", "H1", "D1", "W1"}
        has_htf = any(tf in htf_tfs for tf in tfs)
        is_major = has_htf and touches >= SNR_MIN_TOUCHES

        # Dominant timeframe — use highest hierarchical TF (FP-10 M-20)
        # A level with 5×M15 + 1×H4 should show source_tf="H4" since
        # the H4 touch confirms it as a higher-timeframe level.
        source_tf = max(tfs, key=lambda tf: TF_WEIGHT.get(tf, 0.5))

        # Score formula: touches × recency × tf_weight
        score = touches * (0.3 + 0.7 * recency) * max_tf_weight

        levels.append({
            "price": round(avg_price, 5),
            "touches": touches,
            "recency_score": round(recency, 3),
            "tf_weight": round(max_tf_weight, 3),
            "is_major": is_major,
            "source_tf": source_tf,
            "score": round(score, 3),
        })

    # Sort by score descending
    levels.sort(key=lambda l: l["score"], reverse=True)

    return {"levels": levels}
