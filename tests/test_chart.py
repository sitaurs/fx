"""
tests/test_chart.py — Tests for charts/screenshot.py.
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import numpy as np
import pytest

from charts.screenshot import ChartScreenshotGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gen(tmp_path):
    """ChartScreenshotGenerator that writes to a temp directory."""
    return ChartScreenshotGenerator(temp_dir=str(tmp_path))


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """Synthetic 50-row OHLCV DataFrame with DatetimeIndex."""
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2026-02-01", periods=n, freq="h")
    base = 2350.0
    close = base + np.cumsum(np.random.randn(n) * 2)
    high = close + np.abs(np.random.randn(n)) * 1.5
    low = close - np.abs(np.random.randn(n)) * 1.5
    opens = close + np.random.randn(n) * 0.5
    df = pd.DataFrame(
        {"Open": opens, "High": high, "Low": low, "Close": close},
        index=dates,
    )
    return df


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChartScreenshot:
    """Chart generation integration tests."""

    def test_generates_png_file(self, gen, ohlcv_df):
        """generate_entry_chart returns a valid PNG file path."""
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="XAUUSD",
            direction="sell",
            entry_zone=(2348.0, 2352.0),
            stop_loss=2360.0,
            take_profit_1=2330.0,
        )
        assert os.path.isfile(path)
        assert path.endswith(".png")
        assert os.path.getsize(path) > 1000  # at least 1KB

    def test_filename_contains_pair(self, gen, ohlcv_df):
        """The output filename contains the pair name."""
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="EURUSD",
            direction="buy",
            entry_zone=(1.0480, 1.0495),
            stop_loss=1.0460,
            take_profit_1=1.0530,
        )
        assert "EURUSD" in os.path.basename(path)

    def test_with_tp2(self, gen, ohlcv_df):
        """Chart with TP2 still generates without error."""
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="XAUUSD",
            direction="sell",
            entry_zone=(2348.0, 2352.0),
            stop_loss=2360.0,
            take_profit_1=2330.0,
            take_profit_2=2310.0,
        )
        assert os.path.isfile(path)

    def test_with_zones(self, gen, ohlcv_df):
        """Chart with supply/demand zones overlays."""
        zones = [
            {"type": "supply", "low": 2355.0, "high": 2362.0,
             "start_idx": 10, "width": 15},
            {"type": "demand", "low": 2325.0, "high": 2332.0,
             "start_idx": 5, "width": 12},
        ]
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="XAUUSD",
            direction="sell",
            entry_zone=(2348.0, 2352.0),
            stop_loss=2360.0,
            take_profit_1=2330.0,
            zones=zones,
        )
        assert os.path.isfile(path)

    def test_with_trendlines(self, gen, ohlcv_df):
        """Chart with trendline overlays."""
        trendlines = [
            {"x": [5, 30], "y": [2355.0, 2345.0]},
        ]
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="XAUUSD",
            direction="buy",
            entry_zone=(2340.0, 2345.0),
            stop_loss=2330.0,
            take_profit_1=2365.0,
            trendlines=trendlines,
        )
        assert os.path.isfile(path)

    def test_to_base64(self, gen, ohlcv_df):
        """to_base64 returns a valid data-URI string."""
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="XAUUSD",
            direction="sell",
            entry_zone=(2348.0, 2352.0),
            stop_loss=2360.0,
            take_profit_1=2330.0,
        )
        b64 = ChartScreenshotGenerator.to_base64(path)
        assert b64.startswith("data:image/png;base64,")
        assert len(b64) > 100

    def test_to_bytes(self, gen, ohlcv_df):
        """to_bytes returns raw PNG bytes with correct header."""
        path = gen.generate_entry_chart(
            ohlcv=ohlcv_df,
            pair="XAUUSD",
            direction="sell",
            entry_zone=(2348.0, 2352.0),
            stop_loss=2360.0,
            take_profit_1=2330.0,
        )
        raw = ChartScreenshotGenerator.to_bytes(path)
        assert isinstance(raw, bytes)
        assert raw[:4] == b"\x89PNG"  # PNG magic header

    def test_minimal_data(self, gen):
        """Works with the minimum number of candles (5 rows)."""
        dates = pd.date_range("2026-02-01", periods=5, freq="h")
        df = pd.DataFrame(
            {
                "Open": [2350, 2352, 2348, 2351, 2349],
                "High": [2355, 2356, 2353, 2354, 2353],
                "Low": [2348, 2350, 2346, 2349, 2347],
                "Close": [2352, 2348, 2351, 2349, 2350],
            },
            index=dates,
        )
        path = gen.generate_entry_chart(
            ohlcv=df,
            pair="XAUUSD",
            direction="sell",
            entry_zone=(2348.0, 2352.0),
            stop_loss=2360.0,
            take_profit_1=2330.0,
        )
        assert os.path.isfile(path)
