"""
tests/test_dashboard_v3.py — Tests for Dashboard V3 backend routes.

Tests auth, analytics, market endpoints, trade filtering, and V3 integration.
Does NOT require live OANDA/Gemini — all mocked.
"""

import asyncio
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient, ASGITransport

# Import the FastAPI app
from dashboard.backend.main import app, _trades, _analyses, _events, _equity_history


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_state():
    """Clear in-memory state before each test."""
    _trades.clear()
    _analyses.clear()
    _events.clear()
    _equity_history.clear()
    yield
    _trades.clear()
    _analyses.clear()
    _events.clear()
    _equity_history.clear()


@pytest.fixture
def client():
    """Async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Auth route tests
# ---------------------------------------------------------------------------

class TestAuthLogin:
    """Test /api/auth/login endpoint."""

    @pytest.mark.asyncio
    async def test_login_with_api_key(self, client):
        """Login with valid API key returns JWT token."""
        from config.settings import DASHBOARD_API_KEY
        if not DASHBOARD_API_KEY:
            pytest.skip("DASHBOARD_API_KEY not configured")

        resp = await client.post("/api/auth/login", json={"api_key": DASHBOARD_API_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert "user" in data
        assert data["user"]["role"] == "admin"

    @pytest.mark.asyncio
    async def test_login_with_invalid_key(self, client):
        """Invalid API key returns 401."""
        from config.settings import DASHBOARD_API_KEY
        if not DASHBOARD_API_KEY:
            pytest.skip("DASHBOARD_API_KEY not configured")

        resp = await client.post("/api/auth/login", json={"api_key": "wrong-key"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_empty_body_returns_400(self, client):
        """Empty body returns 400."""
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_login_returns_valid_jwt(self, client):
        """Returned JWT can be used with /api/auth/me."""
        from config.settings import DASHBOARD_API_KEY
        if not DASHBOARD_API_KEY:
            pytest.skip("DASHBOARD_API_KEY not configured")

        login_resp = await client.post("/api/auth/login", json={"api_key": DASHBOARD_API_KEY})
        token = login_resp.json()["token"]

        me_resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["role"] == "admin"

    @pytest.mark.asyncio
    async def test_me_without_token_returns_401(self, client):
        """No auth header returns 401."""
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_invalid_token_returns_401(self, client):
        """Bad JWT returns 401."""
        resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_token(self, client):
        """Token refresh returns new valid JWT."""
        from config.settings import DASHBOARD_API_KEY
        if not DASHBOARD_API_KEY:
            pytest.skip("DASHBOARD_API_KEY not configured")

        login_resp = await client.post("/api/auth/login", json={"api_key": DASHBOARD_API_KEY})
        token = login_resp.json()["token"]

        refresh_resp = await client.post(
            "/api/auth/refresh",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert refresh_resp.status_code == 200
        new_data = refresh_resp.json()
        assert "token" in new_data
        assert new_data["token"] != token  # Should be different


# ---------------------------------------------------------------------------
# Trade filtering tests
# ---------------------------------------------------------------------------

class TestTradeFiltering:
    """Test /api/trades with search and filter params."""

    def _make_trade(self, pair, result, pnl, closed_at_str):
        return {
            "trade_id": f"T-{pair}-{result}",
            "pair": pair,
            "direction": "buy",
            "strategy_mode": "normal",
            "mode": "demo",
            "entry_price": 1.1000,
            "stop_loss": 1.0900,
            "take_profit_1": 1.1200,
            "take_profit_2": None,
            "exit_price": 1.1100,
            "result": result,
            "pips": 10.0,
            "rr_achieved": 1.0,
            "duration_minutes": 60,
            "confluence_score": 10,
            "sl_was_moved_be": False,
            "sl_trail_applied": False,
            "final_sl": 1.0900,
            "demo_pnl": pnl,
            "demo_balance_after": 10000 + pnl,
            "opened_at": "2026-01-01T00:00:00",
            "closed_at": closed_at_str,
        }

    @pytest.mark.asyncio
    async def test_trades_search_by_pair(self, client):
        """Search param filters trades by pair."""
        _trades.extend([
            self._make_trade("EUR_USD", "TP1_HIT", 50.0, "2026-01-15T10:00:00"),
            self._make_trade("GBP_USD", "SL_HIT", -30.0, "2026-01-15T12:00:00"),
            self._make_trade("EUR_JPY", "TP2_HIT", 80.0, "2026-01-16T10:00:00"),
        ])

        resp = await client.get("/api/trades", params={"search": "EUR"})
        data = resp.json()
        assert len(data) == 2
        pairs = {t["pair"] for t in data}
        assert "EUR_USD" in pairs
        assert "EUR_JPY" in pairs

    @pytest.mark.asyncio
    async def test_trades_filter_win(self, client):
        """result_filter=win shows only winning trades."""
        _trades.extend([
            self._make_trade("EUR_USD", "TP1_HIT", 50.0, "2026-01-15T10:00:00"),
            self._make_trade("GBP_USD", "SL_HIT", -30.0, "2026-01-15T12:00:00"),
            self._make_trade("XAU_USD", "TP2_HIT", 100.0, "2026-01-16T10:00:00"),
        ])

        resp = await client.get("/api/trades", params={"result_filter": "win"})
        data = resp.json()
        assert len(data) == 2
        assert all(t["result"] in ("TP1_HIT", "TP2_HIT", "TRAIL_PROFIT") for t in data)

    @pytest.mark.asyncio
    async def test_trades_filter_loss(self, client):
        """result_filter=loss shows only losing trades."""
        _trades.extend([
            self._make_trade("EUR_USD", "TP1_HIT", 50.0, "2026-01-15T10:00:00"),
            self._make_trade("GBP_USD", "SL_HIT", -30.0, "2026-01-15T12:00:00"),
        ])

        resp = await client.get("/api/trades", params={"result_filter": "loss"})
        data = resp.json()
        assert len(data) == 1
        assert data[0]["result"] == "SL_HIT"

    @pytest.mark.asyncio
    async def test_trades_pagination(self, client):
        """Pagination with limit and offset works."""
        for i in range(10):
            _trades.append(
                self._make_trade(f"PAIR_{i}", "TP1_HIT", 10.0, f"2026-01-{15+i:02d}T10:00:00")
            )

        resp = await client.get("/api/trades", params={"limit": 3, "offset": 2})
        data = resp.json()
        assert len(data) == 3

    @pytest.mark.asyncio
    async def test_trades_empty_filters(self, client):
        """Empty filters return all trades."""
        _trades.extend([
            self._make_trade("EUR_USD", "TP1_HIT", 50.0, "2026-01-15T10:00:00"),
            self._make_trade("GBP_USD", "SL_HIT", -30.0, "2026-01-15T12:00:00"),
        ])

        resp = await client.get("/api/trades")
        data = resp.json()
        assert len(data) == 2


# ---------------------------------------------------------------------------
# Analytics route tests
# ---------------------------------------------------------------------------

class TestAnalyticsRoutes:
    """Test /api/analytics/* endpoints."""

    @pytest.mark.asyncio
    async def test_analytics_summary_empty(self, client):
        """Summary with no data returns zeros."""
        resp = await client.get("/api/analytics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_analytics_performance_empty(self, client):
        """Performance with no data returns empty list."""
        resp = await client.get("/api/analytics/performance")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_analytics_by_strategy_empty(self, client):
        """By-strategy with no data returns empty list."""
        resp = await client.get("/api/analytics/by-strategy")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_analytics_by_pair_empty(self, client):
        """By-pair with no data returns empty list."""
        resp = await client.get("/api/analytics/by-pair")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_analytics_period_param(self, client):
        """Period param is accepted without error."""
        for period in ["7d", "30d", "all"]:
            resp = await client.get("/api/analytics/summary", params={"period": period})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Market route tests
# ---------------------------------------------------------------------------

class TestMarketRoutes:
    """Test /api/market/candles endpoint."""

    @pytest.mark.asyncio
    async def test_candles_returns_structure(self, client):
        """Candle endpoint returns expected structure even on failure."""
        resp = await client.get("/api/market/candles/EUR_USD", params={"timeframe": "H1", "count": 50})
        assert resp.status_code == 200
        data = resp.json()
        assert "candles" in data
        assert "pair" in data
        assert "timeframe" in data

    @pytest.mark.asyncio
    async def test_candles_accepts_timeframes(self, client):
        """All timeframe values are accepted."""
        for tf in ["M1", "M5", "M15", "H1", "H4", "D1"]:
            resp = await client.get("/api/market/candles/EUR_USD", params={"timeframe": tf})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Force scan endpoint test
# ---------------------------------------------------------------------------

class TestForceScan:
    """Test /api/analysis/force-scan endpoint."""

    @pytest.mark.asyncio
    async def test_force_scan_no_lifecycle(self, client):
        """Force scan returns error when lifecycle not initialized."""
        from config.settings import DASHBOARD_API_KEY
        headers = {"X-API-Key": DASHBOARD_API_KEY} if DASHBOARD_API_KEY else {}
        resp = await client.post("/api/analysis/force-scan", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# Events endpoint test
# ---------------------------------------------------------------------------

class TestEvents:
    """Test /api/events endpoint."""

    @pytest.mark.asyncio
    async def test_events_empty(self, client):
        """Events returns empty when no events."""
        resp = await client.get("/api/events")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_events_returns_stored(self, client):
        """Events returns stored events."""
        _events.appendleft({
            "time": "12:00:00",
            "type": "STATE_CHANGE",
            "pair": "EUR_USD",
            "summary": "IDLE → ANALYZING",
        })
        resp = await client.get("/api/events")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["pair"] == "EUR_USD"


# ---------------------------------------------------------------------------
# System config endpoints (existing but verify no regression)
# ---------------------------------------------------------------------------

class TestSystemEndpoints:
    """Verify system endpoints still work after V3 changes."""

    @pytest.mark.asyncio
    async def test_system_status(self, client):
        """System status returns expected fields."""
        resp = await client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert "is_halted" in data
        assert "api_status" in data
        assert "pairs" in data

    @pytest.mark.asyncio
    async def test_system_config(self, client):
        """System config endpoint returns response."""
        resp = await client.get("/api/system/config")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_still_works(self, client):
        """Health endpoint still functional after V3 changes."""
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


# ---------------------------------------------------------------------------
# Portfolio & equity (existing, verify no regression)
# ---------------------------------------------------------------------------

class TestPortfolioEndpoints:
    """Verify portfolio endpoints still work."""

    @pytest.mark.asyncio
    async def test_portfolio_default(self, client):
        """Portfolio returns default values when no lifecycle."""
        resp = await client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "balance" in data
        assert "active_trades" in data
        assert isinstance(data["active_trades"], list)

    @pytest.mark.asyncio
    async def test_equity_history(self, client):
        """Equity history returns points list."""
        resp = await client.get("/api/portfolio/equity")
        assert resp.status_code == 200
        data = resp.json()
        assert "points" in data


# ---------------------------------------------------------------------------
# JWT utility direct tests
# ---------------------------------------------------------------------------

class TestJWTUtils:
    """Direct unit tests for JWT creation/verification."""

    def test_create_and_verify_jwt(self):
        """Created JWT can be verified."""
        from dashboard.backend.routes.auth import _create_jwt, _verify_jwt
        import time

        payload = {"sub": "test@test.com", "role": "admin", "iat": int(time.time()), "exp": int(time.time()) + 3600}
        token = _create_jwt(payload)
        verified = _verify_jwt(token)
        assert verified is not None
        assert verified["sub"] == "test@test.com"

    def test_expired_jwt_rejected(self):
        """Expired JWT returns None."""
        from dashboard.backend.routes.auth import _create_jwt, _verify_jwt

        payload = {"sub": "test", "role": "admin", "iat": 1000000, "exp": 1000001}
        token = _create_jwt(payload)
        verified = _verify_jwt(token)
        assert verified is None

    def test_tampered_jwt_rejected(self):
        """Tampered JWT returns None."""
        from dashboard.backend.routes.auth import _create_jwt, _verify_jwt
        import time

        payload = {"sub": "test", "role": "admin", "iat": int(time.time()), "exp": int(time.time()) + 3600}
        token = _create_jwt(payload)
        # Tamper with token
        parts = token.split(".")
        parts[1] = parts[1] + "x"
        tampered = ".".join(parts)
        assert _verify_jwt(tampered) is None

    def test_malformed_jwt_rejected(self):
        """Random string returns None."""
        from dashboard.backend.routes.auth import _verify_jwt
        assert _verify_jwt("not.a.jwt") is None
        assert _verify_jwt("") is None
        assert _verify_jwt("single") is None


# ---------------------------------------------------------------------------
# V3 SPA Catch-all route tests
# ---------------------------------------------------------------------------

class TestV3SPACatchAll:
    """Test /v3/* SPA catch-all serves index.html for all sub-routes."""

    @pytest.mark.asyncio
    async def test_v3_root(self, client, tmp_path):
        """GET /v3 returns index.html when frontend exists."""
        from dashboard.backend import main as _m
        fake_dist = tmp_path / "dist"
        fake_dist.mkdir()
        (fake_dist / "index.html").write_text("<html>V3</html>")
        original = _m._FRONTEND_V3_DIR
        _m._FRONTEND_V3_DIR = fake_dist
        try:
            resp = await client.get("/v3")
            assert resp.status_code == 200
            assert "V3" in resp.text
        finally:
            _m._FRONTEND_V3_DIR = original

    @pytest.mark.asyncio
    async def test_v3_subroute_chart(self, client, tmp_path):
        """GET /v3/chart returns index.html (SPA routing)."""
        from dashboard.backend import main as _m
        fake_dist = tmp_path / "dist"
        fake_dist.mkdir()
        (fake_dist / "index.html").write_text("<html>SPA</html>")
        original = _m._FRONTEND_V3_DIR
        _m._FRONTEND_V3_DIR = fake_dist
        try:
            resp = await client.get("/v3/chart")
            assert resp.status_code == 200
            assert "SPA" in resp.text
        finally:
            _m._FRONTEND_V3_DIR = original

    @pytest.mark.asyncio
    async def test_v3_deep_route(self, client, tmp_path):
        """GET /v3/radar returns index.html (deep SPA route)."""
        from dashboard.backend import main as _m
        fake_dist = tmp_path / "dist"
        fake_dist.mkdir()
        (fake_dist / "index.html").write_text("<html>Deep</html>")
        original = _m._FRONTEND_V3_DIR
        _m._FRONTEND_V3_DIR = fake_dist
        try:
            resp = await client.get("/v3/radar")
            assert resp.status_code == 200
            assert "Deep" in resp.text
        finally:
            _m._FRONTEND_V3_DIR = original

    @pytest.mark.asyncio
    async def test_v3_missing_frontend(self, client, tmp_path):
        """GET /v3/anything returns 503 when frontend not built."""
        from dashboard.backend import main as _m
        original = _m._FRONTEND_V3_DIR
        _m._FRONTEND_V3_DIR = tmp_path / "nonexistent"
        try:
            resp = await client.get("/v3/chart")
            assert resp.status_code == 503
        finally:
            _m._FRONTEND_V3_DIR = original
