"""
tests/test_snr.py — TDD tests for SNR (Support/Resistance) level clustering.

Reference: masterplan.md §6.3
Algorithm:
    - Input: all swing prices from multi-TF
    - Cluster: distance <= 0.2 × ATR → same level
    - Score: touches × recency × rejection_strength × TF_weight
    - Major: high score + appears on H4/H1
    - Minor: low score / only M30/M15

Written FIRST — implementation follows.
"""

from __future__ import annotations

import pytest

from tools.snr import detect_snr_levels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _swing(price: float, idx: int = 0, stype: str = "high", tf: str = "H1") -> dict:
    return {
        "price": price,
        "index": idx,
        "time": f"T{idx}",
        "type": stype,
        "timeframe": tf,
    }


# =========================================================================
# TestDetectSnrLevels
# =========================================================================
class TestDetectSnrLevels:

    def test_empty_input(self):
        result = detect_snr_levels([], atr_value=10.0)
        assert result["levels"] == []

    def test_single_swing_returns_one_level(self):
        swings = [_swing(2000.0, idx=5)]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert len(result["levels"]) == 1
        assert result["levels"][0]["price"] == pytest.approx(2000.0)
        assert result["levels"][0]["touches"] == 1

    def test_two_close_swings_cluster(self):
        """Swings within 0.2 × ATR → cluster into 1 level."""
        # ATR=10, threshold=0.2*10=2. Swings at 2000 and 2001 → cluster
        swings = [_swing(2000.0, idx=5), _swing(2001.0, idx=10)]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert len(result["levels"]) == 1
        lvl = result["levels"][0]
        assert lvl["touches"] == 2
        assert 2000.0 <= lvl["price"] <= 2001.0  # mean price

    def test_two_far_swings_separate(self):
        """Swings farther than 0.2 × ATR → 2 separate levels."""
        # ATR=10, threshold=2. Swings at 2000 and 2010 → separate
        swings = [_swing(2000.0, idx=5), _swing(2010.0, idx=10)]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert len(result["levels"]) == 2

    def test_multiple_touches_increase_score(self):
        """More touches at same level → higher score."""
        swings = [
            _swing(2000.0, idx=5),
            _swing(2000.5, idx=15),
            _swing(2001.0, idx=25),
        ]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert len(result["levels"]) == 1
        assert result["levels"][0]["touches"] == 3

    def test_htf_swings_are_major(self):
        """Swings from H4/H1 should produce major levels."""
        swings = [
            _swing(2000.0, idx=5, tf="H4"),
            _swing(2000.5, idx=15, tf="H4"),
        ]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert len(result["levels"]) == 1
        assert result["levels"][0]["is_major"] is True

    def test_ltf_only_swings_are_minor(self):
        """Swings only from M15 should be minor."""
        swings = [
            _swing(2000.0, idx=5, tf="M15"),
            _swing(2000.5, idx=15, tf="M15"),
        ]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert len(result["levels"]) == 1
        assert result["levels"][0]["is_major"] is False

    def test_level_has_required_keys(self):
        swings = [_swing(2000.0, idx=5)]
        result = detect_snr_levels(swings, atr_value=10.0)
        lvl = result["levels"][0]
        assert "price" in lvl
        assert "touches" in lvl
        assert "score" in lvl
        assert "is_major" in lvl
        assert "source_tf" in lvl

    def test_levels_sorted_by_score_desc(self):
        """Levels should be sorted by score, highest first."""
        swings = [
            _swing(2000.0, idx=5, tf="M15"),   # 1 touch, minor
            _swing(2050.0, idx=10, tf="H4"),
            _swing(2050.5, idx=20, tf="H4"),
            _swing(2050.2, idx=30, tf="H1"),   # 3 touches, major
        ]
        result = detect_snr_levels(swings, atr_value=10.0)
        scores = [l["score"] for l in result["levels"]]
        assert scores == sorted(scores, reverse=True)

    def test_cluster_tolerance_configurable(self):
        """Custom ATR mult for cluster distance."""
        swings = [_swing(2000.0, idx=5), _swing(2004.0, idx=10)]
        # Default 0.2*10=2 → separate
        r1 = detect_snr_levels(swings, atr_value=10.0, cluster_atr_mult=0.2)
        assert len(r1["levels"]) == 2
        # With 0.5*10=5 → cluster
        r2 = detect_snr_levels(swings, atr_value=10.0, cluster_atr_mult=0.5)
        assert len(r2["levels"]) == 1
