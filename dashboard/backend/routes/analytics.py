"""
dashboard/backend/routes/analytics.py — Analytics endpoints for Dashboard V3.

Provides:
  - GET /api/analytics/summary        — Win rate, avg RR, P/L, profit factor
  - GET /api/analytics/performance    — Daily returns with cumulative
  - GET /api/analytics/by-strategy    — Strategy breakdown
  - GET /api/analytics/by-pair        — Pair breakdown

Computes from DB trade data. Does NOT modify core trading system.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, Depends

from dashboard.backend.routes.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# Will be set by main.py on startup
_repo = None


def set_repo(repo):
    global _repo
    _repo = repo


def _period_cutoff(period: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if period == "7d":
        return now - timedelta(days=7)
    elif period == "30d":
        return now - timedelta(days=30)
    return None  # "all"


async def _get_closed_trades(period: str):
    """Load closed trades from DB, filtered by period."""
    if not _repo:
        return []
    try:
        trades = await _repo.list_trades(limit=2000)
        cutoff = _period_cutoff(period)
        if cutoff:
            trades = [t for t in trades if t.closed_at and t.closed_at >= cutoff]
        return trades
    except Exception as exc:
        logger.error("Analytics trade load failed: %s", exc)
        return []


@router.get("/summary")
async def analytics_summary(period: str = Query("all"), _user: str = Depends(require_auth)) -> dict:
    trades = await _get_closed_trades(period)
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "win_rate": 0.0, "avg_rr": 0.0, "total_pnl": 0.0, "profit_factor": 0.0,
        }

    wins = [t for t in trades if t.result in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT")]
    losses = [t for t in trades if t.result in ("SL_HIT", "TIMEOUT_LOSS")]
    be = [t for t in trades if t.result in ("BREAKEVEN", "TIMEOUT_BE")]

    total_pnl = sum(t.demo_pnl or 0 for t in trades)
    gross_profit = sum(t.demo_pnl or 0 for t in trades if (t.demo_pnl or 0) > 0)
    gross_loss = abs(sum(t.demo_pnl or 0 for t in trades if (t.demo_pnl or 0) < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0

    rr_values = [t.rr_achieved for t in trades if t.rr_achieved is not None]
    avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0.0

    total = len(trades)
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "win_rate": len(wins) / total if total > 0 else 0.0,
        "avg_rr": round(avg_rr, 2),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
    }


@router.get("/performance")
async def analytics_performance(period: str = Query("all"), _user: str = Depends(require_auth)) -> list[dict]:
    trades = await _get_closed_trades(period)
    if not trades:
        return []

    # Group by date
    by_date: dict[str, list] = defaultdict(list)
    for t in sorted(trades, key=lambda x: x.closed_at or datetime.min.replace(tzinfo=timezone.utc)):
        if t.closed_at:
            date_key = t.closed_at.strftime("%Y-%m-%d")
            by_date[date_key].append(t)

    result = []
    cumulative = 0.0
    for date_key in sorted(by_date.keys()):
        day_trades = by_date[date_key]
        day_pnl = sum(t.demo_pnl or 0 for t in day_trades)
        cumulative += day_pnl
        result.append({
            "date": date_key,
            "pnl": round(day_pnl, 2),
            "cumulative": round(cumulative, 2),
            "trades": len(day_trades),
        })

    return result


@router.get("/by-strategy")
async def analytics_by_strategy(period: str = Query("all"), _user: str = Depends(require_auth)) -> list[dict]:
    trades = await _get_closed_trades(period)
    if not trades:
        return []

    by_strat: dict[str, list] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy_mode or "unknown"].append(t)

    result = []
    for strategy, strat_trades in by_strat.items():
        wins = [t for t in strat_trades if t.result in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT")]
        rr_vals = [t.rr_achieved for t in strat_trades if t.rr_achieved is not None]
        net_pnl = sum(t.demo_pnl or 0 for t in strat_trades)
        result.append({
            "strategy": strategy,
            "trades": len(strat_trades),
            "win_rate": len(wins) / len(strat_trades) if strat_trades else 0.0,
            "net_pnl": round(net_pnl, 2),
            "avg_rr": round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else 0.0,
        })

    return sorted(result, key=lambda x: -x["trades"])


@router.get("/by-pair")
async def analytics_by_pair(period: str = Query("all"), _user: str = Depends(require_auth)) -> list[dict]:
    trades = await _get_closed_trades(period)
    if not trades:
        return []

    by_pair: dict[str, list] = defaultdict(list)
    for t in trades:
        by_pair[t.pair].append(t)

    result = []
    for pair, pair_trades in by_pair.items():
        wins = [t for t in pair_trades if t.result in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT")]
        net_pnl = sum(t.demo_pnl or 0 for t in pair_trades)
        result.append({
            "pair": pair,
            "trades": len(pair_trades),
            "win_rate": len(wins) / len(pair_trades) if pair_trades else 0.0,
            "net_pnl": round(net_pnl, 2),
        })

    return sorted(result, key=lambda x: -abs(x["net_pnl"]))
