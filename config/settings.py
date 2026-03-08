"""
config/settings.py — Central configuration for AI Forex Agent.

Loads from environment variables (.env) with sensible defaults.
Reference: masterplan.md §4, §2.1 (Hybrid Strategy), §6.4 (SnD Tolerance)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Gemini API
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_PRO_MODEL: str = "gemini-3-pro-preview"
GEMINI_FLASH_MODEL: str = "gemini-3-flash-preview"

# ---------------------------------------------------------------------------
# MT5 OHLCV API (local Flask bridge, optional)
# ---------------------------------------------------------------------------
MT5_OHLCV_API_URL: str = os.getenv("MT5_OHLCV_API_URL", "")

# ---------------------------------------------------------------------------
# OANDA v20 REST API
# ---------------------------------------------------------------------------
OANDA_API_KEY: str = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID: str = os.getenv("OANDA_ACCOUNT_ID", "")
# Practice vs Live — auto-detect from account ID prefix
# Practice: 101-xxx → api-fxpractice.oanda.com
# Live:     001-xxx → api-fxtrade.oanda.com
_oanda_is_practice = OANDA_ACCOUNT_ID.startswith("101-") if OANDA_ACCOUNT_ID else True
OANDA_BASE_URL: str = os.getenv(
    "OANDA_BASE_URL",
    "https://api-fxpractice.oanda.com" if _oanda_is_practice else "https://api-fxtrade.oanda.com",
)
OANDA_STREAM_URL: str = os.getenv(
    "OANDA_STREAM_URL",
    "https://stream-fxpractice.oanda.com" if _oanda_is_practice else "https://stream-fxtrade.oanda.com",
)
# DNS overrides — bypass ISP DNS blocking (Indonesia)
# Resolved via Cloudflare DNS-over-HTTPS on 2026-02-22
OANDA_DNS_OVERRIDES: dict[str, str] = {
    "api-fxpractice.oanda.com": "104.18.34.254",
    "stream-fxpractice.oanda.com": "172.64.148.74",
    "api-fxtrade.oanda.com": "104.18.34.254",
    "stream-fxtrade.oanda.com": "172.64.148.74",
}

# ---------------------------------------------------------------------------
# Trading Pairs & Timeframes
# ---------------------------------------------------------------------------
MVP_PAIRS: list[str] = [
    "XAUUSD",
    "EURUSD",
    "GBPJPY",
    "USDCHF",
    "USDCAD",
    "USDJPY",
]

# D-11: ALL_PAIRS was an identical duplicate of MVP_PAIRS in a different order.
# Kept as alias for backward-compat (scheduler/runner.py imports it).
ALL_PAIRS: list[str] = MVP_PAIRS

TIMEFRAMES: list[str] = ["H4", "H1", "M30", "M15"]

# Default analysis timeframes used by orchestrator (subset of TIMEFRAMES)
# masterplan uses H4+H1+M15 for the 3-TF analysis pipeline.
ANALYSIS_TIMEFRAMES: list[str] = [
    t.strip()
    for t in os.getenv("ANALYSIS_TIMEFRAMES", "H4,H1,M15").split(",")
    if t.strip()
]

# Swing lookback per timeframe (masterplan 6.1)
# Tuned 2026-02-21: M15 6→4, M30 5→3 for better detection in strong trends
SWING_LOOKBACK: dict[str, int] = {
    "H4": 3,
    "H1": 4,
    "M30": 3,
    "M15": 4,
}

# ---------------------------------------------------------------------------
# Zone Tolerances (masterplan 6.4 / 6.7)
# ---------------------------------------------------------------------------
SND_TOLERANCE: dict[str, float] = {
    "XAUUSD": 2.0,      # $2 tolerance for Gold
    "EURUSD": 0.0010,    # 10 pips
    "GBPJPY": 0.15,      # 15 pips
    "USDCHF": 0.0010,
    "USDCAD": 0.0010,
    "USDJPY": 0.15,
}

TRENDLINE_TOLERANCE: dict[str, float] = {
    "XAUUSD": 3.0,      # $3 tolerance
    "EURUSD": 0.0008,    # 8 pips
    "GBPJPY": 0.12,      # 12 pips
    "USDCHF": 0.0008,
    "USDCAD": 0.0008,
    "USDJPY": 0.12,
}

# Zone priority weights (masterplan 6.5)
ZONE_PRIORITY: dict[str, float] = {
    "supply_demand": 1.0,    # ⭐ King
    "snr_level": 0.8,        # Pendukung kuat
    "order_block": 0.6,      # 📦 Prince (secondary)
}

# ---------------------------------------------------------------------------
# Pip value helpers — needed to normalize tolerances
# ---------------------------------------------------------------------------
# Point size = 1 "pip" in price terms
PAIR_POINT: dict[str, float] = {
    # Metals
    "XAUUSD": 0.1,       # 1 pip = $0.10 for Gold
    "XAGUSD": 0.01,      # 1 pip = $0.01 for Silver
    # Major USD pairs
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCHF": 0.0001,
    "USDCAD": 0.0001,
    "USDJPY": 0.01,
    # JPY crosses
    "GBPJPY": 0.01,
    "EURJPY": 0.01,
    "AUDJPY": 0.01,
    "NZDJPY": 0.01,
    "CADJPY": 0.01,
    "CHFJPY": 0.01,
    # Non-USD non-JPY crosses
    "EURGBP": 0.0001,
    "EURAUD": 0.0001,
    "EURNZD": 0.0001,
    "EURCHF": 0.0001,
    "EURCAD": 0.0001,
    "GBPAUD": 0.0001,
    "GBPNZD": 0.0001,
    "GBPCHF": 0.0001,
    "GBPCAD": 0.0001,
    "AUDNZD": 0.0001,
    "AUDCAD": 0.0001,
    "AUDCHF": 0.0001,
    "NZDCAD": 0.0001,
    "NZDCHF": 0.0001,
    "CADCHF": 0.0001,
}

# ---------------------------------------------------------------------------
# SNR Clustering (masterplan 6.3)
# ---------------------------------------------------------------------------
SNR_CLUSTER_ATR_MULT: float = 0.2       # cluster if distance ≤ 0.2×ATR
SNR_MIN_TOUCHES: int = 2

# Pair-adaptive clustering multiplier (FP-10 M-20)
# Gold/JPY crosses need wider cluster distance due to larger price swings
SNR_CLUSTER_PAIR_MULT: dict[str, float] = {
    "XAUUSD": 0.30,
    "EURUSD": 0.20,
    "GBPJPY": 0.25,
    "USDCHF": 0.20,
    "USDCAD": 0.20,
    "USDJPY": 0.25,
}

# ---------------------------------------------------------------------------
# Supply & Demand (masterplan 6.4)
# ---------------------------------------------------------------------------
SND_BASE_MIN_CANDLES: int = 2
SND_BASE_MAX_CANDLES: int = 6
SND_BASE_AVG_RANGE_ATR: float = 0.6     # avg candle range < 0.6×ATR
SND_DISPLACEMENT_ATR: float = 1.2       # displacement >= 1.2×ATR
SND_DISPLACEMENT_BODY_RATIO: float = 0.6
SND_MAX_ZONES: int = int(os.getenv("SND_MAX_ZONES", "10"))  # max zones per type

# ---------------------------------------------------------------------------
# Order Blocks (masterplan 6.5)
# ---------------------------------------------------------------------------
OB_DISPLACEMENT_ATR: float = 1.0        # looser than SnD

# ---------------------------------------------------------------------------
# Price Action (masterplan 6.8)
# ---------------------------------------------------------------------------
PIN_BAR_MIN_WICK_RATIO: float = float(os.getenv("PIN_BAR_MIN_WICK_RATIO", "2.0"))
ENGULFING_MIN_BODY_RATIO: float = float(os.getenv("ENGULFING_MIN_BODY_RATIO", "0.3"))

# ---------------------------------------------------------------------------
# Liquidity (masterplan 6.6)
# ---------------------------------------------------------------------------
LIQUIDITY_EQ_TOLERANCE_ATR: float = float(
    os.getenv("LIQUIDITY_EQ_TOLERANCE_ATR", "0.15")
)

# ---------------------------------------------------------------------------
# RSI Divergence (masterplan 6.9)
# ---------------------------------------------------------------------------
RSI_DIVERGENCE_LOOKBACK: int = int(os.getenv("RSI_DIVERGENCE_LOOKBACK", "10"))

# ---------------------------------------------------------------------------
# Trendline (masterplan 6.7)
# ---------------------------------------------------------------------------
TRENDLINE_MAX_RAY_BARS: int = int(os.getenv("TRENDLINE_MAX_RAY_BARS", "100"))

# ---------------------------------------------------------------------------
# Market Structure (masterplan 6.2)
# ---------------------------------------------------------------------------
BOS_ATR_BUFFER: float = float(os.getenv("BOS_ATR_BUFFER", "0.05"))  # BOS confirmation buffer

# ---------------------------------------------------------------------------
# Scoring thresholds (masterplan Section 7)
# ---------------------------------------------------------------------------
# CON-20: Canonical scoring threshold lives in strategy_rules.MIN_CONFLUENCE_SCORE.
# This setting is kept for convenience: main.py / orchestrator use it for the
# quick "should we publish?" gate.  Both MUST have the same value.
MIN_SCORE_FOR_TRADE: int = 5
MIN_CONFIDENCE: float = 0.6
HYSTERESIS_CANCEL_SCORE: int = 3   # cancel only if score drops below 3

# ---------------------------------------------------------------------------
# State Machine intervals in seconds (masterplan Section 11)
# ---------------------------------------------------------------------------
STATE_INTERVALS: dict[str, int] = {
    "SCANNING": 0,           # triggered by scheduler
    "WATCHING": 30 * 60,     # 30 min
    "APPROACHING": 10 * 60,  # 10 min
    "TRIGGERED": 0,          # immediate
    "ACTIVE": 15 * 60,       # 15 min
    "CLOSED": 0,             # one-shot
}

# ---------------------------------------------------------------------------
# Smart Voting (masterplan 2.1)
# ---------------------------------------------------------------------------
VOTING_THRESHOLD_HIGH: int = 9   # score ≥ 9 → 1× run, publish directly
VOTING_THRESHOLD_LOW: int = 5    # score 5-8 → 3× voting
VOTING_RUNS: int = 3

# ---------------------------------------------------------------------------
# Trade Management (masterplan Section 13)
# ---------------------------------------------------------------------------
SL_ATR_MULTIPLIER: float = 1.5          # SL = swing ± 1.5×ATR
BREAKEVEN_TRIGGER_RR: float = 1.0       # move to BE after 1×risk profit
TRAIL_TRIGGER_RR: float = 1.5           # start trailing after 1.5×risk
MIN_RR: float = 1.5                     # minimum risk:reward

# ---------------------------------------------------------------------------
# Correlation Groups (masterplan Section 10)
# ---------------------------------------------------------------------------
CORRELATION_GROUPS: dict[str, list[str]] = {
    "USD_MAJOR": ["EURUSD", "USDCHF", "USDCAD"],
    "JPY_CROSS": ["GBPJPY", "USDJPY"],
    "GOLD_USD": ["XAUUSD"],  # Gold correlates negatively with USD strength
}

# ---------------------------------------------------------------------------
# DXY / Index Correlation Gate (masterplan §6.10, M-19)
# ---------------------------------------------------------------------------
# Feature flag: when False, dxy_relevance_score() returns neutral immediately.
# Enabled now that synthetic DXY is computed from OANDA component pairs.
DXY_GATE_ENABLED: bool = _env_bool("DXY_GATE_ENABLED", True)
# Base window for rolling Pearson correlation (M-18: adaptive adjusts ±).
DXY_DEFAULT_WINDOW: int = int(os.getenv("DXY_DEFAULT_WINDOW", "48"))

# ---------------------------------------------------------------------------
# Synthetic DXY — ICE US Dollar Index formula components
# ---------------------------------------------------------------------------
# Official ICE DXY formula:
#   DXY = 50.14348112 × EURUSD^(-0.576) × USDJPY^(0.136) × GBPUSD^(-0.119)
#                     × USDCAD^(0.091) × USDSEK^(0.042) × USDCHF^(0.036)
#
# Each entry: (oanda_pair, exponent, is_inverted)
#   is_inverted=True for pairs quoted as XXX/USD (EUR/USD, GBP/USD)
#   because the formula uses USD/XXX convention internally.
DXY_ICE_CONSTANT: float = 50.14348112
DXY_COMPONENT_PAIRS: list[tuple[str, float, bool]] = [
    ("EURUSD", -0.576, False),   # EUR/USD — largest weight
    ("USDJPY",  0.136, False),   # USD/JPY
    ("GBPUSD", -0.119, False),   # GBP/USD
    ("USDCAD",  0.091, False),   # USD/CAD
    ("USDSEK",  0.042, False),   # USD/SEK
    ("USDCHF",  0.036, False),   # USD/CHF
]
# Candle count for DXY computation — needs enough bars for correlation window
DXY_CANDLE_COUNT: int = int(os.getenv("DXY_CANDLE_COUNT", "200"))

# ---------------------------------------------------------------------------
# Cooldown after invalidation (masterplan Section 12)
# ---------------------------------------------------------------------------
COOLDOWN_MINUTES: int = 30

# ---------------------------------------------------------------------------
# Lifecycle cooldown after trade close (prevent same-pair reopen)
# Separate from COOLDOWN_MINUTES which is state machine cancellation cooldown.
# ---------------------------------------------------------------------------
LIFECYCLE_COOLDOWN_MINUTES: int = 5

# ---------------------------------------------------------------------------
# Per-pair price sanity thresholds (masterplan §13)
# If plan entry deviates more than this % from real price, recalculate.
# ---------------------------------------------------------------------------
PRICE_SANITY_THRESHOLDS: dict[str, float] = {
    "XAUUSD": 0.005,   # 0.5% for Gold (~$14 at $2800)
    "EURUSD": 0.003,   # 0.3% (~32 pips)
    "GBPJPY": 0.003,
    "USDCHF": 0.003,
    "USDCAD": 0.003,
    "USDJPY": 0.003,
}
PRICE_SANITY_DEFAULT: float = 0.01  # 1% for unknown pairs

# ---------------------------------------------------------------------------
# WhatsApp (go-whatsapp-web-multidevice)
# ---------------------------------------------------------------------------
WHATSAPP_API_URL: str = os.getenv("WHATSAPP_API_URL", "http://localhost:3000")
WHATSAPP_PHONE: str = os.getenv("WHATSAPP_PHONE", "")
WHATSAPP_DEVICE_ID: str = os.getenv("WHATSAPP_DEVICE_ID", "")
WHATSAPP_BASIC_USER: str = os.getenv("WHATSAPP_BASIC_USER", "")
WHATSAPP_BASIC_PASS: str = os.getenv("WHATSAPP_BASIC_PASS", "")

# ---------------------------------------------------------------------------
# Dashboard / WebSocket
# ---------------------------------------------------------------------------
DASHBOARD_WS_TOKEN: str = os.getenv("DASHBOARD_WS_TOKEN", "")  # empty = no auth
DASHBOARD_API_KEY: str = os.getenv("DASHBOARD_API_KEY", "")  # empty = no auth on admin REST
DASHBOARD_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv("DASHBOARD_ALLOWED_ORIGINS", "*").split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Logging (L-45)
# ---------------------------------------------------------------------------
# Valid levels: DEBUG, INFO, WARNING, ERROR, CRITICAL (case-insensitive).
_LOG_LEVEL_RAW: str = os.getenv("LOG_LEVEL", "INFO").upper()
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
LOG_LEVEL: str = _LOG_LEVEL_RAW if _LOG_LEVEL_RAW in _VALID_LOG_LEVELS else "INFO"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_FILE_PATH: str = str(_PROJECT_ROOT / "data" / "forex_agent.db")
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DB_FILE_PATH}")

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Gemini Retry (FIX L-12: extracted from gemini_client module-level)
# ---------------------------------------------------------------------------
GEMINI_MAX_RETRIES: int = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
GEMINI_RETRY_BASE_DELAY: float = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "1.0"))

# ---------------------------------------------------------------------------
# Budget & Cost tracking
# ---------------------------------------------------------------------------
DAILY_BUDGET_USD: float = float(os.getenv("DAILY_BUDGET_USD", "10.0"))
TRADING_MODE: str = os.getenv("TRADING_MODE", "demo")  # "demo" | "real"

# Initial account balance — USC cent account = 2000, standard USD = 10000
INITIAL_BALANCE: float = float(os.getenv("INITIAL_BALANCE", "2000.0"))

# Position sizing runtime defaults
POSITION_SIZING_MODE: str = os.getenv("POSITION_SIZING_MODE", "risk_percent")  # risk_percent | fixed_lot
FIXED_LOT_SIZE: float = float(os.getenv("FIXED_LOT_SIZE", "0.01"))
DRAWDOWN_GUARD_ENABLED: bool = _env_bool("DRAWDOWN_GUARD_ENABLED", True)

# Entry execution guard
# Trade can open only when current price is inside entry zone ± buffer (in pips).
ENTRY_ZONE_EXECUTION_BUFFER_PIPS: float = float(
    os.getenv("ENTRY_ZONE_EXECUTION_BUFFER_PIPS", "0.0")
)

# Active-trade revalidation (periodic setup validity checks)
ACTIVE_REVALIDATION_ENABLED: bool = _env_bool("ACTIVE_REVALIDATION_ENABLED", True)
ACTIVE_REVALIDATION_INTERVAL_MINUTES: int = int(os.getenv("ACTIVE_REVALIDATION_INTERVAL_MINUTES", "90"))

# Default TTL for pending setups when none specified (masterplan §6)
PENDING_SETUP_DEFAULT_TTL_HOURS: float = float(os.getenv("PENDING_SETUP_DEFAULT_TTL_HOURS", "4.0"))

# Challenge mode helpers
# Challenge mode helpers (L-42 documented)
# Used by production_lifecycle._apply_challenge_mode("challenge_cent").
#   LOT_MULTIPLIER  — fixed lot size for cent account (default 0.01 lot)
#   SL_MULTIPLIER   — widen SL by this factor (1.5× for cent volatility)
#   TP_MULTIPLIER   — widen TP by this factor (symmetrical to SL)
# Override via .env  e.g. CHALLENGE_CENT_SL_MULTIPLIER=2.0
CHALLENGE_CENT_LOT_MULTIPLIER: float = float(os.getenv("CHALLENGE_CENT_LOT_MULTIPLIER", "0.01"))
CHALLENGE_CENT_SL_MULTIPLIER: float = float(os.getenv("CHALLENGE_CENT_SL_MULTIPLIER", "1.5"))
CHALLENGE_CENT_TP_MULTIPLIER: float = float(os.getenv("CHALLENGE_CENT_TP_MULTIPLIER", "1.5"))

# ---------------------------------------------------------------------------
# Strategy mode selection priority (masterplan §5.3 — FIX M-11)
# ---------------------------------------------------------------------------
# Extracted from system_prompt.py so the priority list is configurable.
# Order matters — first matching mode is selected.
MODE_SELECTION_PRIORITY: list[dict] = [
    {
        "mode": "index_correlation",
        "enabled": _env_bool("MODE_INDEX_CORRELATION_ENABLED", True),
        "note": "Synthetic DXY from OANDA component pairs",
    },
    {
        "mode": "sniper_confluence",
        "enabled": True,
        "note": "Trendline valid + SnD zone confluence",
    },
    {
        "mode": "scalping_channel",
        "enabled": True,
        "note": "Market sideways + flag/channel pattern",
    },
]

# ---------------------------------------------------------------------------
# Operating hours WIB → UTC+7 (masterplan 2.1)
# ---------------------------------------------------------------------------
TRADING_START_HOUR_WIB: int = 14   # 14:00 WIB = 07:00 UTC
TRADING_END_HOUR_WIB: int = 2      # 02:00 WIB next day = 19:00 UTC
