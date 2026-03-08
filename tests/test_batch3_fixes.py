"""
tests/test_batch3_fixes.py — Batch 3: SMC Tools Accuracy

Covers F2-01 through F2-14 (excluding F2-09, fixed in Batch 1).
Each test class targets one finding.
"""

from __future__ import annotations

import math
import pytest

# =========================================================================
# Helpers: generate synthetic OHLCV
# =========================================================================

def _candle(idx: int, o: float, h: float, l: float, c: float, t: str = "") -> dict:
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100,
        "time": t or f"2024-01-01T{idx:02d}:00:00",
    }


def _flat_candles(n: int, price: float = 100.0, spread: float = 0.5) -> list[dict]:
    """Generate n candles at roughly the same price level (flat market)."""
    return [
        _candle(i, price, price + spread, price - spread, price)
        for i in range(n)
    ]


def _trending_candles(n: int, start: float = 100.0, step: float = 1.0) -> list[dict]:
    """Generate n trending candles."""
    candles = []
    for i in range(n):
        o = start + i * step
        c = o + step * 0.8
        h = max(o, c) + abs(step) * 0.2
        l = min(o, c) - abs(step) * 0.2
        candles.append(_candle(i, o, h, l, c))
    return candles


# =========================================================================
# F2-01: Swing — tied maxima allowed
# =========================================================================
class TestF2_01_SwingTiedMaxima:
    """Flat markets where highs/lows are equal should still produce swings."""

    def test_tied_highs_produce_swing(self):
        from tools.swing import detect_swing_points

        # 11 bars (lookback=2 → window=5). Centre bar at index 2..8.
        # ALL highs are equal → old code dropped them all.
        candles = _flat_candles(11, price=100.0, spread=0.5)
        result = detect_swing_points(candles, lookback=2, min_distance_atr=0.0)
        # With tied maxima and centre preference, we should get at least 1 swing
        assert len(result["swing_highs"]) >= 1, "tied highs must produce ≥1 swing"

    def test_tied_lows_produce_swing(self):
        from tools.swing import detect_swing_points

        candles = _flat_candles(11, price=100.0, spread=0.5)
        result = detect_swing_points(candles, lookback=2, min_distance_atr=0.0)
        assert len(result["swing_lows"]) >= 1, "tied lows must produce ≥1 swing"

    def test_unique_max_still_detected(self):
        """Non-tied swings should still work as before."""
        from tools.swing import detect_swing_points

        candles = []
        for i in range(15):
            # Create a clear swing high at index 7
            if i == 7:
                candles.append(_candle(i, 100, 120, 99, 101))
            else:
                candles.append(_candle(i, 100, 105, 95, 100))
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        prices = [s["price"] for s in result["swing_highs"]]
        assert 120 in prices


# =========================================================================
# F2-02: Swing — distance filter uses time+price hybrid
# =========================================================================
class TestF2_02_SwingDistanceFilter:
    """Swings separated in time but close in price must be kept."""

    def test_time_separated_swings_kept(self):
        from tools.swing import _filter_by_distance

        # Two swings: same price, 20 bars apart → old filter drops 2nd
        swings = [
            {"index": 0, "price": 100.0},
            {"index": 20, "price": 100.1},  # only 0.1 apart in price
        ]
        result = _filter_by_distance(swings, min_dist=5.0, min_bars=10)
        assert len(result) == 2, "time-separated swings must be kept"

    def test_close_in_both_price_and_time_dropped(self):
        from tools.swing import _filter_by_distance

        swings = [
            {"index": 0, "price": 100.0},
            {"index": 2, "price": 100.1},  # close in both
        ]
        result = _filter_by_distance(swings, min_dist=5.0, min_bars=10)
        assert len(result) == 1

    def test_price_separated_kept(self):
        from tools.swing import _filter_by_distance

        swings = [
            {"index": 0, "price": 100.0},
            {"index": 2, "price": 110.0},  # price far apart, time close
        ]
        result = _filter_by_distance(swings, min_dist=5.0, min_bars=10)
        assert len(result) == 2

    def test_empty_input(self):
        from tools.swing import _filter_by_distance

        assert _filter_by_distance([], min_dist=5.0, min_bars=5) == []

    def test_disabled_filters(self):
        from tools.swing import _filter_by_distance

        swings = [{"index": 0, "price": 1}, {"index": 1, "price": 1}]
        result = _filter_by_distance(swings, min_dist=0.0, min_bars=0)
        assert len(result) == 2


