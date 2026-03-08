"""
charts/screenshot.py — Chart screenshot generator for WhatsApp notifications.

Uses ``mplfinance`` + ``matplotlib`` to generate candlestick charts with:
  - Entry zone rectangle
  - SL / TP horizontal lines
  - Supply / Demand zone overlays
  - Order Block overlays
  - Trendline overlays (ray-style)
  - Swing point markers (High ▼ / Low ▲)
  - SNR horizontal lines
  - BOS / CHoCH event markers

Supports both full entry-charts and isolated audit charts.

Reference: masterplan.md §22.5
"""

from __future__ import annotations

import base64
import io
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must be before pyplot import

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import mplfinance as mpf
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default chart style (dark theme matching TradingView aesthetic)
# ---------------------------------------------------------------------------

_MARKET_COLORS = mpf.make_marketcolors(
    up="#26a69a",
    down="#ef5350",
    edge="inherit",
    wick="inherit",
    volume="in",
)

DARK_STYLE = mpf.make_mpf_style(
    marketcolors=_MARKET_COLORS,
    gridcolor="#2a2a2a",
    gridstyle="--",
    facecolor="#0d1117",
    figcolor="#0d1117",
    rc={
        "axes.labelcolor": "white",
        "xtick.color": "white",
        "ytick.color": "white",
    },
)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class ChartScreenshotGenerator:
    """Generate PNG chart screenshots for WhatsApp / Dashboard."""

    def __init__(self, temp_dir: str | None = None) -> None:
        self.temp_dir = temp_dir or os.path.join(tempfile.gettempdir(), "charts")
        os.makedirs(self.temp_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Main entry point (original — backward-compatible)
    # ------------------------------------------------------------------

    def generate_entry_chart(
        self,
        ohlcv: pd.DataFrame,
        pair: str,
        direction: str,
        entry_zone: tuple[float, float],
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: Optional[float] = None,
        zones: Optional[list[dict]] = None,
        trendlines: Optional[list[dict]] = None,
        figsize: tuple[int, int] = (12, 8),
        dpi: int = 150,
    ) -> str:
        """Generate an entry-chart PNG and return its file path.

        Parameters
        ----------
        ohlcv:
            DataFrame with columns [Open, High, Low, Close] and a
            ``DatetimeIndex``.  At least 20 rows recommended.

        Raises
        ------
        ValueError
            If *ohlcv* is empty (L-60).
        """
        # L-60: guard against empty / too-small DataFrames
        if ohlcv is None or ohlcv.empty:
            raise ValueError("ohlcv DataFrame is empty -- cannot generate chart")

        fig, axes = mpf.plot(
            ohlcv,
            type="candle",
            style=DARK_STYLE,
            figsize=figsize,
            returnfig=True,
            panel_ratios=(4,),
        )

        ax = axes[0]
        n = len(ohlcv)

        # --- Entry zone (translucent green rectangle) ----------------------
        entry_low, entry_high = entry_zone
        zone_x_start = max(n - 20, 0)
        zone_width = n - zone_x_start
        rect = patches.Rectangle(
            (zone_x_start, entry_low),
            zone_width,
            entry_high - entry_low,
            linewidth=1,
            edgecolor="#4caf50",
            facecolor="#4caf5033",
            label="Entry Zone",
        )
        ax.add_patch(rect)

        # --- SL line (red dashed) ------------------------------------------
        ax.axhline(
            y=stop_loss, color="#f44336", linestyle="--",
            linewidth=2, label=f"SL: {stop_loss}",
        )

        # --- TP1 line (green dashed) ----------------------------------------
        ax.axhline(
            y=take_profit_1, color="#4caf50", linestyle="--",
            linewidth=2, label=f"TP1: {take_profit_1}",
        )

        # --- TP2 line (blue dashed) -----------------------------------------
        if take_profit_2 is not None:
            ax.axhline(
                y=take_profit_2, color="#2196f3", linestyle="--",
                linewidth=2, label=f"TP2: {take_profit_2}",
            )

        # --- SnD / OB zone overlays ----------------------------------------
        if zones:
            for z in zones:
                color = "#ff9800" if z.get("type") == "supply" else "#03a9f4"
                sx = z.get("start_idx", 0)
                w = z.get("width", 10)
                rect_z = patches.Rectangle(
                    (sx, z["low"]),
                    w,
                    z["high"] - z["low"],
                    linewidth=1,
                    edgecolor=color,
                    facecolor=f"{color}22",
                )
                ax.add_patch(rect_z)

        # --- Trendlines -----------------------------------------------------
        if trendlines:
            for tl in trendlines:
                ax.plot(
                    tl["x"], tl["y"],
                    color="#ffeb3b", linestyle="-", linewidth=1.5,
                )

        # --- Title -----------------------------------------------------------
        arrow = "\u2b06" if direction == "buy" else "\u2b07"
        ax.set_title(
            f"{pair}  {direction.upper()} {arrow}  |  "
            f"Entry: {entry_low}-{entry_high}",
            fontsize=14,
            color="white",
            loc="left",
        )

        # --- Legend -----------------------------------------------------------
        ax.legend(
            loc="upper left", fontsize=9,
            facecolor="#1e1e1e", labelcolor="white",
        )

        # --- Save to file ----------------------------------------------------
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.temp_dir, f"{pair}_{ts}.png")
        try:
            fig.savefig(filepath, dpi=dpi, bbox_inches="tight", facecolor="#0d1117")
            logger.info("Chart saved → %s", filepath)
            return filepath
        finally:
            plt.close(fig)

    # ------------------------------------------------------------------
    # Isolated Audit Chart (flexible, element-by-element)
    # ------------------------------------------------------------------

    def generate_audit_chart(
        self,
        ohlcv: pd.DataFrame,
        pair: str,
        title: str,
        *,
        swing_highs: Optional[list[dict]] = None,
        swing_lows: Optional[list[dict]] = None,
        snr_levels: Optional[list[dict]] = None,
        supply_zones: Optional[list[dict]] = None,
        demand_zones: Optional[list[dict]] = None,
        bullish_obs: Optional[list[dict]] = None,
        bearish_obs: Optional[list[dict]] = None,
        trendlines: Optional[list[dict]] = None,
        bos_choch_events: Optional[list[dict]] = None,
        figsize: tuple[int, int] = (14, 8),
        dpi: int = 150,
        filename: Optional[str] = None,
    ) -> str:
        """Generate an audit chart PNG with only the requested overlays.

        All overlay parameters are optional -- only drawn if provided.
        Returns the filepath of the saved PNG.

        Raises
        ------
        ValueError
            If *ohlcv* is empty (L-60).
        """
        # L-60: guard against empty DataFrames
        if ohlcv is None or ohlcv.empty:
            raise ValueError("ohlcv DataFrame is empty -- cannot generate chart")
        fig, axes = mpf.plot(
            ohlcv,
            type="candle",
            style=DARK_STYLE,
            figsize=figsize,
            returnfig=True,
            panel_ratios=(4,),
        )
        ax = axes[0]
        n = len(ohlcv)

        # --- Swing markers --------------------------------------------------
        if swing_highs:
            idxs = [s["index"] for s in swing_highs if 0 <= s["index"] < n]
            prices = [s["price"] for s in swing_highs if 0 <= s["index"] < n]
            ax.scatter(
                idxs, prices, marker="v", color="#ef5350", s=60,
                zorder=5, label=f"Swing High ({len(idxs)})",
            )

        if swing_lows:
            idxs = [s["index"] for s in swing_lows if 0 <= s["index"] < n]
            prices = [s["price"] for s in swing_lows if 0 <= s["index"] < n]
            ax.scatter(
                idxs, prices, marker="^", color="#26a69a", s=60,
                zorder=5, label=f"Swing Low ({len(idxs)})",
            )

        # --- SNR horizontal levels ------------------------------------------
        if snr_levels:
            for i, lvl in enumerate(snr_levels):
                alpha = max(0.3, min(1.0, lvl.get("score", 0.5)))
                lw = 2 if lvl.get("is_major") else 1
                ax.axhline(
                    y=lvl["price"], color="#ab47bc", linestyle="-",
                    linewidth=lw, alpha=alpha,
                )
            ax.plot([], [], color="#ab47bc", linestyle="-",
                    label=f"SNR ({len(snr_levels)})")

        # --- Supply Zones (orange boxes) ------------------------------------
        if supply_zones:
            for z in supply_zones:
                sx = z.get("base_start_idx", z.get("start_idx", 0))
                w = z.get("base_end_idx", sx + 10) - sx + 5
                rect_z = patches.Rectangle(
                    (sx, z["low"]), w, z["high"] - z["low"],
                    linewidth=1.2, edgecolor="#ff9800", facecolor="#ff980030",
                )
                ax.add_patch(rect_z)
            ax.plot([], [], color="#ff9800", linewidth=3,
                    label=f"Supply ({len(supply_zones)})")

        # --- Demand Zones (cyan boxes) --------------------------------------
        if demand_zones:
            for z in demand_zones:
                sx = z.get("base_start_idx", z.get("start_idx", 0))
                w = z.get("base_end_idx", sx + 10) - sx + 5
                rect_z = patches.Rectangle(
                    (sx, z["low"]), w, z["high"] - z["low"],
                    linewidth=1.2, edgecolor="#03a9f4", facecolor="#03a9f430",
                )
                ax.add_patch(rect_z)
            ax.plot([], [], color="#03a9f4", linewidth=3,
                    label=f"Demand ({len(demand_zones)})")

        # --- Bullish Order Blocks (lime boxes) ------------------------------
        if bullish_obs:
            for ob in bullish_obs:
                ci = ob.get("candle_index", 0)
                rect_ob = patches.Rectangle(
                    (ci, ob["low"]), 5, ob["high"] - ob["low"],
                    linewidth=1.2, edgecolor="#76ff03", facecolor="#76ff0325",
                    linestyle="--",
                )
                ax.add_patch(rect_ob)
            ax.plot([], [], color="#76ff03", linewidth=2, linestyle="--",
                    label=f"Bull OB ({len(bullish_obs)})")

        # --- Bearish Order Blocks (pink boxes) ------------------------------
        if bearish_obs:
            for ob in bearish_obs:
                ci = ob.get("candle_index", 0)
                rect_ob = patches.Rectangle(
                    (ci, ob["low"]), 5, ob["high"] - ob["low"],
                    linewidth=1.2, edgecolor="#ff4081", facecolor="#ff408125",
                    linestyle="--",
                )
                ax.add_patch(rect_ob)
            ax.plot([], [], color="#ff4081", linewidth=2, linestyle="--",
                    label=f"Bear OB ({len(bearish_obs)})")

        # --- Trendlines (yellow rays) --------------------------------------
        if trendlines:
            for tl in trendlines:
                ax.plot(
                    tl["x"], tl["y"],
                    color="#ffeb3b", linestyle="-", linewidth=1.5, zorder=3,
                )
            ax.plot([], [], color="#ffeb3b", linestyle="-",
                    label=f"Trendline ({len(trendlines)})")

        # --- BOS / CHoCH event markers --------------------------------------
        if bos_choch_events:
            bos_count = 0
            choch_count = 0
            for ev in bos_choch_events:
                idx = ev.get("break_index", 0)
                price = ev.get("break_price", 0)
                if idx < 0 or idx >= n:
                    continue
                etype = ev.get("event_type", "BOS")
                if etype == "BOS":
                    color, marker = "#2196f3", "D"
                    bos_count += 1
                else:  # CHoCH
                    color, marker = "#ff5722", "X"
                    choch_count += 1

                ax.scatter(
                    [idx], [price], marker=marker, color=color,
                    s=80, zorder=6, edgecolors="white", linewidths=0.5,
                )
                ax.annotate(
                    etype,
                    xy=(idx, price),
                    xytext=(idx + 1, price),
                    fontsize=7,
                    color=color,
                    fontweight="bold",
                )

            if bos_count:
                ax.scatter([], [], marker="D", color="#2196f3", s=40,
                           label=f"BOS ({bos_count})")
            if choch_count:
                ax.scatter([], [], marker="X", color="#ff5722", s=40,
                           label=f"CHoCH ({choch_count})")

        # --- Title -----------------------------------------------------------
        ax.set_title(title, fontsize=13, color="white", loc="left")

        # --- Legend -----------------------------------------------------------
        ax.legend(
            loc="upper left", fontsize=8,
            facecolor="#1e1e1e", labelcolor="white",
            framealpha=0.8,
        )

        # --- Save to file ----------------------------------------------------
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = filename or f"{pair}_audit_{ts}.png"
        filepath = os.path.join(self.temp_dir, fname)
        try:
            fig.savefig(filepath, dpi=dpi, bbox_inches="tight", facecolor="#0d1117")
            logger.info("Chart saved → %s", filepath)
            return filepath
        finally:
            plt.close(fig)

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    @staticmethod
    def to_base64(filepath: str) -> str:
        """Return the PNG as a data-URI base64 string.

        .. deprecated::
            Use :meth:`to_data_uri` for clarity (L-61).  This alias is
            kept for backward compatibility.
        """
        return ChartScreenshotGenerator.to_data_uri(filepath)

    @staticmethod
    def to_data_uri(filepath: str) -> str:
        """Return the PNG as a ``data:image/png;base64,…`` URI (L-61)."""
        with open(filepath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/png;base64,{b64}"

    @staticmethod
    def to_bytes(filepath: str) -> bytes:
        """Return raw PNG bytes."""
        with open(filepath, "rb") as f:
            return f.read()

    # CON-25: temp file cleanup
    def cleanup(self) -> int:
        """Remove all PNG files from the temp directory.

        Returns the number of files removed.
        """
        removed = 0
        for entry in Path(self.temp_dir).glob("*.png"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
        if removed:
            logger.info("Chart cleanup: removed %d temp files", removed)
        return removed


# M-39: Lazy-init singleton — matplotlib import is heavy (~50 MB).
# The real instance is created on first access via ``get_chart_generator()``.
_chart_generator: ChartScreenshotGenerator | None = None


def get_chart_generator() -> ChartScreenshotGenerator:
    """Return the module-level chart generator (created on first call)."""
    global _chart_generator
    if _chart_generator is None:
        _chart_generator = ChartScreenshotGenerator()
    return _chart_generator


# Backward-compat: eagerly created singleton.  New code should prefer
# ``get_chart_generator()`` to benefit from lazy init when the module
# is imported only for type-checking.
chart_generator = ChartScreenshotGenerator()
