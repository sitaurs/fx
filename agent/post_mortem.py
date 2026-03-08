"""
agent/post_mortem.py — Automatic trade post-mortem analysis.

After every trade closes (CLOSED state), this module generates a structured
analysis of what worked, what didn't, and lessons learned.

For SL_HIT trades, additional cause analysis with parameter adjustment
suggestions is generated.

Usage::

    pm = PostMortemGenerator()
    report = pm.generate(trade, market_context)
    # report is a PostMortemReport dataclass / JSON-serializable dict

Reference: masterplan.md §14 (Win/Loss Post-Mortem)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SL Hit cause codes (masterplan §14 table)
# ---------------------------------------------------------------------------

SL_CAUSE_CODES = {
    "sweep_extended": {
        "description": "Sweep lanjut jadi breakout",
        "mitigation": "Tunggu 2 candle reclaim sebelum entry",
    },
    "news_spike": {
        "description": "Volatilitas news tidak terprediksi",
        "mitigation": "Tambah news filter, jangan entry 30m sebelum news",
    },
    "zone_weak": {
        "description": "Zona minor dianggap major",
        "mitigation": "Tighten scoring — minimum score 7 untuk trade",
    },
    "choch_premature": {
        "description": "ChoCh belum confirmed saat entry",
        "mitigation": "Min 2 candle confirm setelah CHOCH",
    },
    "counter_htf_ignored": {
        "description": "H4 bias tidak direspek",
        "mitigation": "Naikkan penalty HTF conflict di scoring",
    },
    "sl_too_tight": {
        "description": "SL < 1×ATR, terkena noise",
        "mitigation": "Enforce minimum SL = 1×ATR",
    },
    "timing_late": {
        "description": "Entry terlalu telat, sudah jauh dari zona",
        "mitigation": "TTL lebih ketat — kurangi ttl_hours",
    },
    "correlation_miss": {
        "description": "Pair bergerak karena DXY, bukan pair sendiri",
        "mitigation": "Enforce DXY gate — pastikan correlation check",
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SLCauseAnalysis:
    """Detailed analysis when SL is hit."""

    primary_cause: str = "unknown"           # code from SL_CAUSE_CODES
    secondary_causes: list[str] = field(default_factory=list)  # M-34
    explanation: str = ""
    what_was_missed: str = ""
    # L-58: Typed as Optional mapping with known keys:
    #   {"setting": str, "current": str, "suggested": str}
    suggested_param_change: Optional[dict[str, str]] = None


@dataclass
class PostMortemReport:
    """Complete post-mortem analysis for a closed trade."""

    trade_id: str
    pair: str
    direction: str
    entry_price: float
    exit_price: float
    result: str              # "TP1_HIT", "SL_HIT", etc.
    pips: float
    duration_minutes: int
    strategy_mode: str
    confluence_score: int
    voting_confidence: float

    what_worked: list[str] = field(default_factory=list)
    what_didnt_work: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)

    sl_cause: Optional[SLCauseAnalysis] = None

    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


# ---------------------------------------------------------------------------
# Market context (passed to generator)
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """Market conditions at trade close for post-mortem analysis."""

    atr_at_entry: float = 0.0
    atr_at_close: float = 0.0
    htf_bias_at_entry: str = ""       # bullish/bearish/range
    htf_bias_at_close: str = ""
    structure_intact: bool = True      # was H1 structure still valid?
    news_during_trade: bool = False
    sl_was_moved_be: bool = False
    sl_trail_applied: bool = False
    entry_zone_type: str = ""          # supply/demand/snr
    choch_occurred: bool = False       # CHOCH against direction during trade?
    sweep_at_entry: bool = False       # was there a sweep before entry?


# ---------------------------------------------------------------------------
# PostMortemGenerator
# ---------------------------------------------------------------------------

class PostMortemGenerator:
    """Generate structured post-mortem reports for closed trades.

    Logic is rule-based (deterministic) — no LLM needed.
    The orchestrator can optionally ask Gemini to enhance the report
    with deeper reasoning, but the base analysis is pure Python.
    """

    def generate(
        self,
        trade_id: str,
        pair: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        stop_loss: float,
        take_profit_1: float,
        result: str,
        pips: float,
        duration_minutes: int,
        strategy_mode: str = "",
        confluence_score: int = 0,
        voting_confidence: float = 0.0,
        context: MarketContext | None = None,
    ) -> PostMortemReport:
        """Generate post-mortem for a single trade."""

        ctx = context or MarketContext()

        report = PostMortemReport(
            trade_id=trade_id,
            pair=pair,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            result=result,
            pips=pips,
            duration_minutes=duration_minutes,
            strategy_mode=strategy_mode,
            confluence_score=confluence_score,
            voting_confidence=voting_confidence,
        )

        # Analyze what worked / didn't work
        # M-33: TRAIL_PROFIT is a win — trailing stop exited in profit.
        if result in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT"):
            self._analyze_win(report, ctx)
        elif result == "SL_HIT":
            self._analyze_loss(report, ctx, stop_loss, entry_price, direction)
        elif result == "BE_HIT":
            self._analyze_breakeven(report, ctx)
        elif result == "MANUAL_CLOSE":
            self._analyze_manual(report, ctx, pips)
        else:
            report.lessons.append("Trade cancelled before completion")

        # Universal lessons
        self._add_universal_lessons(report, ctx, duration_minutes)

        logger.info(
            "Post-mortem generated: %s %s %s (%.1f pips)",
            trade_id, pair, result, pips,
        )
        return report

    # -- Win analysis -------------------------------------------------------

    def _analyze_win(self, report: PostMortemReport, ctx: MarketContext) -> None:
        """Analyze a winning trade."""
        if ctx.htf_bias_at_entry:
            expected = "bullish" if report.direction == "buy" else "bearish"
            if ctx.htf_bias_at_entry == expected:
                report.what_worked.append(
                    f"HTF bias ({ctx.htf_bias_at_entry}) aligned with trade direction"
                )

        if ctx.sweep_at_entry:
            report.what_worked.append("Liquidity sweep identified before entry")

        if ctx.sl_was_moved_be:
            report.what_worked.append(
                "SL+ to breakeven protected during minor pullback"
            )

        if report.confluence_score >= 9:
            report.what_worked.append(
                f"High confluence score ({report.confluence_score}) validated"
            )

        if report.result == "TP1_HIT" and report.pips > 0:
            report.what_worked.append("TP1 reached — setup thesis confirmed")
            if ctx.sl_trail_applied:
                report.lessons.append(
                    "Trail stop was active — check if TP2 was reachable"
                )
            else:
                report.lessons.append(
                    "Consider partial close + trail for TP2 capture"
                )

        if report.result == "TP2_HIT":
            report.what_worked.append("Full TP2 reached — excellent execution")
            report.lessons.append(
                "Winning patience — held through TP1 to TP2 was correct"
            )

        # M-33: TRAIL_PROFIT-specific analysis
        if report.result == "TRAIL_PROFIT":
            report.what_worked.append(
                "Trailing stop locked in profits above breakeven"
            )
            if ctx.sl_trail_applied:
                report.what_worked.append(
                    "Trail mechanism worked as designed — profit protected"
                )
            report.lessons.append(
                "Check if TP1/TP2 could have been reached "
                "before trail triggered exit"
            )

    # -- Loss analysis ------------------------------------------------------

    def _analyze_loss(
        self,
        report: PostMortemReport,
        ctx: MarketContext,
        stop_loss: float,
        entry_price: float,
        direction: str,
    ) -> None:
        """Analyze a losing trade and determine SL cause."""
        report.what_didnt_work.append("Stop loss hit — trade invalidated")

        # Determine cause
        cause = SLCauseAnalysis()

        # M-34: Collect ALL applicable causes, not just the first.
        # The first matched cause becomes primary; others are secondary.
        _detected: list[str] = []

        # Check common loss causes
        if ctx.news_during_trade:
            _detected.append("news_spike")
            report.what_didnt_work.append("News spike during trade")

        if ctx.choch_occurred:
            _detected.append("counter_htf_ignored")
            report.what_didnt_work.append("H1 CHOCH ignored — should have closed earlier")

        if not ctx.structure_intact:
            _detected.append("sweep_extended")

        if ctx.atr_at_entry > 0:
            risk = abs(entry_price - stop_loss)
            if risk < ctx.atr_at_entry:
                _detected.append("sl_too_tight")
                cause.suggested_param_change = {
                    "setting": "SL_ATR_MULTIPLIER",
                    "current": "< 1.0×ATR",
                    "suggested": "1.5×ATR minimum",
                }
                report.what_didnt_work.append("SL was too tight — noise hit it")

        # HTF alignment check
        if ctx.htf_bias_at_entry:
            expected = "bullish" if direction == "buy" else "bearish"
            if ctx.htf_bias_at_entry != expected:
                if "counter_htf_ignored" not in _detected:
                    _detected.append("counter_htf_ignored")
                report.what_didnt_work.append(
                    f"Counter-HTF trade: bias={ctx.htf_bias_at_entry}, "
                    f"traded={direction}"
                )

        # Assign primary + secondary causes (M-34)
        if _detected:
            cause.primary_cause = _detected[0]
            cause.secondary_causes = _detected[1:]
        else:
            cause.primary_cause = "sweep_extended"

        # Fill explanation from primary cause code
        _EXPLANATIONS = {
            "news_spike": "Major news event caused unexpected volatility",
            "counter_htf_ignored": "H1/H4 structure changed against trade direction",
            "sweep_extended": "Price swept entry zone and continued without reclaim",
            "sl_too_tight": "SL was tighter than 1×ATR — market noise triggered it",
        }
        cause.explanation = _EXPLANATIONS.get(
            cause.primary_cause, "Standard SL hit — market moved against position"
        )

        # Add mitigation from cause code table
        code_info = SL_CAUSE_CODES.get(cause.primary_cause, {})
        if code_info:
            report.lessons.append(
                f"Mitigation: {code_info.get('mitigation', 'Review setup')}"
            )

        report.sl_cause = cause

    # -- Breakeven analysis -------------------------------------------------

    def _analyze_breakeven(
        self, report: PostMortemReport, ctx: MarketContext
    ) -> None:
        """Analyze a breakeven trade."""
        report.what_worked.append(
            "SL+ to breakeven protected capital — no loss taken"
        )
        report.what_didnt_work.append(
            "Trade didn't reach TP — momentum faded after initial move"
        )
        report.lessons.append(
            "Consider if SL+ was moved too early — "
            "check if price revisited entry zone before moving to TP"
        )

    # -- Manual close analysis ----------------------------------------------

    def _analyze_manual(
        self, report: PostMortemReport, ctx: MarketContext, pips: float
    ) -> None:
        """Analyze a manually closed trade."""
        if pips > 0:
            report.what_worked.append(
                f"Manual close in profit ({pips:.1f} pips)"
            )
            if ctx.choch_occurred:
                report.what_worked.append(
                    "Correct decision to close on structure break"
                )
        else:
            report.what_didnt_work.append(
                f"Manual close at loss ({pips:.1f} pips)"
            )
            report.lessons.append(
                "Review if manual close was justified or emotional"
            )

    # -- Universal lessons --------------------------------------------------

    def _add_universal_lessons(
        self,
        report: PostMortemReport,
        ctx: MarketContext,
        duration_minutes: int,
    ) -> None:
        """Add lessons that apply to all trade types."""
        # Confluence score analysis
        if report.confluence_score < 7 and report.result == "SL_HIT":
            report.lessons.append(
                f"Low confluence score ({report.confluence_score}) "
                f"correlated with loss — enforce min score 7"
            )

        if report.voting_confidence < 0.7 and report.result == "SL_HIT":
            report.lessons.append(
                f"Low voting confidence ({report.voting_confidence:.0%}) "
                f"— consider requiring 70%+ for entry"
            )

        # Duration analysis
        if duration_minutes < 15:
            report.lessons.append(
                "Very short trade duration — check if entry timing was off"
            )
        elif duration_minutes > 480:
            report.lessons.append(
                "Long duration (>8h) — consider if setup was still valid"
            )

        # Strategy-specific notes
        if report.strategy_mode == "scalping_channel":
            if duration_minutes > 120:
                report.lessons.append(
                    "Scalp trade lasted >2h — may indicate wrong mode selection"
                )
