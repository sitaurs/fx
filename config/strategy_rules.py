"""
config/strategy_rules.py — Scoring weights & strategy rules.

Reference: masterplan.md §7 (Scoring & Fusion)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Scoring Weights (masterplan Section 7)
# ---------------------------------------------------------------------------
# L-43: Each key maps to a signed integer weight added to the confluence score.
#
# Positive factors (awarded when condition is met):
#   htf_alignment  (+3)  H4/H1 bias aligned with trade direction
#   fresh_zone     (+2)  Order-block / SnD zone not yet mitigated
#   sweep_detected (+3)  Valid liquidity sweep identified
#   near_major_snr (+2)  Entry near major support/resistance level
#   pa_confirmed   (+2)  Rejection or engulfing candle at zone
#   ema_filter_ok  (+1)  Price position vs EMA-50 confirms direction
#   rsi_filter_ok  (+1)  RSI value supports trade direction
#
# Negative penalties (applied when risk condition is present):
#   sl_too_tight     (-2)  SL closer than sl_min_atr_mult × ATR
#   sl_too_wide      (-2)  SL wider than sl_max_atr_mult × ATR
#   counter_htf_bias (-3)  Trade direction opposes H4 trend
#   zone_mitigated   (-2)  Zone was already tested / used
#
# MAX_POSSIBLE_SCORE = sum(positive weights) = 14
# MIN_POSSIBLE_SCORE = sum(all weights)      = 14 + (-9) = 5
SCORING_WEIGHTS = {
    "htf_alignment": +3,         # H4/H1 bias searah
    "fresh_zone": +2,            # OB/SnD belum mitigated
    "sweep_detected": +3,        # liquidity sweep valid
    "near_major_snr": +2,        # dekat level major
    "pa_confirmed": +2,          # rejection/engulfing di zona
    "ema_filter_ok": +1,         # price vs EMA50
    "rsi_filter_ok": +1,         # RSI mendukung
    # Penalties
    "sl_too_tight": -2,          # SL < 0.5×ATR (rawan sweep)
    "sl_too_wide": -2,           # SL > 2.5×ATR (RR jelek)
    "counter_htf_bias": -3,      # melawan H4
    "zone_mitigated": -2,        # zona sudah dipakai
}

MAX_POSSIBLE_SCORE: int = sum(v for v in SCORING_WEIGHTS.values() if v > 0)
# = 3+2+3+2+2+1+1 = 14

# CON-14: Runtime assertion — verify weights really sum to documented value.
assert MAX_POSSIBLE_SCORE == 14, (
    f"SCORING_WEIGHTS positive sum changed to {MAX_POSSIBLE_SCORE}, "
    "update documentation and MIN_CONFLUENCE_SCORE if intentional"
)

# M-23 / L-08: Minimum score threshold for tradeable setups (masterplan §7).
# Previously hardcoded as `score >= 5` in scorer.py.
MIN_CONFLUENCE_SCORE: int = 5

# M-23: Penalty flag names — separated from positive factors for clarity.
# All penalty weights MUST be negative in SCORING_WEIGHTS.
SCORER_PENALTY_FLAGS: list[str] = [
    "sl_too_tight",       # SL < sl_min_atr_mult × ATR — risk of sweep
    "sl_too_wide",        # SL > sl_max_atr_mult × ATR — poor R:R
    "counter_htf_bias",   # Trade opposes H4/H1 trend bias
    "zone_mitigated",     # Zone already tested / used
]


# ---------------------------------------------------------------------------
# Strategy Mode Selection Rules (masterplan Section 3 — Phase 5)
# ---------------------------------------------------------------------------
# Mode 1: Index Correlation — DXY/JPYX bias + price at zone
# Mode 2: Sniper Confluence — trendline valid + SnD zone
# Mode 3: Scalping Channel  — sideways market + flag/channel pattern

STRATEGY_MODES = {
    "index_correlation": {
        "description": "DXY/JPYX showing strong directional zone "
                       "→ pair entry at SnD/OB with index confirmation",
        "requires": ["dxy_gate_pass", "zone_detected"],
        "sweep_required": True,
        "choch_required": True,
    },
    "sniper_confluence": {
        "description": "Trendline valid + SnD zone confluence, "
                       "wait for sweep+reclaim at intersection",
        "requires": ["trendline_valid", "zone_detected"],
        "sweep_required": True,
        "choch_required": True,
    },
    "scalping_channel": {
        "description": "Market sideways, flag/channel detected, "
                       "entry at channel edge, tight SL, quick TP",
        "requires": ["channel_or_flag"],
        "sweep_required": False,
        "choch_required": False,
    },
}


# ---------------------------------------------------------------------------
# Anti-Rungkad Gate checks (masterplan Phase 6 / Section 6)
# ---------------------------------------------------------------------------
ANTI_RUNGKAD_CHECKS = [
    {
        "id": "liquidity_sweep",
        "description": "Sweep detected + reclaim",
        "mandatory_for": ["index_correlation", "sniper_confluence"],
        "optional_for": ["scalping_channel"],
    },
    {
        "id": "choch_confirmation",
        "description": "ChoCh in LTF (M15/M5) after entering zone",
        "mandatory_for": ["index_correlation", "sniper_confluence"],
        "optional_for": ["scalping_channel"],
    },
    {
        "id": "rsi_divergence",
        "description": "RSI divergence as momentum exhaustion signal",
        "mandatory_for": [],
        "optional_for": ["index_correlation", "sniper_confluence", "scalping_channel"],
    },
    {
        "id": "crash_cancel",
        "description": "Marubozu candle through zone without rejection → CANCEL",
        "mandatory_for": ["index_correlation", "sniper_confluence", "scalping_channel"],
        "optional_for": [],
    },
]


# ---------------------------------------------------------------------------
# Validation hard rules (masterplan Phase 8 / Section 5.1)
# ---------------------------------------------------------------------------
VALIDATION_RULES = {
    "min_rr": 1.5,
    "sl_min_atr_mult": 0.5,    # synced with scorer penalty threshold
    "sl_max_atr_mult": 2.5,    # synced with scorer penalty threshold
    "zone_must_be_fresh": True,
    "must_not_counter_htf": True,
    "max_retry": 3,
}


# ---------------------------------------------------------------------------
# Timeframe weights for SNR scoring (masterplan 6.3)
# ---------------------------------------------------------------------------
TF_WEIGHT = {
    "H4": 4.0,
    "H1": 3.0,
    "M30": 2.0,
    "M15": 1.0,
}
