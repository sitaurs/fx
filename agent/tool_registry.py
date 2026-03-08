"""
agent/tool_registry.py — Register all Python tools for Gemini Function Calling.

Collects every public tool function into a single list that the Gemini SDK
can consume directly via ``GenerateContentConfig(tools=ALL_TOOLS)``.

The SDK auto-converts plain Python functions (with type hints + docstrings)
into Function Declarations — no manual schema boilerplate needed.

Reference: masterplan.md §5.1, §18.1
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Import every public tool function
# ---------------------------------------------------------------------------

# Indicators (ATR, EMA, RSI)
from tools.indicators import compute_atr, compute_ema, compute_rsi

# Swing points
from tools.swing import detect_swing_points

# Supply & Demand zones
from tools.supply_demand import detect_snd_zones

# Market structure (BOS / CHoCH)
from tools.structure import detect_bos_choch

# Support & Resistance levels
from tools.snr import detect_snr_levels

# Liquidity pools & sweeps
from tools.liquidity import detect_eqh_eql, detect_sweep

# Order blocks
from tools.orderblock import detect_orderblocks

# Trendlines
from tools.trendline import detect_trendlines

# Price action patterns (pin bar, engulfing)
from tools.price_action import detect_pin_bar, detect_engulfing

# Setup scorer
from tools.scorer import score_setup_candidate

# Plan validator
from tools.validator import validate_trading_plan

# ChoCh micro filter
from tools.choch_filter import detect_choch_micro

# DXY relevance gate — DISABLED §7.10: no DXY data source available yet.
# Re-enable when a DXY OHLCV feed is wired in.
# from tools.dxy_gate import dxy_relevance_score


# ---------------------------------------------------------------------------
# Master list — order follows the 10-phase analysis flow (masterplan §3)
# ---------------------------------------------------------------------------
ALL_TOOLS: list = [
    # Phase 1-2: Data & Indicators
    compute_atr,
    compute_ema,
    compute_rsi,
    # Phase 3: Swing & Structure
    detect_swing_points,
    detect_bos_choch,
    # Phase 3: Zone detection
    detect_snd_zones,
    detect_snr_levels,
    detect_orderblocks,
    # Phase 3: Liquidity
    detect_eqh_eql,
    detect_sweep,
    # Phase 4: DXY gate — DISABLED (§7.10: no data source)
    # dxy_relevance_score,
    # Phase 5: Trendlines & PA
    detect_trendlines,
    detect_pin_bar,
    detect_engulfing,
    detect_choch_micro,
    # Phase 7-8: Scoring & Validation
    score_setup_candidate,
    validate_trading_plan,
]

TOOL_COUNT: int = len(ALL_TOOLS)  # expected: 16 (dxy_gate disabled §7.10)
