"""
tools/scorer.py — Setup scoring engine (weighted sum).

Scoring formula (masterplan Section 7):
    + 3  htf_alignment       (H4/H1 bias matches direction)
    + 2  fresh_zone           (SnD/OB not mitigated)
    + 3  sweep_detected       (valid liquidity sweep)
    + 2  near_major_snr       (close to major S/R level)
    + 2  pa_confirmed         (pin bar / engulfing at zone)
    + 1  ema_filter_ok        (price vs EMA50 matches direction)
    + 1  rsi_filter_ok        (RSI supports direction)
    - 2  sl_too_tight         (SL < 0.5 × ATR)
    - 2  sl_too_wide          (SL > 2.5 × ATR)
    - 3  counter_htf_bias     (against H4 trend)
    - 2  zone_mitigated       (zone already used)

MINIMUM: 5 for trade.

Reference: masterplan.md §5.3, §7
"""

from __future__ import annotations

from config.strategy_rules import (
    SCORING_WEIGHTS,
    MAX_POSSIBLE_SCORE,
    MIN_CONFLUENCE_SCORE,
    SCORER_PENALTY_FLAGS,
)


def score_setup_candidate(
    htf_alignment: bool = False,
    fresh_zone: bool = False,
    sweep_detected: bool = False,
    near_major_snr: bool = False,
    pa_confirmed: bool = False,
    ema_filter_ok: bool = False,
    rsi_filter_ok: bool = False,
    sl_too_tight: bool = False,
    sl_too_wide: bool = False,
    counter_htf_bias: bool = False,
    zone_mitigated: bool = False,
) -> dict:
    """Calculate confluence score for a setup candidate.

    Args:
        htf_alignment: H4/H1 bias matches trade direction.
        fresh_zone: Target zone has not been mitigated.
        sweep_detected: Valid liquidity sweep present.
        near_major_snr: Entry near a major S/R level.
        pa_confirmed: Price action confirmation (pin/engulfing).
        ema_filter_ok: Price vs EMA50 supports direction.
        rsi_filter_ok: RSI supports direction.
        sl_too_tight: SL < 0.5 × ATR (penalty).
        sl_too_wide: SL > 2.5 × ATR (penalty).
        counter_htf_bias: Trade goes against H4 bias (penalty).
        zone_mitigated: Target zone already mitigated (penalty).

    Returns:
        Dict with the following structure (FP-10 L-34):

        - **score** (``int``): Final clamped confluence score in
          ``[0, max_possible]``.  See ``SCORING_WEIGHTS`` in
          ``config/strategy_rules.py`` for individual weights.
        - **breakdown** (``dict[str, int]``): Per-flag weight
          contribution.  Keys match the flag names above.  Value is
          the weight from ``SCORING_WEIGHTS`` if active, else ``0``.
          Penalty values are negative when active.
        - **tradeable** (``bool``): ``True`` when ``score >= 5``
          (minimum score for trade per masterplan §7).
        - **max_possible** (``int``): Theoretical maximum score
          (sum of all positive weights, currently 14).
    """
    breakdown: dict[str, int] = {}
    score = 0

    # Positive factors
    factors = {
        "htf_alignment": htf_alignment,
        "fresh_zone": fresh_zone,
        "sweep_detected": sweep_detected,
        "near_major_snr": near_major_snr,
        "pa_confirmed": pa_confirmed,
        "ema_filter_ok": ema_filter_ok,
        "rsi_filter_ok": rsi_filter_ok,
    }

    # Penalties (L-37): Risk-related deductions applied after positive scoring.
    # Each penalty carries a NEGATIVE weight in SCORING_WEIGHTS. Active
    # penalties reduce the total score; the floor is clamped to 0.
    # Penalty thresholds are synced with VALIDATION_RULES in strategy_rules.py:
    #   sl_too_tight  → SL < sl_min_atr_mult (0.5) × ATR
    #   sl_too_wide   → SL > sl_max_atr_mult (2.5) × ATR
    #   counter_htf_bias → direction opposes H4 trend
    #   zone_mitigated   → zone already tested (reduced reliability)
    # See SCORER_PENALTY_FLAGS in config/strategy_rules.py for the
    # canonical list and documentation.
    penalties = {
        "sl_too_tight": sl_too_tight,
        "sl_too_wide": sl_too_wide,
        "counter_htf_bias": counter_htf_bias,
        "zone_mitigated": zone_mitigated,
    }

    for name, active in factors.items():
        weight = SCORING_WEIGHTS.get(name, 0)
        if active:
            breakdown[name] = weight
            score += weight
        else:
            breakdown[name] = 0

    for name, active in penalties.items():
        weight = SCORING_WEIGHTS.get(name, 0)
        if active:
            breakdown[name] = weight  # weight is negative
            score += weight
        else:
            breakdown[name] = 0

    # FIX F0-05: Enforce floor (0) and cap (MAX_POSSIBLE_SCORE)
    # FIX F2-12: Use computed constant, not hardcoded 14
    score = max(0, min(score, MAX_POSSIBLE_SCORE))

    return {
        "score": score,
        "breakdown": breakdown,
        "tradeable": score >= MIN_CONFLUENCE_SCORE,
        "max_possible": MAX_POSSIBLE_SCORE,
    }