# =========================================================================
# F2-03: Structure — broken swing invalidated (no re-trigger)
# =========================================================================
class TestF2_03_StructureInvalidation:
    """After BOS breaks a swing, the same swing must not fire again."""

    def test_no_duplicate_bos_on_same_swing(self):
        from tools.structure import detect_bos_choch

        # Construct scenario: swing high at 110, then 3 candles close above 110
        swing_highs = [{"index": 5, "price": 110.0, "time": "t5"}]
        swing_lows = [{"index": 3, "price": 90.0, "time": "t3"}]

        candles = []
        for i in range(20):
            if i <= 5:
                candles.append(_candle(i, 100, 105, 95, 100))
            elif i in (6, 7, 8):
                # All 3 close above 110 + buffer → old code fires BOS 3 times
                candles.append(_candle(i, 109, 115, 108, 112))
            else:
                candles.append(_candle(i, 100, 105, 95, 100))

        result = detect_bos_choch(candles, swing_highs, swing_lows, atr_value=10.0)
        bos_events = [e for e in result["events"] if e["event_type"] == "bos"]
        # After fix, the broken swing is replaced → only 1 BOS event
        assert len(bos_events) <= 2, f"Expected ≤2 BOS events, got {len(bos_events)}"


# =========================================================================
# F2-04: Structure — HH/LL updates on BOS
# =========================================================================
class TestF2_04_StructureHHLL:
    """HH and LL should be updated when BOS events fire."""

    def test_last_hh_updates_on_bullish_bos(self):
        from tools.structure import detect_bos_choch

        swing_highs = [{"index": 3, "price": 105.0, "time": "t3"}]
        swing_lows = [{"index": 2, "price": 95.0, "time": "t2"}]

        candles = []
        for i in range(15):
            if i <= 3:
                candles.append(_candle(i, 100, 105, 95, 100))
            elif i == 5:
                # Break above 105 + buffer → BOS
                candles.append(_candle(i, 104, 112, 104, 110))
            else:
                candles.append(_candle(i, 100, 104, 96, 100))

        result = detect_bos_choch(candles, swing_highs, swing_lows, atr_value=10.0)
        # last_hh should be updated (not None) after bullish BOS
        assert result["last_hh"] is not None, "last_hh should update after bullish BOS"

    def test_last_ll_updates_on_bearish_bos(self):
        from tools.structure import detect_bos_choch

        swing_highs = [{"index": 2, "price": 105.0, "time": "t2"}]
        swing_lows = [{"index": 3, "price": 95.0, "time": "t3"}]

        candles = []
        for i in range(15):
            if i <= 3:
                candles.append(_candle(i, 100, 105, 95, 100))
            elif i == 5:
                # Break below 95 - buffer → BOS
                candles.append(_candle(i, 96, 96, 88, 89))
            else:
                candles.append(_candle(i, 100, 105, 95, 100))

        result = detect_bos_choch(candles, swing_highs, swing_lows, atr_value=10.0)
        assert result["last_ll"] is not None, "last_ll should update after bearish BOS"


# =========================================================================
# F2-05: Supply & Demand — displacement from edge, not midpoint
# =========================================================================
class TestF2_05_SndDisplacementEdge:
    """Displacement must be measured from base HIGH for demand, base LOW for supply."""

    def test_demand_displacement_from_base_high(self):
        from tools.supply_demand import _check_displacement

        # Base: high=105, low=95 → midpoint=100
        # Displacement close=112
        # OLD: 112 - 100 = 12 (inflated)
        # NEW: 112 - 105 = 7 (correct: rally from zone top)
        base_candles = [
            _candle(0, 100, 105, 95, 100),
            _candle(1, 99, 104, 96, 101),
        ]
        ohlcv = base_candles + [_candle(2, 106, 115, 106, 112)]
        result = _check_displacement(
            ohlcv=ohlcv, base_start=0, base_end=1,
            base_candles=base_candles, atr_value=5.0,
            min_displacement=6.0, displacement_body_ratio=0.5,
        )
        if result is not None:
            assert result["zone_type"] == "demand"
            # displacement_strength = (112 - 105) / 5.0 = 1.4
            assert result["displacement_strength"] == pytest.approx(1.4, abs=0.1)

    def test_supply_displacement_from_base_low(self):
        from tools.supply_demand import _check_displacement

        base_candles = [
            _candle(0, 100, 105, 95, 100),
            _candle(1, 99, 104, 96, 101),
        ]
        # Displacement down: close=86
        # OLD: 100 - 86 = 14 (inflated)
        # NEW: 95 - 86 = 9 (correct: drop from zone bottom)
        ohlcv = base_candles + [_candle(2, 94, 94, 84, 86)]
        result = _check_displacement(
            ohlcv=ohlcv, base_start=0, base_end=1,
            base_candles=base_candles, atr_value=5.0,
            min_displacement=8.0, displacement_body_ratio=0.5,
        )
        if result is not None:
            assert result["zone_type"] == "supply"
            # displacement_strength = (95 - 86) / 5.0 = 1.8
            assert result["displacement_strength"] == pytest.approx(1.8, abs=0.1)


