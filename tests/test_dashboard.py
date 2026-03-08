"""
tests/test_dashboard.py — Tests for dashboard/backend/main.py.

Uses FastAPI's TestClient for sync HTTP tests and direct
function calls for push helpers.
"""

from __future__ import annotations

import asyncio
import pytest
from fastapi.testclient import TestClient

from dashboard.backend.main import (
    app,
    push_analysis_update,
    push_state_change,
    push_trade_closed,
    update_daily_stats,
    require_api_key,
    _analyses,
    _trades,
    _daily_stats,
    ws_manager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_store():
    """Reset in-memory stores before each test."""
    _analyses.clear()
    _trades.clear()
    asyncio.get_event_loop().run_until_complete(update_daily_stats({}))
    yield
    _analyses.clear()
    _trades.clear()
    asyncio.get_event_loop().run_until_complete(update_daily_stats({}))


@pytest.fixture
def client():
    return TestClient(app)


# ===========================================================================
# 1. Health endpoint
# ===========================================================================

class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "ws_connections" in data


# ===========================================================================
# 2. Analysis endpoints
# ===========================================================================

class TestAnalysisEndpoints:
    def test_live_empty(self, client):
        resp = client.get("/api/analysis/live")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_live_returns_active(self, client):
        _analyses["XAUUSD"] = {
            "pair": "XAUUSD", "state": "WATCHING", "score": 11,
        }
        _analyses["EURUSD"] = {
            "pair": "EURUSD", "state": "CLOSED", "score": 8,
        }
        resp = client.get("/api/analysis/live")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["pair"] == "XAUUSD"

    def test_live_excludes_cancelled(self, client):
        _analyses["XAUUSD"] = {
            "pair": "XAUUSD", "state": "CANCELLED",
        }
        resp = client.get("/api/analysis/live")
        assert resp.json() == []

    def test_pair_analysis_found(self, client):
        _analyses["XAUUSD"] = {"pair": "XAUUSD", "state": "WATCHING"}
        resp = client.get("/api/analysis/XAUUSD")
        assert resp.status_code == 200
        assert resp.json()["pair"] == "XAUUSD"

    def test_pair_analysis_case_insensitive(self, client):
        _analyses["XAUUSD"] = {"pair": "XAUUSD", "state": "WATCHING"}
        resp = client.get("/api/analysis/xauusd")
        assert resp.status_code == 200

    def test_pair_not_found(self, client):
        resp = client.get("/api/analysis/GBPJPY")
        assert resp.status_code == 404


# ===========================================================================
# 3. Trade endpoints
# ===========================================================================

class TestTradeEndpoints:
    def test_trades_empty(self, client):
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_trades_pagination(self, client):
        for i in range(10):
            _trades.append({"id": i, "pair": "XAUUSD"})
        resp = client.get("/api/trades?limit=3&offset=2")
        data = resp.json()
        assert len(data) == 3
        assert data[0]["id"] == 2


# ===========================================================================
# 4. Stats endpoints
# ===========================================================================

class TestStatsEndpoints:
    def test_daily_stats_default(self, client):
        resp = client.get("/api/stats/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_scans"] == 0

    def test_daily_stats_updated(self, client):
        asyncio.get_event_loop().run_until_complete(update_daily_stats({
            "date": "2026-02-19",
            "total_scans": 15,
            "setups_found": 3,
            "trades_taken": 1,
            "total_pips": 43.0,
        }))
        resp = client.get("/api/stats/daily")
        data = resp.json()
        assert data["total_scans"] == 15
        assert data["total_pips"] == 43.0


# ===========================================================================
# 5. Push helpers
# ===========================================================================

class TestPushHelpers:
    @pytest.mark.asyncio
    async def test_push_analysis_update(self):
        await push_analysis_update("XAUUSD", {
            "pair": "XAUUSD", "state": "WATCHING", "score": 11,
        })
        assert "XAUUSD" in _analyses
        assert _analyses["XAUUSD"]["score"] == 11

    @pytest.mark.asyncio
    async def test_push_trade_closed(self):
        await push_trade_closed({
            "pair": "XAUUSD", "pips": 43.0, "direction": "sell",
        })
        assert len(_trades) == 1
        assert _trades[0]["pips"] == 43.0


# ===========================================================================
# 6. WebSocket
# ===========================================================================

class TestWebSocket:
    def test_ws_connect(self, client):
        """WebSocket handshake succeeds."""
        with client.websocket_connect("/ws") as ws:
            # Just connecting is enough for this test
            assert ws is not None

    def test_ws_manager_count(self):
        """ConnectionManager starts at 0."""
        from dashboard.backend.main import ConnectionManager
        mgr = ConnectionManager()
        assert mgr.active_count == 0


# ===========================================================================
# 7. Admin API authentication (FIX C-05)
# ===========================================================================

class TestAdminAuth:
    """Test that admin endpoints require API key when configured."""

    def test_unhalt_no_key_when_configured(self, client, monkeypatch):
        """Admin endpoints return 401 when API key is configured but not provided."""
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "secret123")
        resp = client.post("/api/system/unhalt")
        assert resp.status_code == 401

    def test_unhalt_wrong_key(self, client, monkeypatch):
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "secret123")
        resp = client.post(
            "/api/system/unhalt",
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_unhalt_correct_key(self, client, monkeypatch):
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "secret123")
        resp = client.post(
            "/api/system/unhalt",
            headers={"X-API-Key": "secret123"},
        )
        # May return success or "lifecycle not init" — but NOT 401
        assert resp.status_code == 200

    def test_unhalt_no_key_configured(self, client, monkeypatch):
        """When no API key is configured, endpoints are accessible (backward compat)."""
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "")
        resp = client.post("/api/system/unhalt")
        assert resp.status_code == 200

    def test_balance_set_requires_key(self, client, monkeypatch):
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "mykey")
        resp = client.post(
            "/api/system/balance/set",
            json={"balance": 5000.0},
        )
        assert resp.status_code == 401

    def test_manual_close_requires_key(self, client, monkeypatch):
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "mykey")
        resp = client.post(
            "/api/positions/trade123/close",
            json={"reason": "test"},
        )
        assert resp.status_code == 401

    def test_config_patch_requires_key(self, client, monkeypatch):
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "mykey")
        resp = client.patch(
            "/api/system/config",
            json={"mode": "demo"},
        )
        assert resp.status_code == 401

    def test_read_endpoints_no_auth_needed(self, client, monkeypatch):
        """Read-only endpoints should NOT require auth."""
        monkeypatch.setattr("dashboard.backend.main.DASHBOARD_API_KEY", "mykey")
        # These should all return 200 without any API key
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/analysis/live").status_code == 200
        assert client.get("/api/trades").status_code == 200
        assert client.get("/api/stats/daily").status_code == 200
        assert client.get("/api/system/status").status_code == 200
        assert client.get("/api/portfolio/equity").status_code == 200
        assert client.get("/api/events").status_code == 200
