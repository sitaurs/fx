"""
tools/dxy_gate.py — DXY/Index correlation gate.

Algorithm (masterplan 6.10):
    - Compute rolling correlation (48-96 bar) between pair returns and DXY returns.
    - |corr| < 0.2 → "not relevant", skip.
    - |corr| >= 0.2 + DXY at zone + rejection → active confirmation.
    - Volatility spike → reduce weight.

Production status (D-10):
    ENABLED — synthetic DXY computed from 6 OANDA component pairs
    (EUR/USD, USD/JPY, GBP/USD, USD/CAD, USD/SEK, USD/CHF) using the
    official ICE DXY formula.  See data/fetcher.py:fetch_synthetic_dxy().
    ``DXY_GATE_ENABLED`` in config/settings.py controls the feature flag.

Reference: masterplan.md §6.10
"""

from __future__ import annotations

import logging
import math

from config.settings import DXY_GATE_ENABLED, DXY_DEFAULT_WINDOW

logger = logging.getLogger(__name__)

# Symbol naming convention (CON-16):
# Throughout this module and callers, the index is referred to by its
# standard ticker symbol:
#   - "DXY"  for US Dollar Index (ICE)
#   - "JPYX" for a synthetic JPY-cross index
# These names are used in log messages, return dicts, and the
# ``ohlcv_index`` parameter documentation.  Callers must map their
# broker's symbol (e.g. "USD.IDX", "DX-Y.NYB") to "DXY" before
# passing data to this module.

# L-30: Minimum absolute Pearson |r| for the pair-index relationship
# to be considered tradeable.  0.2 is deliberately low — it filters
# only truly uncorrelated pairs while capturing moderate relationships.
# Empirical basis: at window=48 H1 bars (2 trading days), |r| < 0.2
# is statistically indistinguishable from noise at the 95% CI for
# samples of this size.
_DEFAULT_MIN_CORRELATION: float = 0.2


def dxy_relevance_score(
    ohlcv_pair: list[dict],
    ohlcv_index: list[dict],
    window: int | None = None,
    min_correlation: float = _DEFAULT_MIN_CORRELATION,
    adaptive_window: bool = True,
) -> dict:
    """Calculate correlation between a pair and an index (DXY/JPYX).

    Args:
        ohlcv_pair: OHLCV for the trading pair (e.g. EURUSD).
        ohlcv_index: OHLCV for the index (e.g. DXY — see CON-16 note above).
        window: Rolling correlation window in bars.  When *None*, uses
            ``DXY_DEFAULT_WINDOW`` from config (default 48).  When
            *adaptive_window* is True, this value is the base window that
            gets adjusted based on recent volatility (M-18).
        min_correlation: Threshold for relevance (default 0.2, see L-30).
        adaptive_window: When True, adjusts the correlation window based
            on the pair's recent volatility.  Higher volatility → shorter
            window (min 24) to capture regime changes faster; lower
            volatility → longer window (max 96) for more stable signals.

    Returns:
        Dict with: correlation (float), relevant (bool), direction (str),
                   window_used (int), enabled (bool).

    Note:
        When ``DXY_GATE_ENABLED`` is False (M-19), returns a neutral
        result with ``enabled=False`` without computing anything.
    """
    # M-19: Feature flag — allows disabling without commenting out code
    if not DXY_GATE_ENABLED:
        logger.debug("DXY gate disabled via DXY_GATE_ENABLED config")
        return {
            "correlation": 0.0,
            "relevant": False,
            "direction": "neutral",
            "window_used": 0,
            "enabled": False,
        }

    base_window = window if window is not None else DXY_DEFAULT_WINDOW

    n = min(len(ohlcv_pair), len(ohlcv_index))
    if n < base_window + 1:
        return {
            "correlation": 0.0,
            "relevant": False,
            "direction": "neutral",
            "window_used": base_window,
            "enabled": True,
        }

    # M-18: Adaptive window — adjust based on recent pair volatility.
    # Compute average true range proxy from the last `base_window` bars.
    effective_window = base_window
    if adaptive_window and n > base_window:
        recent = ohlcv_pair[n - base_window : n]
        avg_range = sum(c["high"] - c["low"] for c in recent) / len(recent)
        # Compare to longer-term average (2× base window or available)
        long_start = max(0, n - base_window * 2)
        long_segment = ohlcv_pair[long_start:n]
        long_avg = sum(c["high"] - c["low"] for c in long_segment) / len(long_segment)

        if long_avg > 0:
            vol_ratio = avg_range / long_avg
            if vol_ratio > 1.3:
                # High volatility: shorten window (faster reaction)
                effective_window = max(24, int(base_window * 0.7))
            elif vol_ratio < 0.7:
                # Low volatility: lengthen window (more stability)
                effective_window = min(96, int(base_window * 1.5))
            # else: keep base_window

    # Ensure we have enough data for the effective window
    if n < effective_window + 1:
        effective_window = n - 1

    # Compute log returns for the most recent `effective_window` bars
    pair_returns = []
    index_returns = []
    for i in range(n - effective_window, n):
        if ohlcv_pair[i - 1]["close"] > 0 and ohlcv_index[i - 1]["close"] > 0:
            pr = math.log(ohlcv_pair[i]["close"] / ohlcv_pair[i - 1]["close"])
            ir = math.log(ohlcv_index[i]["close"] / ohlcv_index[i - 1]["close"])
            pair_returns.append(pr)
            index_returns.append(ir)

    if len(pair_returns) < 10:
        return {
            "correlation": 0.0,
            "relevant": False,
            "direction": "neutral",
            "window_used": effective_window,
            "enabled": True,
        }

    corr = _pearson(pair_returns, index_returns)

    relevant = abs(corr) >= min_correlation
    if corr > min_correlation:
        direction = "positive"
    elif corr < -min_correlation:
        direction = "negative"
    else:
        direction = "neutral"

    return {
        "correlation": round(corr, 4),
        "relevant": relevant,
        "direction": direction,
        "window_used": effective_window,
        "enabled": True,
    }


def _pearson(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n == 0:
        return 0.0

    mx = sum(x) / n
    my = sum(y) / n

    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den_x = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    den_y = math.sqrt(sum((yi - my) ** 2 for yi in y))

    if den_x < 1e-15 or den_y < 1e-15:
        return 0.0

    return num / (den_x * den_y)