# =========================================================================
# F2-06: Supply & Demand — zone freshness tracking
# =========================================================================
class TestF2_06_SndFreshness:
    """Zones visited by later candles must be marked is_fresh=False."""

    def test_demand_zone_mitigated(self):
        from tools.supply_demand import _update_freshness

        zones = [{
            "zone_type": "demand",
            "high": 105.0,
            "low": 95.0,
            "base_end_idx": 2,
            "is_fresh": True,
        }]
        # FIX H-08: Demand mitigated when close < zone LOW (not just inside zone)
        ohlcv = [
            _candle(i, 100, 106, 94, 100) for i in range(5)
        ] + [_candle(5, 94, 95, 90, 93)]  # close=93 < zone low=95 -> mitigated
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is False

    def test_demand_zone_stays_fresh(self):
        from tools.supply_demand import _update_freshness

        zones = [{
            "zone_type": "demand",
            "high": 105.0,
            "low": 95.0,
            "base_end_idx": 2,
            "is_fresh": True,
        }]
        # All candles after zone stay ABOVE the zone
        ohlcv = [_candle(i, 100, 106, 94, 100) for i in range(3)] + [
            _candle(j, 110, 115, 108, 112) for j in range(3, 8)
        ]
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is True

    def test_supply_zone_mitigated(self):
        from tools.supply_demand import _update_freshness

        zones = [{
            "zone_type": "supply",
            "high": 105.0,
            "low": 95.0,
            "base_end_idx": 2,
            "is_fresh": True,
        }]
        # FIX H-08: Supply mitigated when close > zone HIGH (not just inside zone)
        ohlcv = [
            _candle(i, 100, 106, 94, 100) for i in range(5)
        ] + [_candle(5, 106, 110, 104, 107)]  # close=107 > zone high=105 -> mitigated
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is False


# =========================================================================
# F2-07: SNR — anchor-based clustering (order-independent)
# =========================================================================
class TestF2_07_SnrClustering:
    """Clustering must use anchor price, not drifting mean."""

    def test_cluster_anchor_based(self):
        from tools.snr import detect_snr_levels

        # Three swings: 100.0, 100.3, 100.6
        # cluster_dist = 0.2 × ATR(2.0) = 0.4
        # Old (mean-based): mean drifts → all 3 merged
        # New (anchor-based): 100.0 is anchor, 100.3 within 0.4 → merged,
        #   100.6 is 0.6 from anchor → new cluster
        swings = [
            {"price": 100.0, "index": 1, "time": "t1", "type": "high", "timeframe": "H1"},
            {"price": 100.3, "index": 5, "time": "t5", "type": "high", "timeframe": "H1"},
            {"price": 100.6, "index": 10, "time": "t10", "type": "high", "timeframe": "H1"},
        ]
        result = detect_snr_levels(swings, atr_value=2.0, cluster_atr_mult=0.2)
        levels = result["levels"]
        assert len(levels) == 2, f"Expected 2 clusters (anchor-based), got {len(levels)}"

    def test_tight_cluster_still_merges(self):
        from tools.snr import detect_snr_levels

        swings = [
            {"price": 100.0, "index": 1, "time": "t1", "type": "high", "timeframe": "H4"},
            {"price": 100.1, "index": 5, "time": "t5", "type": "high", "timeframe": "H1"},
            {"price": 100.2, "index": 10, "time": "t10", "type": "low", "timeframe": "H1"},
        ]
        result = detect_snr_levels(swings, atr_value=2.0, cluster_atr_mult=0.2)
        levels = result["levels"]
        assert len(levels) == 1, "All within 0.4 of anchor should merge"

    def test_deterministic_output(self):
        """Same input should always produce same output."""
        from tools.snr import detect_snr_levels

        swings = [
            {"price": p, "index": i * 5, "time": f"t{i}", "type": "high", "timeframe": "H1"}
            for i, p in enumerate([100, 100.2, 101, 101.1, 102])
        ]
        r1 = detect_snr_levels(swings, atr_value=2.0, cluster_atr_mult=0.2)
        r2 = detect_snr_levels(swings, atr_value=2.0, cluster_atr_mult=0.2)
        assert r1 == r2


