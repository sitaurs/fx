"""
dashboard/backend/main.py — FastAPI dashboard backend v2.0.

Provides:
  - REST API  : /api/portfolio, /api/portfolio/equity, /api/system/status,
                /api/trades/{id}, /api/analysis/live, etc.
  - WebSocket : /ws for real-time state-change broadcasts.
  - Static    : Serves dashboard frontend from dashboard/frontend/

Reference: DASHBOARD_PLAN.md, masterplan.md §21.2
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import (
    DASHBOARD_WS_TOKEN,
    DASHBOARD_API_KEY,
    DASHBOARD_ALLOWED_ORIGINS,
    TRADING_MODE,
    CORRELATION_GROUPS,
    MVP_PAIRS,
    PAIR_POINT,
)


# ---------------------------------------------------------------------------
# API Key authentication dependency (FIX C-05)
# ---------------------------------------------------------------------------


async def require_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> str:
    """Dependency that enforces API key auth on admin endpoints.

    If DASHBOARD_API_KEY is not configured (empty), auth is bypassed
    for backward compatibility — but a warning is logged on first call.
    """
    if not DASHBOARD_API_KEY:
        # No key configured — allow (dev/migration mode)
        return "no-auth-configured"
    if x_api_key and x_api_key == DASHBOARD_API_KEY:
        return x_api_key
    raise HTTPException(status_code=401, detail="Invalid or missing API key")

logger = logging.getLogger(__name__)


class SystemConfigPatch(BaseModel):
    mode: Optional[str] = None
    challenge_mode: Optional[str] = None
    position_sizing_mode: Optional[str] = None
    fixed_lot_size: Optional[float] = Field(default=None, ge=0.0)
    risk_per_trade: Optional[float] = Field(default=None, ge=0.0)
    drawdown_guard_enabled: Optional[bool] = None
    max_daily_drawdown: Optional[float] = Field(default=None, ge=0.0)
    max_total_drawdown: Optional[float] = Field(default=None, ge=0.0)
    max_concurrent_trades: Optional[int] = Field(default=None, ge=1)
    active_revalidation_enabled: Optional[bool] = None
    active_revalidation_interval_minutes: Optional[int] = Field(default=None, ge=15)


class BalanceSetRequest(BaseModel):
    balance: float = Field(ge=0.0)
    reset_hwm: bool = True
    reset_daily_start: bool = True
    update_initial_balance: bool = True


class ManualCloseRequest(BaseModel):
    reason: str = Field(default="Manual close from dashboard")

# ---------------------------------------------------------------------------
# In-memory MVP store
# ---------------------------------------------------------------------------

_MAX_ANALYSES = 50
_MAX_TRADES = 500

_analyses: dict[str, dict] = {}
_trades: list[dict] = []
_daily_stats: dict[str, Any] = {}
_repo: Optional[Any] = None
_lifecycle: Optional[Any] = None
_scheduler: Optional[Any] = None
_start_time: float = time.time()

# Equity history: list of {date, balance, hwm}
_equity_history: list[dict] = []

# Event log: ring buffer for Live Events (survives page refresh while server runs)
_events: deque[dict] = deque(maxlen=200)

# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Track active WebSocket connections and broadcast updates."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS connected  total=%d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._connections.remove(ws)
        except ValueError:
            pass
        logger.info("WS disconnected  total=%d", len(self._connections))

    async def broadcast(self, message: dict) -> None:
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass

    @property
    def active_count(self) -> int:
        return len(self._connections)


ws_manager = ConnectionManager()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Forex Dashboard",
    version="2.1.0",
    description="Pro Fintech Dashboard for AI Forex Trading Agent",
)

# L-10: Track PM2 restart count (incremented on each on_startup).
_pm2_restart_count = 0

app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static files — serve dashboard frontend
# ---------------------------------------------------------------------------

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
async def _mount_static():
    global _pm2_restart_count
    _pm2_restart_count += 1  # L-10: track restart cycles
    if _FRONTEND_DIR.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=str(_FRONTEND_DIR)),
            name="frontend",
        )
        logger.info("Frontend mounted from %s", _FRONTEND_DIR)


# ---------------------------------------------------------------------------
# Root → serve index.html
# ---------------------------------------------------------------------------


@app.get("/")
async def dashboard_ui():
    index = _FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index), media_type="text/html")
    return JSONResponse(
        status_code=503,
        content={"detail": "Dashboard frontend not found. Place index.html in dashboard/frontend/."},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    global _pm2_restart_count
    return {
        "status": "ok",
        "version": app.version,          # L-02
        "restart_count": _pm2_restart_count,  # L-10
        "ws_connections": ws_manager.active_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": int(time.time() - _start_time),
    }


# ---------------------------------------------------------------------------
# Event storage helper + endpoint
# ---------------------------------------------------------------------------


def _store_event(msg: dict) -> None:
    """Store a broadcast message into the server-side event ring buffer."""
    data = msg.get("data", {})
    pair = (data.get("pair") or "—").upper() if isinstance(data, dict) else "—"
    evt_type = msg.get("type", "UNKNOWN")

    # Build human-readable summary
    summary = ""
    if evt_type == "ANALYSIS_UPDATE":
        # FIX: data["plan"] can be None (not missing), so .get("plan", {}) returns None
        _plan = data.get("plan") if isinstance(data, dict) else None
        s = _plan.get("primary_setup", {}) if isinstance(_plan, dict) else {}
        summary = f"{s.get('direction', '?')} score={s.get('confluence_score', '?')}" if s else (data.get("error") or "no plan")
    elif evt_type == "STATE_CHANGE":
        summary = f"{data.get('old_state', '?')} → {data.get('new_state', '?')}"
    elif evt_type == "TRADE_CLOSED":
        summary = f"{data.get('result', '?')} {(data.get('pips') or 0):.1f} pips ${(data.get('pnl') or 0):.2f}"
    elif evt_type == "PORTFOLIO_UPDATE":
        summary = f"bal=${data.get('balance', 0)} float=${data.get('floating_pnl', 0)}"

    _events.appendleft({
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "type": evt_type,
        "pair": pair,
        "summary": summary,
    })


@app.get("/api/events")
async def get_events(limit: int = Query(100, ge=1, le=200)) -> list[dict]:
    """Return stored events (newest first). Survives page refresh."""
    return list(_events)[:limit]


# ===================================================================
# NEW API ENDPOINTS (DASHBOARD_PLAN)
# ===================================================================


# ---------------------------------------------------------------------------
# Portfolio endpoint (§5.1)
# ---------------------------------------------------------------------------


@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    lc = _lifecycle

    if not lc:
        return {
            "balance": 10000.0,
            "initial_balance": 10000.0,
            "high_water_mark": 10000.0,
            "daily_start_balance": 10000.0,
            "floating_pnl": 0.0,
            "effective_balance": 10000.0,
            "daily_drawdown_pct": 0.0,
            "total_drawdown_pct": 0.0,
            "max_daily_drawdown": 0.05,
            "max_total_drawdown": 0.15,
            "is_halted": False,
            "halt_reason": "",
            "mode": TRADING_MODE,
            "active_trades": [],
            "active_count": 0,
            "max_concurrent": 2,
            "correlation_status": _build_correlation_status([]),
            "challenge_mode": "none",
            "position_sizing_mode": "risk_percent",
            "fixed_lot_size": 0.01,
            "drawdown_guard_enabled": True,
            "active_revalidation_enabled": True,
            "active_revalidation_interval_minutes": 90,
            "runtime_config": {},
        }

    # FIX H-15: Use async price fetch to avoid blocking the event loop
    from agent.production_lifecycle import get_current_price_async
    price_cache: dict[str, float] = {}
    for pair in list(getattr(lc, "_active", {}).keys()):
        try:
            price_cache[pair] = await get_current_price_async(pair)
        except Exception:
            pass

    # Build active trades list — now with per-trade floating P/L
    active_trades = []
    total_floating = 0.0
    for pair, (trade, mgr) in getattr(lc, "_active", {}).items():
        try:
            cur_price = price_cache.get(pair)
            # Per-trade floating calculations
            f_pips = 0.0
            f_dollar = 0.0
            rr_cur = 0.0
            if cur_price is not None:
                point = PAIR_POINT.get(pair, 0.0001)
                raw_pnl = (cur_price - trade.entry_price) if trade.direction == "buy" else (trade.entry_price - cur_price)
                f_pips = raw_pnl / point
                risk = abs(trade.entry_price - getattr(trade, "original_sl", trade.stop_loss))
                rr_cur = (raw_pnl / risk) if risk > 0 else 0.0
                if hasattr(lc, "trade_floating_pnl"):
                    f_dollar = lc.trade_floating_pnl(trade, cur_price)
                else:
                    risk_amount = lc.balance * lc.risk_per_trade
                    f_dollar = risk_amount * rr_cur
                total_floating += f_dollar

            active_trades.append({
                "pair": pair,
                "direction": trade.direction,
                "entry_price": trade.entry_price,
                "current_price": cur_price,
                "floating_pips": round(f_pips, 1),
                "floating_dollar": round(f_dollar, 2),
                "rr_current": round(rr_cur, 2),
                "stop_loss": trade.stop_loss,
                "original_sl": getattr(trade, "original_sl", trade.stop_loss),
                "take_profit_1": trade.take_profit_1,
                "take_profit_2": getattr(trade, "take_profit_2", None),
                "entry_zone_low": getattr(trade, "entry_zone_low", None),
                "entry_zone_high": getattr(trade, "entry_zone_high", None),
                "recommended_entry": getattr(trade, "recommended_entry", None),
                "sl_moved_to_be": getattr(trade, "sl_moved_to_be", False),
                "partial_closed": getattr(trade, "partial_closed", False),
                "trail_active": getattr(trade, "trail_active", False),
                "remaining_size": getattr(trade, "remaining_size", 1.0),
                "realized_pnl": getattr(trade, "realized_pnl", 0.0),
                "lot_size": getattr(trade, "lot_size", 0.0),
                "risk_amount": getattr(trade, "risk_amount", 0.0),
                "last_revalidation_at": (
                    trade.last_revalidation_at.isoformat()
                    if getattr(trade, "last_revalidation_at", None)
                    else None
                ),
                "last_revalidation_note": getattr(trade, "last_revalidation_note", ""),
                "opened_at": trade.opened_at.isoformat() if hasattr(trade, "opened_at") and trade.opened_at else None,
                "strategy_mode": getattr(trade, "strategy_mode", "unknown"),
                "confluence_score": getattr(trade, "confluence_score", 0),
                "trade_id": trade.trade_id,
            })
        except Exception as exc:
            logger.error("Error building active trade %s: %s", pair, exc)

    # Drawdown calculations — reuse price_cache to avoid double fetch
    floating = total_floating
    effective_balance = lc.balance + floating
    daily_dd = 0.0
    total_dd = 0.0
    if lc.daily_start_balance > 0:
        daily_dd = max(0, (lc.daily_start_balance - effective_balance) / lc.daily_start_balance)
    if lc.high_water_mark > 0:
        total_dd = max(0, (lc.high_water_mark - effective_balance) / lc.high_water_mark)

    return {
        "balance": round(lc.balance, 2),
        "initial_balance": lc.initial_balance,
        "high_water_mark": round(lc.high_water_mark, 2),
        "daily_start_balance": round(lc.daily_start_balance, 2),
        "floating_pnl": round(floating, 2),
        "effective_balance": round(effective_balance, 2),
        "daily_drawdown_pct": round(daily_dd, 4),
        "total_drawdown_pct": round(total_dd, 4),
        "max_daily_drawdown": lc.max_daily_drawdown,
        "max_total_drawdown": lc.max_total_drawdown,
        "is_halted": lc.is_halted,
        "halt_reason": lc.halt_reason,
        "mode": lc.mode,
        "active_trades": active_trades,
        "active_count": lc.active_count,
        "max_concurrent": lc.max_concurrent_trades,
        "correlation_status": _build_correlation_status(lc.active_pairs),
        "challenge_mode": getattr(lc, "challenge_mode", "none"),
        "position_sizing_mode": getattr(lc, "position_sizing_mode", "risk_percent"),
        "fixed_lot_size": getattr(lc, "fixed_lot_size", 0.01),
        "drawdown_guard_enabled": getattr(lc, "drawdown_guard_enabled", True),
        "active_revalidation_enabled": getattr(lc, "active_revalidation_enabled", True),
        "active_revalidation_interval_minutes": getattr(lc, "active_revalidation_interval_minutes", 90),
        "runtime_config": lc.get_runtime_config() if hasattr(lc, "get_runtime_config") else {},
        "pending_setups": lc._pending.to_dashboard_list() if hasattr(lc, "_pending") else [],
    }


def _build_correlation_status(active_pairs: list[str]) -> dict:
    result = {}
    for group, members in CORRELATION_GROUPS.items():
        active = [p for p in members if p in active_pairs]
        available = [p for p in members if p not in active_pairs]
        result[group] = {"active": active, "available": available}
    return result


# ---------------------------------------------------------------------------
# Equity history endpoint (§5.2)
# ---------------------------------------------------------------------------


@app.get("/api/portfolio/equity")
async def get_equity_history() -> dict:
    if _equity_history:
        return {"points": _equity_history}
    lc = _lifecycle
    if lc:
        now = datetime.now(timezone.utc)
        return {"points": [{
            "date": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": now.isoformat(),
            "label": now.strftime("%m-%d %H:%M"),
            "balance": round(lc.balance, 2),
            "hwm": round(lc.high_water_mark, 2),
        }]}
    return {"points": []}


# ---------------------------------------------------------------------------
# System status endpoint (§5.3)
# ---------------------------------------------------------------------------


@app.get("/api/system/status")
async def get_system_status() -> dict:
    lc = _lifecycle
    sched = _scheduler

    scheduler_jobs = []
    if sched:
        for job in sched.jobs:
            next_run = getattr(job, "next_run_time", None)
            scheduler_jobs.append({
                "name": job.name,
                "id": job.id,
                "next_run": next_run.isoformat() if next_run else None,
            })

    from config.settings import OANDA_API_KEY, GEMINI_API_KEY
    return {
        "mode": lc.mode if lc else TRADING_MODE,
        "is_halted": lc.is_halted if lc else False,
        "halt_reason": lc.halt_reason if lc else "",
        "scheduler_jobs": scheduler_jobs,
        "api_status": {
            "oanda": bool(OANDA_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
        },
        "uptime_seconds": int(time.time() - _start_time),
        "active_orchestrators": len(_analyses),
        "ws_connections": ws_manager.active_count,
        "pairs": MVP_PAIRS,
        "runtime_config": lc.get_runtime_config() if lc and hasattr(lc, "get_runtime_config") else {},
    }


@app.get("/api/system/config")
async def get_system_config() -> dict:
    lc = _lifecycle
    if not lc or not hasattr(lc, "get_runtime_config"):
        return {"success": False, "error": "Lifecycle not initialized"}
    return {"success": True, "config": lc.get_runtime_config()}


@app.patch("/api/system/config")
async def patch_system_config(
    payload: SystemConfigPatch,
    _key: str = Depends(require_api_key),
) -> dict:
    lc = _lifecycle
    if not lc or not hasattr(lc, "update_runtime_config"):
        return {"success": False, "error": "Lifecycle not initialized"}
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"success": True, "config": lc.get_runtime_config()}
    config = await lc.update_runtime_config(updates)
    record_equity_point()
    return {"success": True, "config": config}


@app.post("/api/system/config/reset-default")
async def reset_system_config(
    _key: str = Depends(require_api_key),
) -> dict:
    lc = _lifecycle
    if not lc or not hasattr(lc, "reset_runtime_config"):
        return {"success": False, "error": "Lifecycle not initialized"}
    config = await lc.reset_runtime_config()
    record_equity_point()
    return {"success": True, "config": config}


@app.post("/api/system/balance/set")
async def set_balance(
    payload: BalanceSetRequest,
    _key: str = Depends(require_api_key),
) -> dict:
    lc = _lifecycle
    if not lc or not hasattr(lc, "update_runtime_config"):
        return {"success": False, "error": "Lifecycle not initialized"}
    updates = {
        "balance": payload.balance,
        "reset_hwm": payload.reset_hwm,
        "reset_daily_start": payload.reset_daily_start,
        "update_initial_balance": payload.update_initial_balance,
    }
    config = await lc.update_runtime_config(updates)
    record_equity_point()
    return {"success": True, "config": config}


# ---------------------------------------------------------------------------
# Unhalt endpoint (admin)
# ---------------------------------------------------------------------------


@app.post("/api/system/unhalt")
async def unhalt_system(
    _key: str = Depends(require_api_key),
) -> dict:
    lc = _lifecycle
    if not lc:
        return {"success": False, "error": "Lifecycle not initialized"}
    if not lc.is_halted:
        return {"success": True, "message": "System is not halted"}

    # FIX H-16: Use public method instead of private attribute mutation
    old_reason = lc.halt_reason
    if hasattr(lc, "unhalt"):
        await lc.unhalt()
    else:
        # Fallback for older lifecycle versions
        lc._halted = False
        lc._halt_reason = ""
        await lc.save_state()
    logger.warning("System manually unhalted. Previous: %s", old_reason)
    return {"success": True, "message": f"Unhalted. Was: {old_reason}"}


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------


@app.get("/api/analysis/live")
async def get_live_analysis() -> list[dict]:
    return [
        a for a in _analyses.values()
        if a.get("state") not in ("CLOSED", "CANCELLED")
    ]


@app.get("/api/analysis/{pair}")
async def get_pair_analysis(pair: str) -> dict:
    upper = pair.upper()
    if upper in _analyses:
        return _analyses[upper]
    return JSONResponse(status_code=404, content={"detail": "Pair not found"})


# ---------------------------------------------------------------------------
# Pending setups endpoint
# ---------------------------------------------------------------------------


@app.get("/api/pending-setups")
async def get_pending_setups() -> list[dict]:
    """Return list of pending setups waiting for price to enter zone."""
    lc = _lifecycle
    if not lc or not hasattr(lc, "_pending"):
        return []
    return lc._pending.to_dashboard_list()


# ---------------------------------------------------------------------------
# Trade endpoints
# ---------------------------------------------------------------------------


@app.get("/api/trades")
async def get_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    if _trades:
        return _trades[offset: offset + limit]
    if _repo:
        try:
            db_trades = await _repo.list_trades(limit=limit)
            return [_trade_to_dict(t) for t in db_trades]
        except Exception as exc:
            logger.error("DB trade query failed: %s", exc)
    return []


@app.get("/api/trades/{trade_id}")
async def get_single_trade(trade_id: str) -> dict:
    for t in _trades:
        if t.get("trade_id") == trade_id:
            return t
    if _repo:
        try:
            trade = await _repo.get_trade(trade_id)
            if trade:
                result = _trade_to_dict(trade)
                if trade.post_mortem_json:
                    try:
                        result["post_mortem"] = json.loads(trade.post_mortem_json)
                    except Exception:
                        result["post_mortem"] = None
                return result
        except Exception as exc:
            logger.error("DB single trade query failed: %s", exc)
    return JSONResponse(status_code=404, content={"detail": "Trade not found"})


@app.post("/api/positions/{trade_id}/close")
async def manual_close_position(
    trade_id: str,
    payload: ManualCloseRequest,
    _key: str = Depends(require_api_key),
) -> dict:
    lc = _lifecycle
    if not lc or not hasattr(lc, "manual_close_trade"):
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Lifecycle not initialized"},
        )
    try:
        result = await lc.manual_close_trade(
            trade_id=trade_id,
            reason=payload.reason,
        )
        return {"success": True, "trade": result}
    except KeyError:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Active trade not found"},
        )
    except Exception as exc:
        logger.error("Manual close failed for %s: %s", trade_id, exc)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


def _trade_to_dict(t) -> dict:
    return {
        "trade_id": t.trade_id,
        "pair": t.pair,
        "direction": t.direction,
        "strategy_mode": t.strategy_mode,
        "mode": t.mode,
        "entry_price": t.entry_price,
        "stop_loss": t.stop_loss,
        "take_profit_1": t.take_profit_1,
        "take_profit_2": t.take_profit_2,
        "exit_price": t.exit_price,
        "result": t.result,
        "pips": t.pips,
        "rr_achieved": t.rr_achieved,
        "duration_minutes": t.duration_minutes,
        "confluence_score": t.confluence_score,
        "sl_was_moved_be": t.sl_was_moved_be,
        "sl_trail_applied": t.sl_trail_applied,
        "final_sl": t.final_sl,
        "demo_pnl": t.demo_pnl,
        "demo_balance_after": t.demo_balance_after,
        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


# ---------------------------------------------------------------------------
# Stats endpoints
# ---------------------------------------------------------------------------


@app.get("/api/stats/daily")
async def get_daily_stats() -> dict:
    return _daily_stats or {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_scans": 0,
        "setups_found": 0,
        "trades_taken": 0,
        "total_pips": 0.0,
    }


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
) -> None:
    if DASHBOARD_WS_TOKEN and token != DASHBOARD_WS_TOKEN:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Public helpers (called by main.py / orchestrator / scheduler)
# ---------------------------------------------------------------------------


def set_repo(repo: Any) -> None:
    global _repo
    _repo = repo


async def load_equity_from_db() -> None:
    """Restore equity history from DB on startup."""
    global _equity_history
    if not _repo:
        return
    try:
        points = await _repo.load_equity_history(limit=500)
        if points:
            _equity_history.clear()
            _equity_history.extend(points)
            logger.info("Restored %d equity points from DB", len(points))
    except Exception as exc:
        logger.warning("Failed to load equity history from DB: %s", exc)


def set_lifecycle(lifecycle: Any) -> None:
    global _lifecycle
    _lifecycle = lifecycle


def set_scheduler(scheduler: Any) -> None:
    global _scheduler
    _scheduler = scheduler


def record_equity_point() -> None:
    lc = _lifecycle
    if not lc:
        return
    now = datetime.now(timezone.utc)
    balance = round(lc.balance, 2)
    hwm = round(lc.high_water_mark, 2)
    _equity_history.append({
        "date": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": now.isoformat(),
        "label": now.strftime("%m-%d %H:%M"),
        "balance": balance,
        "hwm": hwm,
    })
    while len(_equity_history) > 500:
        _equity_history.pop(0)
    # Persist to DB in background — FIX M-08: add error handling to fire-and-forget
    if _repo:
        async def _safe_save_equity():
            try:
                await _repo.save_equity_point(balance, hwm)
            except Exception as exc:
                logger.warning("Equity snapshot persist failed: %s", exc)
        asyncio.ensure_future(_safe_save_equity())


async def push_analysis_update(pair: str, data: dict) -> None:
    _analyses[pair.upper()] = data
    while len(_analyses) > _MAX_ANALYSES:
        oldest_key = next(iter(_analyses))
        del _analyses[oldest_key]
    msg = {"type": "ANALYSIS_UPDATE", "data": data}
    _store_event(msg)
    await ws_manager.broadcast(msg)


async def push_state_change(pair: str, old_state: str, new_state: str) -> None:
    msg = {
        "type": "STATE_CHANGE",
        "data": {
            "pair": pair.upper(),
            "old_state": old_state,
            "new_state": new_state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    _store_event(msg)
    await ws_manager.broadcast(msg)


async def push_trade_closed(trade_data: dict) -> None:
    _trades.insert(0, trade_data)
    while len(_trades) > _MAX_TRADES:
        _trades.pop()
    record_equity_point()
    msg = {"type": "TRADE_CLOSED", "data": trade_data}
    _store_event(msg)
    await ws_manager.broadcast(msg)


async def push_portfolio_update() -> None:
    lc = _lifecycle
    if not lc:
        return
    # Build per-trade floating for WebSocket push
    from agent.production_lifecycle import get_current_price_async
    trade_floats = []
    total_floating = 0.0
    for pair, (trade, _mgr) in getattr(lc, "_active", {}).items():
        try:
            cur_price = await get_current_price_async(pair)
            point = PAIR_POINT.get(pair, 0.0001)
            raw_pnl = (cur_price - trade.entry_price) if trade.direction == "buy" else (trade.entry_price - cur_price)
            f_pips = raw_pnl / point
            risk = abs(trade.entry_price - getattr(trade, "original_sl", trade.stop_loss))
            rr_cur = (raw_pnl / risk) if risk > 0 else 0.0
            if hasattr(lc, "trade_floating_pnl"):
                f_dollar = lc.trade_floating_pnl(trade, cur_price)
            else:
                f_dollar = (lc.balance * lc.risk_per_trade) * rr_cur
            total_floating += f_dollar
            trade_floats.append({
                "pair": pair,
                "current_price": cur_price,
                "floating_pips": round(f_pips, 1),
                "floating_dollar": round(f_dollar, 2),
                "rr_current": round(rr_cur, 2),
                "remaining_size": round(getattr(trade, "remaining_size", 1.0), 4),
            })
        except Exception:
            pass
    record_equity_point()
    await ws_manager.broadcast({
        "type": "PORTFOLIO_UPDATE",
        "data": {
            "balance": round(lc.balance, 2),
            "floating_pnl": round(total_floating, 2),
            "active_count": lc.active_count,
            "is_halted": lc.is_halted,
            "trade_floats": trade_floats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    })


async def update_daily_stats(stats: dict) -> None:
    global _daily_stats
    _daily_stats = stats
    record_equity_point()
