"""
tools/indicators.py — Technical indicators: ATR, EMA, RSI, RSI Divergence.

All functions are pure/deterministic — no LLM calls.
Designed as Gemini Function Declarations (tool use).

Reference: masterplan.md §6.9, §5.1
"""

from __future__ import annotations

import math

from config.settings import RSI_DIVERGENCE_LOOKBACK


# ---------------------------------------------------------------------------
# EMA cache (FP-10 L-23) — avoids recomputation in the same analysis cycle
# Key: (n, period, first_close, mid_close, last_close)
# Bounded by _EMA_CACHE_MAX to prevent memory leaks across cycles.
# ---------------------------------------------------------------------------
_ema_cache: dict[tuple, dict] = {}
_EMA_CACHE_MAX: int = 64


def _ema_cache_key(ohlcv: list[dict], period: int) -> tuple:
    """Lightweight fingerprint from candle data for EMA caching."""
    n = len(ohlcv)
    if n == 0:
        return (0, period, 0.0, 0.0, 0.0)
    return (
        n,
        period,
        ohlcv[0]["close"],
        ohlcv[n // 2]["close"],
        ohlcv[-1]["close"],
    )


def clear_ema_cache() -> None:
    """Explicitly clear the EMA cache (e.g. between analysis cycles)."""
    _ema_cache.clear()


def compute_atr(ohlcv: list[dict], period: int = 14) -> dict:
    """Compute Average True Range (ATR) using Wilder's smoothing.

    Args:
        ohlcv: List of candle dicts with keys: open, high, low, close, volume, time.
        period: ATR lookback period (default 14).

    Returns:
        Dict with keys: period (int), values (list[float]), current (float).
        Values are NaN-padded for the first ``period`` bars.
    """
    n = len(ohlcv)
    if n == 0:
        return {"period": period, "values": [], "current": float("nan")}

    # --- True Range per bar ---
    tr: list[float] = []
    for i in range(n):
        h = ohlcv[i]["high"]
        l = ohlcv[i]["low"]
        if i == 0:
            tr.append(h - l)
        else:
            prev_c = ohlcv[i - 1]["close"]
            tr.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))

    # --- Wilder's smoothed ATR ---
    atr_values: list[float] = [float("nan")] * n

    if n < period:
        # Not enough data for a full period — use simple average of available TR
        avg = sum(tr) / n
        atr_values[-1] = avg
        return {"period": period, "values": atr_values, "current": avg}

    # Initial ATR = SMA of first `period` TRs
    first_atr = sum(tr[:period]) / period
    atr_values[period - 1] = first_atr

    # Subsequent ATR = (prev_ATR × (period-1) + current_TR) / period
    for i in range(period, n):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + tr[i]) / period

    current = atr_values[-1]
    return {"period": period, "values": atr_values, "current": current}


def compute_ema(ohlcv: list[dict], period: int = 20, *, use_cache: bool = True) -> dict:
    """Compute Exponential Moving Average on close prices.

    Args:
        ohlcv: List of candle dicts.
        period: EMA period (default 20).
        use_cache: If True, check / populate the module-level EMA cache
                   to avoid recomputing the same data + period (FP-10 L-23).

    Returns:
        Dict with keys: period (int), values (list[float]), current (float).
    """
    # --- L-23: check cache ---
    if use_cache:
        key = _ema_cache_key(ohlcv, period)
        cached = _ema_cache.get(key)
        if cached is not None:
            return cached

    n = len(ohlcv)
    if n == 0:
        result = {"period": period, "values": [], "current": float("nan")}
        return result

    closes = [c["close"] for c in ohlcv]
    ema_values: list[float] = [float("nan")] * n
    multiplier = 2.0 / (period + 1)

    if n < period:
        # Use SMA of available data as seed
        sma = sum(closes) / n
        ema_values[-1] = sma
        return {"period": period, "values": ema_values, "current": sma}

    # Seed: SMA of first `period` closes
    sma = sum(closes[:period]) / period
    ema_values[period - 1] = sma

    for i in range(period, n):
        ema_values[i] = (closes[i] - ema_values[i - 1]) * multiplier + ema_values[i - 1]

    current = ema_values[-1]
    result = {"period": period, "values": ema_values, "current": current}

    # --- L-23: populate cache ---
    if use_cache:
        if len(_ema_cache) >= _EMA_CACHE_MAX:
            _ema_cache.clear()
        _ema_cache[key] = result

    return result