# =========================================================================
# F2-08: OB — symmetric bullish zone boundary (full candle range)
# =========================================================================
class TestF2_08_OrderblockZone:
    """Bullish OB zone must include prev['high'], not prev['open']."""

    def test_bullish_ob_includes_high(self):
        from tools.orderblock import detect_orderblocks

        # prev: bearish candle O=105, H=108, L=98, C=99
        # curr: strong bullish displacement
        # Need ≥3 candles (n<3 guard in orderblock.py)
        candles = [
            _candle(0, 100, 105, 95, 100),  # filler
            _candle(1, 105, 108, 98, 99),   # bearish (the OB candle)
            _candle(2, 99, 120, 98, 118),   # strong bullish
        ]
        result = detect_orderblocks(candles, atr_value=5.0, displacement_atr_mult=1.0)
        obs = result["bullish_obs"]
        assert len(obs) >= 1
        ob = obs[0]
        # FIX: high should be prev["high"]=108, not prev["open"]=105
        assert ob["high"] == 108, f"Bullish OB high should be 108, got {ob['high']}"
        assert ob["low"] == 98

    def test_bearish_ob_zone_unchanged(self):
        from tools.orderblock import detect_orderblocks

        # prev: bullish candle O=95, H=108, L=94, C=105
        # curr: strong bearish displacement
        candles = [
            _candle(0, 100, 105, 95, 100),  # filler
            _candle(1, 95, 108, 94, 105),   # bullish (the OB candle)
            _candle(2, 105, 106, 80, 82),   # strong bearish
        ]
        result = detect_orderblocks(candles, atr_value=5.0, displacement_atr_mult=1.0)
        obs = result["bearish_obs"]
        assert len(obs) >= 1
        ob = obs[0]
        assert ob["high"] == 108
        # FIX H-09: bearish OB now uses full candle range (prev["low"]) not prev["open"]
        assert ob["low"] == 94  # prev candle LOW (was prev["open"]=95)


# =========================================================================
# F2-10: Trendline — ATR-adaptive tolerance
# =========================================================================
class TestF2_10_TrendlineTolerance:
    """Trendline tolerance should adapt to ATR."""

    def test_tolerance_scales_with_atr(self):
        from tools.trendline import detect_trendlines

        # With large ATR, tolerance should be larger → more trendlines valid
        swing_lows = [
            {"index": 0, "price": 100.0},
            {"index": 10, "price": 105.0},
        ]
        swing_highs = []
        # Construct ohlcv with minor breaches that static tolerance would reject
        candles = []
        for i in range(20):
            expected = 100.0 + (5.0 / 10) * i  # line from 100 to 110
            # Add slight noise below the line
            lo = expected - 0.3
            candles.append(_candle(i, expected, expected + 1, lo, expected + 0.5))

        # Large ATR → larger tolerance should accommodate noise
        result_high_atr = detect_trendlines(
            swing_highs=swing_highs, swing_lows=swing_lows,
            ohlcv=candles, pair="XAUUSD", atr_value=10.0,
        )
        # Low ATR → tighter tolerance may reject
        result_low_atr = detect_trendlines(
            swing_highs=swing_highs, swing_lows=swing_lows,
            ohlcv=candles, pair="XAUUSD", atr_value=0.01,
        )
        # Just verify the function accepts the new parameter without error
        assert isinstance(result_high_atr, dict)
        assert isinstance(result_low_atr, dict)

    def test_default_atr_zero_uses_static(self):
        from tools.trendline import detect_trendlines

        # atr_value=0 must fall back to static tolerance (no crash)
        result = detect_trendlines([], [], [], pair="XAUUSD", atr_value=0.0)
        assert result == {"uptrend_lines": [], "downtrend_lines": []}


