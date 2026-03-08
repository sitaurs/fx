"""
tools/liquidity.py — Equal Highs/Lows detection and liquidity sweep detection.

Algorithm (masterplan 6.6):
    EQH: 2+ swing highs within 0.15 × ATR, minimum 5 bars between.
    EQL: 2+ swing lows within 0.15 × ATR, minimum 5 bars between.
    Sweep: high[t] > pool.high + buffer, BUT close[t] < pool.high.
    Filter breakout: if 2 candles close above → not a sweep, it's a breakout.

Reference: masterplan.md §6.6
"""

from __future__ import annotations

import math

from config.settings import LIQUIDITY_EQ_TOLERANCE_ATR


def detect_eqh_eql(
    swing_highs: list[dict],
    swing_lows: list[dict],
    atr_value: float,
    tolerance_atr_mult: float = LIQUIDITY_EQ_TOLERANCE_ATR,
    min_swings: int = 2,
    min_bars_between: int = 5,
) -> dict:
    """Detect Equal Highs / Equal Lows (resting liquidity pools).

    Args:
        swing_highs: Swing highs from detect_swing_points().
        swing_lows: Swing lows from detect_swing_points().
        atr_value: Current ATR value.
        tolerance_atr_mult: Max price distance as ATR multiplier (default 0.15).
        min_swings: Minimum swings to form a pool (default 2).
        min_bars_between: Minimum bar distance between contributing swings (default 5).

    Returns:
        Dict with eqh_pools and eql_pools, each a list of pool dicts.
    """
    if atr_value <= 0 or math.isnan(atr_value):
        return {"eqh_pools": [], "eql_pools": []}

    tol = tolerance_atr_mult * atr_value

    eqh_pools = _find_pools(swing_highs, tol, min_swings, min_bars_between, "eqh")
    eql_pools = _find_pools(swing_lows, tol, min_swings, min_bars_between, "eql")

    return {"eqh_pools": eqh_pools, "eql_pools": eql_pools}


def _find_pools(
    swings: list[dict],
    tolerance: float,
    min_swings: int,
    min_bars_between: int,
    pool_type: str,
) -> list[dict]:
    """Cluster swings into equal-level pools."""
    if len(swings) < min_swings:
        return []

    sorted_sw = sorted(swings, key=lambda s: s["price"])
    pools: list[dict] = []

    i = 0
    while i < len(sorted_sw):
        cluster = [sorted_sw[i]]
        j = i + 1
        while j < len(sorted_sw):
            cluster_mean = sum(s["price"] for s in cluster) / len(cluster)
            if abs(sorted_sw[j]["price"] - cluster_mean) <= tolerance:
                cluster.append(sorted_sw[j])
                j += 1
            else:
                break

        # Filter by min_bars_between
        if len(cluster) >= min_swings:
            # Sort cluster by index and check bar distances
            by_idx = sorted(cluster, key=lambda s: s["index"])
            valid = [by_idx[0]]
            for k in range(1, len(by_idx)):
                if by_idx[k]["index"] - valid[-1]["index"] >= min_bars_between:
                    valid.append(by_idx[k])

            if len(valid) >= min_swings:
                avg_price = sum(s["price"] for s in valid) / len(valid)
                pools.append({
                    "pool_type": pool_type,
                    "price": round(avg_price, 5),
                    "swing_count": len(valid),
                    "indices": [s["index"] for s in valid],
                    "is_swept": False,
                    "score": len(valid),  # simple count-based score
                })

        i = j

    return pools


def detect_sweep(
    ohlcv: list[dict],
    liquidity_pools: list[dict],
    atr_value: float,
    buffer_atr_mult: float = 0.05,
    breakout_confirm_bars: int = 2,
    max_lookback: int = 30,
) -> dict:
    """Detect liquidity sweeps from OHLCV data.

    A sweep occurs when price briefly exceeds a pool level but closes back inside.

    Args:
        ohlcv: OHLCV candle data.
        liquidity_pools: From detect_eqh_eql().
        atr_value: Current ATR.
        buffer_atr_mult: Buffer beyond pool level (default 0.05 × ATR).
        breakout_confirm_bars: Bars that must close beyond to confirm breakout (default 2).
        max_lookback: FIX F2-11 — only scan last N bars to avoid stale sweeps (default 30).

    Returns:
        Dict with sweep_events list.
    """
    n = len(ohlcv)
    if not liquidity_pools or n == 0 or atr_value <= 0:
        return {"sweep_events": []}

    buffer = buffer_atr_mult * atr_value
    events: list[dict] = []

    # FIX F2-11: Only scan recent candles
    scan_start = max(0, n - max_lookback)

    for pool in liquidity_pools:
        pool_price = pool["price"]
        is_eqh = pool["pool_type"] == "eqh"

        for i in range(scan_start, n):
            c = ohlcv[i]

            if is_eqh:
                # Sweep above: wick goes above pool + buffer, close stays below
                if c["high"] > pool_price + buffer and c["close"] < pool_price:
                    # Check breakout filter: next N bars should NOT all close above
                    breakout = _is_breakout(ohlcv, i, pool_price, is_eqh, breakout_confirm_bars)
                    if not breakout:
                        events.append({
                            "pool": pool,
                            "sweep_index": i,
                            "sweep_price": c["high"],
                            "reclaim": True,
                            "time": c["time"],
                        })
            else:
                # Sweep below: wick goes below pool - buffer, close stays above
                if c["low"] < pool_price - buffer and c["close"] > pool_price:
                    breakout = _is_breakout(ohlcv, i, pool_price, is_eqh, breakout_confirm_bars)
                    if not breakout:
                        events.append({
                            "pool": pool,
                            "sweep_index": i,
                            "sweep_price": c["low"],
                            "reclaim": True,
                            "time": c["time"],
                        })

    return {"sweep_events": events}


def _is_breakout(
    ohlcv: list[dict],
    sweep_idx: int,
    pool_price: float,
    is_eqh: bool,
    confirm_bars: int,
) -> bool:
    """Check if the sweep is actually a breakout (N consecutive closes beyond)."""
    n = len(ohlcv)
    count = 0
    for j in range(sweep_idx + 1, min(sweep_idx + 1 + confirm_bars, n)):
        close = ohlcv[j]["close"]
        if is_eqh and close > pool_price:
            count += 1
        elif not is_eqh and close < pool_price:
            count += 1
    return count >= confirm_bars