def compute_rsi(ohlcv: list[dict], period: int = 14) -> dict:
    """Compute Relative Strength Index using Wilder's smoothing.

    Args:
        ohlcv: List of candle dicts.
        period: RSI period (default 14).

    Returns:
        Dict with keys: period (int), values (list[float]), current (float).
        RSI = 100 - 100/(1 + RS), where RS = avg_gain / avg_loss.
    """
    n = len(ohlcv)
    if n == 0:
        return {"period": period, "values": [], "current": float("nan")}

    closes = [c["close"] for c in ohlcv]

    # --- Compute gains and losses ---
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for i in range(1, n):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    rsi_values: list[float] = [float("nan")] * n

    if n < period + 1:
        # Not enough data
        return {"period": period, "values": rsi_values, "current": float("nan")}

    # Initial averages (SMA over first `period` changes — indices 1..period)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period

    rsi_values[period] = _calc_rsi(avg_gain, avg_loss)

    # Wilder smoothing for subsequent bars
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_values[i] = _calc_rsi(avg_gain, avg_loss)

    current = rsi_values[-1]
    if math.isnan(current):
        current = rsi_values[-1]
    return {"period": period, "values": rsi_values, "current": current}


def _calc_rsi(avg_gain: float, avg_loss: float) -> float:
    """Calculate RSI from average gain and average loss."""
    if avg_loss == 0.0:
        if avg_gain == 0.0:
            return 50.0  # no movement → neutral
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# RSI Divergence (FP-10 M-14)
# ---------------------------------------------------------------------------

def detect_rsi_divergence(
    ohlcv: list[dict],
    rsi_values: list[float],
    atr_value: float = 0.0,
    lookback: int | None = None,
) -> dict:
    """Detect RSI divergence using ATR-scaled lookback.

    Bearish divergence: price makes higher high but RSI makes lower high.
    Bullish divergence: price makes lower low but RSI makes higher low.

    The lookback window adapts to volatility: in high-volatility regimes
    (ATR > median ATR of the series), lookback is shortened by 30% to
    respond faster to momentum exhaustion; in low-volatility regimes it
    uses the base value from ``RSI_DIVERGENCE_LOOKBACK`` config.

    Args:
        ohlcv: OHLCV candle data.
        rsi_values: Pre-computed RSI values (same length as ohlcv).
        atr_value: Current ATR — used to scale the lookback window.
                   If 0 or NaN, uses the static lookback from config.
        lookback: Override lookback (default: ``RSI_DIVERGENCE_LOOKBACK``).

    Returns:
        Dict with keys:
            divergence_type: "bullish" | "bearish" | None
            price_pivot_idx: Index of the divergence price pivot (int | None)
            rsi_pivot_idx: Index of the divergence RSI pivot (int | None)
            lookback_used: Actual lookback applied after ATR scaling (int)
    """
    base_lookback = lookback if lookback is not None else RSI_DIVERGENCE_LOOKBACK
    n = len(ohlcv)

    if n < 4 or len(rsi_values) != n:
        return {
            "divergence_type": None,
            "price_pivot_idx": None,
            "rsi_pivot_idx": None,
            "lookback_used": base_lookback,
        }

    # --- ATR-adaptive lookback (M-14) ---
    effective_lookback = base_lookback
    if atr_value > 0 and not math.isnan(atr_value):
        # Estimate median ATR from simple average of candle ranges
        ranges = [c["high"] - c["low"] for c in ohlcv[-min(50, n):]]
        median_range = sorted(ranges)[len(ranges) // 2] if ranges else atr_value
        if atr_value > median_range:
            # High-volatility regime → shorten lookback (30% reduction)
            effective_lookback = max(4, int(base_lookback * 0.7))

    start = max(0, n - effective_lookback)

    # Find two most recent local highs and lows in the window
    highs: list[tuple[int, float, float]] = []  # (idx, price_high, rsi_val)
    lows: list[tuple[int, float, float]] = []   # (idx, price_low, rsi_val)

    for i in range(start + 1, n - 1):
        rsi_i = rsi_values[i]
        if math.isnan(rsi_i):
            continue
        # Local high: higher than both neighbours
        if ohlcv[i]["high"] > ohlcv[i - 1]["high"] and ohlcv[i]["high"] > ohlcv[i + 1]["high"]:
            highs.append((i, ohlcv[i]["high"], rsi_i))
        # Local low: lower than both neighbours
        if ohlcv[i]["low"] < ohlcv[i - 1]["low"] and ohlcv[i]["low"] < ohlcv[i + 1]["low"]:
            lows.append((i, ohlcv[i]["low"], rsi_i))

    result: dict = {
        "divergence_type": None,
        "price_pivot_idx": None,
        "rsi_pivot_idx": None,
        "lookback_used": effective_lookback,
    }

    # --- Bearish divergence: recent price HH but RSI LH ---
    if len(highs) >= 2:
        prev_h, curr_h = highs[-2], highs[-1]
        if curr_h[1] > prev_h[1] and curr_h[2] < prev_h[2]:
            result["divergence_type"] = "bearish"
            result["price_pivot_idx"] = curr_h[0]
            result["rsi_pivot_idx"] = prev_h[0]
            return result

    # --- Bullish divergence: recent price LL but RSI HL ---
    if len(lows) >= 2:
        prev_l, curr_l = lows[-2], lows[-1]
        if curr_l[1] < prev_l[1] and curr_l[2] > prev_l[2]:
            result["divergence_type"] = "bullish"
            result["price_pivot_idx"] = curr_l[0]
            result["rsi_pivot_idx"] = prev_l[0]
            return result

    return result