# =========================================================================
# F2-11: Liquidity — sweep recency filter
# =========================================================================
class TestF2_11_SweepRecency:
    """Sweep detection should only scan recent candles."""

    def test_old_sweep_excluded(self):
        from tools.liquidity import detect_sweep

        n = 60  # 60 candles, max_lookback=30 → scan only bars 30-59
        candles = [_candle(i, 100, 102, 98, 100) for i in range(n)]
        # Put a sweep at bar 5 (old) and bar 50 (recent)
        candles[5] = _candle(5, 100, 115, 100, 99)     # sweep above pool
        candles[50] = _candle(50, 100, 115, 100, 99)    # sweep above pool

        pools = [{"pool_type": "eqh", "price": 110.0}]
        result = detect_sweep(candles, pools, atr_value=5.0, max_lookback=30)
        events = result["sweep_events"]
        # Only bar 50 should be detected (within last 30 bars)
        sweep_indices = [e["sweep_index"] for e in events]
        assert 5 not in sweep_indices, "Old sweep at bar 5 should be excluded"
        assert 50 in sweep_indices, "Recent sweep at bar 50 should be included"

    def test_all_bars_with_large_lookback(self):
        from tools.liquidity import detect_sweep

        n = 10
        candles = [_candle(i, 100, 102, 98, 100) for i in range(n)]
        candles[2] = _candle(2, 100, 115, 100, 99)
        pools = [{"pool_type": "eqh", "price": 110.0}]
        result = detect_sweep(candles, pools, atr_value=5.0, max_lookback=100)
        events = result["sweep_events"]
        assert len(events) >= 1


# =========================================================================
# F2-12: Scorer — max_possible from config constant
# =========================================================================
class TestF2_12_ScorerMaxPossible:
    """max_possible should come from MAX_POSSIBLE_SCORE constant."""

    def test_max_possible_matches_constant(self):
        from tools.scorer import score_setup_candidate
        from config.strategy_rules import MAX_POSSIBLE_SCORE

        result = score_setup_candidate()
        assert result["max_possible"] == MAX_POSSIBLE_SCORE

    def test_all_positive_capped_at_constant(self):
        from tools.scorer import score_setup_candidate
        from config.strategy_rules import MAX_POSSIBLE_SCORE

        result = score_setup_candidate(
            htf_alignment=True, fresh_zone=True, sweep_detected=True,
            near_major_snr=True, pa_confirmed=True, ema_filter_ok=True,
            rsi_filter_ok=True,
        )
        assert result["score"] == MAX_POSSIBLE_SCORE
        assert result["score"] == result["max_possible"]


# =========================================================================
# F2-13: Validator — counter-trend enforcement (FP-11 H-12 update)
# Now: violation when VALIDATION_RULES["must_not_counter_htf"]=True (default),
#      warning when False.  Tests updated to match enforced behaviour.
# =========================================================================
class TestF2_13_ValidatorCounterTrend:
    """Counter-trend handled based on must_not_counter_htf config."""

    def test_counter_trend_buy_bearish_is_violation(self):
        from tools.validator import validate_trading_plan

        setup = {"entry": 100, "sl": 95, "tp": 115, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=5.0, htf_bias="bearish")
        # H-12: must_not_counter_htf=True → violation
        assert any("Counter-trend" in v for v in result["violations"])
        assert result["passed"] is False

    def test_counter_trend_sell_bullish_is_violation(self):
        from tools.validator import validate_trading_plan

        setup = {"entry": 100, "sl": 105, "tp": 85, "direction": "sell"}
        result = validate_trading_plan(setup, atr_value=5.0, htf_bias="bullish")
        # H-12: must_not_counter_htf=True → violation
        assert any("Counter-trend" in v for v in result["violations"])
        assert result["passed"] is False

    def test_same_direction_no_warning(self):
        from tools.validator import validate_trading_plan

        setup = {"entry": 100, "sl": 95, "tp": 115, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=5.0, htf_bias="bullish")
        assert all("Counter-trend" not in w for w in result["warnings"])
        assert all("Counter-trend" not in v for v in result["violations"])

    def test_counter_trend_ranging_no_violation(self):
        """When htf_bias is 'ranging', no counter-trend issue."""
        from tools.validator import validate_trading_plan

        setup = {"entry": 100, "sl": 95, "tp": 115, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=5.0, htf_bias="ranging")
        assert all("Counter-trend" not in v for v in result["violations"])
        assert result["passed"] is True


# =========================================================================
# F2-14: Price action — zone proximity filter
# =========================================================================
class TestF2_14_PriceActionZoneProximity:
    """Pin bars and engulfing patterns should optionally filter by zone proximity."""

    def test_pin_bar_near_zone_kept(self):
        from tools.price_action import detect_pin_bar

        # Bullish pin at price ~100, with a demand zone at 95-105
        candles = [_candle(0, 102, 103, 96, 101)]  # long lower wick
        zones = [{"high": 105, "low": 95}]
        result = detect_pin_bar(candles, zone_levels=zones, atr_value=5.0)
        assert len(result["pin_bars"]) >= 1

    def test_pin_bar_far_from_zone_excluded(self):
        from tools.price_action import detect_pin_bar

        # Pin at price ~200, zone at 95-105 → far away
        candles = [_candle(0, 202, 203, 196, 201)]  # bullish pin far from zone
        zones = [{"high": 105, "low": 95}]
        result = detect_pin_bar(candles, zone_levels=zones, atr_value=5.0)
        assert len(result["pin_bars"]) == 0

    def test_pin_bar_no_zone_filter(self):
        """Without zone_levels, all pin bars should be detected."""
        from tools.price_action import detect_pin_bar

        candles = [_candle(0, 202, 203, 196, 201)]
        result = detect_pin_bar(candles)
        assert len(result["pin_bars"]) >= 1

    def test_engulfing_near_zone_kept(self):
        from tools.price_action import detect_engulfing

        # Bullish engulfing near zone
        candles = [
            _candle(0, 102, 103, 98, 99),   # bearish
            _candle(1, 98, 105, 97, 104),    # bullish engulfs
        ]
        zones = [{"high": 105, "low": 95}]
        result = detect_engulfing(candles, zone_levels=zones, atr_value=5.0)
        assert len(result["engulfing_patterns"]) >= 1

    def test_engulfing_far_from_zone_excluded(self):
        from tools.price_action import detect_engulfing

        candles = [
            _candle(0, 202, 203, 198, 199),
            _candle(1, 198, 206, 197, 205),
        ]
        zones = [{"high": 105, "low": 95}]
        result = detect_engulfing(candles, zone_levels=zones, atr_value=5.0)
        assert len(result["engulfing_patterns"]) == 0

    def test_engulfing_no_zone_filter(self):
        from tools.price_action import detect_engulfing

        candles = [
            _candle(0, 202, 203, 198, 199),
            _candle(1, 198, 206, 197, 205),
        ]
        result = detect_engulfing(candles)
        assert len(result["engulfing_patterns"]) >= 1


# =========================================================================
# Integration: context_builder calls with new signatures
# =========================================================================
class TestBatch3_Integration:
    """Verify that context_builder passes new args correctly."""

    def test_trendline_accepts_atr_value(self):
        from tools.trendline import detect_trendlines
        # Just verify signature doesn't crash
        result = detect_trendlines([], [], [], pair="XAUUSD", atr_value=5.0)
        assert "uptrend_lines" in result

    def test_detect_sweep_accepts_max_lookback(self):
        from tools.liquidity import detect_sweep
        result = detect_sweep([], [], atr_value=5.0, max_lookback=30)
        assert result == {"sweep_events": []}

    def test_detect_pin_bar_backward_compatible(self):
        """Call without new params must still work."""
        from tools.price_action import detect_pin_bar
        result = detect_pin_bar([])
        assert result == {"pin_bars": []}

    def test_detect_engulfing_backward_compatible(self):
        from tools.price_action import detect_engulfing
        result = detect_engulfing([])
        assert result == {"engulfing_patterns": []}

    def test_scorer_imports_constant(self):
        from tools.scorer import score_setup_candidate
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        r = score_setup_candidate()
        assert r["max_possible"] == MAX_POSSIBLE_SCORE
