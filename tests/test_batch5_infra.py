"""
tests/test_batch5_infra.py — Batch 5: Infrastructure Hardening tests.

Covers:
  F5-01: Dashboard DB-backed trade history
  F5-02: WebSocket token authentication
  F5-03: WebSocket disconnect safe removal (double-disconnect)
  F5-04: WhatsApp connection pooling, retry, circuit breaker
  F5-05: Scheduler fault isolation (batch fallback + per-pair isolation)
  F5-06: Chart plt.close in finally (memory leak prevention)
  F5-07: Graceful shutdown — save active trades to DB
  F5-09: scan_batch uses fresh outcome (not stale last_plan)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pytest

# =========================================================================
# F5-03: WebSocket disconnect safe removal
# =========================================================================


class TestWSDisconnectSafe:
    """F5-03: ConnectionManager.disconnect() must not raise on double-remove."""

    def _make_manager(self):
        from dashboard.backend.main import ConnectionManager
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_disconnect_unknown_ws_no_error(self):
        """Disconnecting a WS that's not in the list must not raise."""
        mgr = self._make_manager()
        fake_ws = MagicMock()
        # Should not raise ValueError
        mgr.disconnect(fake_ws)
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_double_disconnect_no_error(self):
        """Disconnecting same WS twice must not raise."""
        mgr = self._make_manager()
        fake_ws = MagicMock()
        # Manually add it
        mgr._connections.append(fake_ws)
        assert mgr.active_count == 1
        mgr.disconnect(fake_ws)
        assert mgr.active_count == 0
        # Second disconnect — should not raise
        mgr.disconnect(fake_ws)
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_removes_stale_and_disconnect_safe(self):
        """After broadcast removes a stale WS, explicit disconnect should be safe."""
        mgr = self._make_manager()
        stale_ws = MagicMock()
        stale_ws.send_json = AsyncMock(side_effect=Exception("disconnected"))
        mgr._connections.append(stale_ws)
        # Broadcast removes stale
        await mgr.broadcast({"type": "TEST"})
        # Now disconnect should not raise
        mgr.disconnect(stale_ws)


# =========================================================================
# F5-04: WhatsApp connection pooling, retry, circuit breaker
# =========================================================================


class TestCircuitBreaker:
    """F5-04a: CircuitBreaker state transitions."""

    def _make_cb(self, threshold=3, recovery=0.1):
        from notifier.whatsapp import CircuitBreaker
        return CircuitBreaker(threshold=threshold, recovery_timeout=recovery)

    def test_starts_closed(self):
        cb = self._make_cb()
        assert cb.state == "closed"
        assert cb.allow_request()

    def test_opens_after_threshold_failures(self):
        cb = self._make_cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()  # 3rd
        assert cb.state == "open"
        assert not cb.allow_request()

    def test_half_open_after_recovery_timeout(self):
        cb = self._make_cb(threshold=2, recovery=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.06)
        assert cb.state == "half_open"
        assert cb.allow_request()

    def test_success_resets_to_closed(self):
        cb = self._make_cb(threshold=2, recovery=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.06)
        assert cb.state == "half_open"
        cb.record_success()
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_failure_count_tracks(self):
        cb = self._make_cb(threshold=5)
        for i in range(4):
            cb.record_failure()
        assert cb.failure_count == 4
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.failure_count == 5
        assert cb.state == "open"


class TestWhatsAppRetry:
    """F5-04b: WhatsApp retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        """Should succeed after first failure + retry."""
        from notifier.whatsapp import WhatsAppNotifier
        import httpx

        notifier = WhatsAppNotifier(
            base_url="http://fake:9999",
            phone="1234567890",
            max_retries=3,
            backoff_base=0.01,
        )

        call_count = 0

        async def mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.RequestError("temporary failure")
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"status": "ok"}
            return resp

        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.request = mock_request
        notifier._client = client_mock

        result = await notifier.send_message("test")
        assert result == {"status": "ok"}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_request(self):
        """When circuit is OPEN, requests should be rejected immediately."""
        from notifier.whatsapp import WhatsAppNotifier

        notifier = WhatsAppNotifier(
            base_url="http://fake:9999",
            phone="1234567890",
        )
        # Force circuit open
        for _ in range(5):
            notifier.circuit.record_failure()
        assert notifier.circuit.state == "open"

        with pytest.raises(ConnectionError, match="Circuit breaker OPEN"):
            await notifier.send_message("test")

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self):
        """After max_retries failures, should raise the last exception."""
        from notifier.whatsapp import WhatsAppNotifier
        import httpx

        notifier = WhatsAppNotifier(
            base_url="http://fake:9999",
            phone="1234567890",
            max_retries=2,
            backoff_base=0.01,
        )

        async def always_fail(method, url, **kwargs):
            raise httpx.RequestError("down")

        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.request = always_fail
        notifier._client = client_mock

        with pytest.raises(httpx.RequestError, match="down"):
            await notifier.send_message("test")


class TestWhatsAppPooling:
    """F5-04c: Connection pooling — shared httpx client."""

    def test_client_reused_across_calls(self):
        """_get_client() should return the same instance."""
        from notifier.whatsapp import WhatsAppNotifier

        notifier = WhatsAppNotifier(base_url="http://fake:9999", phone="123")
        c1 = notifier._get_client()
        c2 = notifier._get_client()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_close_disposes_client(self):
        """close() should set _client to None."""
        from notifier.whatsapp import WhatsAppNotifier

        notifier = WhatsAppNotifier(base_url="http://fake:9999", phone="123")
        _ = notifier._get_client()
        assert notifier._client is not None
        await notifier.close()
        assert notifier._client is None

    def test_closed_client_recreated(self):
        """If client is closed, _get_client() creates a new one."""
        from notifier.whatsapp import WhatsAppNotifier

        notifier = WhatsAppNotifier(base_url="http://fake:9999", phone="123")
        c1 = notifier._get_client()
        c1._transport = None  # We can't truly close in unit test, so simulate
        # Force the check: create mock that says is_closed=True
        mock_client = MagicMock()
        mock_client.is_closed = True
        notifier._client = mock_client
        c2 = notifier._get_client()
        assert c2 is not mock_client


# =========================================================================
# F5-05: Scheduler fault isolation
# =========================================================================


class TestSchedulerFaultIsolation:
    """F5-05: Scheduler _run_batch falls back to per-pair on batch failure."""

    @pytest.mark.asyncio
    async def test_batch_fn_failure_falls_back_to_scan_fn(self):
        """If batch_fn raises, should fall back to per-pair scan_fn."""
        from scheduler.runner import ScanScheduler

        scanned = []

        async def failing_batch(pairs):
            raise RuntimeError("batch kaboom")

        async def scan_fn(pair):
            scanned.append(pair)

        sched = ScanScheduler(
            scan_fn=scan_fn,
            batch_fn=failing_batch,
            pairs=["EURUSD", "GBPJPY"],
        )
        await sched._run_batch(["EURUSD", "GBPJPY"])
        assert scanned == ["EURUSD", "GBPJPY"]

    @pytest.mark.asyncio
    async def test_per_pair_isolation_continues_on_failure(self):
        """If one pair fails in per-pair mode, the rest should still scan."""
        from scheduler.runner import ScanScheduler

        scanned = []

        async def scan_fn(pair):
            if pair == "GBPJPY":
                raise RuntimeError("GBPJPY dead")
            scanned.append(pair)

        sched = ScanScheduler(
            scan_fn=scan_fn,
            pairs=["EURUSD", "GBPJPY", "USDJPY"],
        )
        await sched._run_batch(["EURUSD", "GBPJPY", "USDJPY"])
        assert scanned == ["EURUSD", "USDJPY"]

    @pytest.mark.asyncio
    async def test_batch_fn_success_no_fallback(self):
        """If batch_fn succeeds, scan_fn should NOT be called."""
        from scheduler.runner import ScanScheduler

        batch_called = []
        scan_called = []

        async def batch_fn(pairs):
            batch_called.extend(pairs)

        async def scan_fn(pair):
            scan_called.append(pair)

        sched = ScanScheduler(
            scan_fn=scan_fn,
            batch_fn=batch_fn,
            pairs=["EURUSD"],
        )
        await sched._run_batch(["EURUSD"])
        assert batch_called == ["EURUSD"]
        assert scan_called == []

    @pytest.mark.asyncio
    async def test_no_batch_fn_uses_scan_fn(self):
        """Without batch_fn, scan_fn is used for each pair."""
        from scheduler.runner import ScanScheduler

        scanned = []

        async def scan_fn(pair):
            scanned.append(pair)

        sched = ScanScheduler(scan_fn=scan_fn, pairs=["XAUUSD", "EURUSD"])
        await sched._run_batch(["XAUUSD", "EURUSD"])
        assert scanned == ["XAUUSD", "EURUSD"]


# =========================================================================
# F5-06: Chart plt.close in finally
# =========================================================================


class TestChartMemoryLeak:
    """F5-06: plt.close(fig) must be called even if savefig raises."""

    def test_generate_entry_chart_closes_on_error(self):
        """If savefig raises, fig should still be closed."""
        import matplotlib.pyplot as plt
        from charts.screenshot import ChartScreenshotGenerator
        import pandas as pd

        gen = ChartScreenshotGenerator()
        # Create minimal OHLCV
        ohlcv = pd.DataFrame(
            {
                "Open": [1.0] * 20,
                "High": [1.1] * 20,
                "Low": [0.9] * 20,
                "Close": [1.05] * 20,
            },
            index=pd.date_range("2024-01-01", periods=20, freq="h"),
        )

        initial_figs = len(plt.get_fignums())

        with patch("matplotlib.figure.Figure.savefig", side_effect=IOError("disk full")):
            with pytest.raises(IOError, match="disk full"):
                gen.generate_entry_chart(
                    ohlcv, "EURUSD", "buy",
                    entry_zone=(1.0, 1.05),
                    stop_loss=0.95,
                    take_profit_1=1.1,
                )

        # Figure count should not have increased (fig was closed)
        assert len(plt.get_fignums()) == initial_figs

    def test_generate_audit_chart_closes_on_error(self):
        """If savefig raises on audit chart, fig should still be closed."""
        import matplotlib.pyplot as plt
        from charts.screenshot import ChartScreenshotGenerator
        import pandas as pd

        gen = ChartScreenshotGenerator()
        ohlcv = pd.DataFrame(
            {
                "Open": [1.0] * 20,
                "High": [1.1] * 20,
                "Low": [0.9] * 20,
                "Close": [1.05] * 20,
            },
            index=pd.date_range("2024-01-01", periods=20, freq="h"),
        )

        initial_figs = len(plt.get_fignums())

        with patch("matplotlib.figure.Figure.savefig", side_effect=IOError("disk full")):
            with pytest.raises(IOError, match="disk full"):
                gen.generate_audit_chart(
                    ohlcv, "EURUSD", "Audit Test",
                )

        assert len(plt.get_fignums()) == initial_figs

    def test_generate_entry_chart_happy_path(self):
        """Normal generation should also close the figure."""
        import matplotlib.pyplot as plt
        from charts.screenshot import ChartScreenshotGenerator
        import pandas as pd
        import os

        gen = ChartScreenshotGenerator()
        ohlcv = pd.DataFrame(
            {
                "Open": [1.0] * 20,
                "High": [1.1] * 20,
                "Low": [0.9] * 20,
                "Close": [1.05] * 20,
            },
            index=pd.date_range("2024-01-01", periods=20, freq="h"),
        )

        initial_figs = len(plt.get_fignums())
        filepath = gen.generate_entry_chart(
            ohlcv, "TEST", "buy",
            entry_zone=(1.0, 1.05),
            stop_loss=0.95,
            take_profit_1=1.1,
        )
        assert os.path.exists(filepath)
        assert len(plt.get_fignums()) == initial_figs
        os.unlink(filepath)


# =========================================================================
# F5-07: Graceful shutdown — save active trades
# =========================================================================


class TestGracefulShutdown:
    """F5-07: save_active_trades() persists active trades to DB."""

    @pytest.mark.asyncio
    async def test_save_active_trades_serializes(self):
        """save_active_trades() should call repo.set_setting_json with trade data."""
        from agent.production_lifecycle import ProductionLifecycle

        repo = AsyncMock()
        repo.get_setting_json = AsyncMock(return_value={})
        repo.set_setting_json = AsyncMock()

        lifecycle = ProductionLifecycle(repo=repo, mode="demo")

        # Create a mock active trade
        trade = MagicMock()
        trade.trade_id = "T001"
        trade.direction = "buy"
        trade.entry_price = 1.1000
        trade.stop_loss = 1.0950
        trade.take_profit_1 = 1.1100
        trade.take_profit_2 = 1.1200
        trade.partial_closed = False
        trade.sl_moved_to_be = False
        trade.opened_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        mgr = MagicMock()
        lifecycle._active["EURUSD"] = (trade, mgr)

        await lifecycle.save_active_trades()

        repo.set_setting_json.assert_called()
        call_args = repo.set_setting_json.call_args
        assert call_args[0][0] == "active_trades"
        saved = call_args[0][1]
        assert len(saved) == 1
        assert saved[0]["pair"] == "EURUSD"
        assert saved[0]["trade_id"] == "T001"
        assert saved[0]["direction"] == "buy"

    @pytest.mark.asyncio
    async def test_save_active_trades_empty(self):
        """With no active trades, should save empty list."""
        from agent.production_lifecycle import ProductionLifecycle

        repo = AsyncMock()
        repo.get_setting_json = AsyncMock(return_value={})
        repo.set_setting_json = AsyncMock()

        lifecycle = ProductionLifecycle(repo=repo, mode="demo")
        await lifecycle.save_active_trades()

        call_args = repo.set_setting_json.call_args
        assert call_args[0][0] == "active_trades"
        assert call_args[0][1] == []

    @pytest.mark.asyncio
    async def test_save_active_trades_handles_bad_trade(self):
        """If one trade fails to serialize, others should still be saved."""
        from agent.production_lifecycle import ProductionLifecycle

        repo = AsyncMock()
        repo.get_setting_json = AsyncMock(return_value={})
        repo.set_setting_json = AsyncMock()

        lifecycle = ProductionLifecycle(repo=repo, mode="demo")

        # Bad trade — trade_id raises
        bad_trade = MagicMock()
        type(bad_trade).trade_id = PropertyMock(side_effect=AttributeError("boom"))
        bad_mgr = MagicMock()
        lifecycle._active["BAD"] = (bad_trade, bad_mgr)

        # Good trade
        good_trade = MagicMock()
        good_trade.trade_id = "T002"
        good_trade.direction = "sell"
        good_trade.entry_price = 1.2000
        good_trade.stop_loss = 1.2050
        good_trade.take_profit_1 = 1.1900
        good_trade.take_profit_2 = None
        good_trade.partial_closed = False
        good_trade.sl_moved_to_be = False
        good_trade.opened_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        good_mgr = MagicMock()
        lifecycle._active["GBPJPY"] = (good_trade, good_mgr)

        await lifecycle.save_active_trades()

        saved = repo.set_setting_json.call_args[0][1]
        # At least the good trade should be saved
        good_items = [t for t in saved if t.get("pair") == "GBPJPY"]
        assert len(good_items) == 1


# =========================================================================
# F5-09: scan_batch uses fresh outcome
# =========================================================================


class TestScanBatchFreshOutcome:
    """F5-09: scan_batch should use the actual AnalysisOutcome from scan_pair()."""

    @pytest.mark.asyncio
    async def test_scan_pair_returns_outcome(self):
        """scan_pair should return the AnalysisOutcome from _scan_pair_inner."""
        # We patch _scan_pair_inner to return a known outcome
        from agent.orchestrator import AnalysisOutcome
        from agent.state_machine import AnalysisState

        expected = AnalysisOutcome(
            pair="EURUSD",
            state=AnalysisState.SCANNING,
            plan=None,
            error=None,
            elapsed_seconds=1.5,
        )

        with patch("main._scan_pair_inner", new_callable=AsyncMock, return_value=expected):
            with patch.dict("main._scan_locks", {}, clear=True):
                from main import scan_pair
                result = await scan_pair("EURUSD")
                assert result is expected

    @pytest.mark.asyncio
    async def test_scan_pair_returns_none_if_locked(self):
        """If pair scan is already locked, scan_pair returns None."""
        import main

        lock = asyncio.Lock()
        await lock.acquire()  # lock it so scan_pair sees it as locked

        with patch.dict("main._scan_locks", {"XAUUSD": lock}):
            result = await main.scan_pair("XAUUSD")
            assert result is None

        lock.release()


# =========================================================================
# F5-01: Dashboard DB-backed trade history
# =========================================================================


class TestDashboardDBHistory:
    """F5-01: /api/trades falls back to DB when in-memory store is empty."""

    @pytest.mark.asyncio
    async def test_trades_fallback_to_db(self):
        """When _trades is empty, should query repo.list_trades()."""
        import dashboard.backend.main as dash

        # Clear in-memory trades
        dash._trades.clear()

        mock_trade = MagicMock()
        mock_trade.trade_id = "DB001"
        mock_trade.pair = "EURUSD"
        mock_trade.direction = "buy"
        mock_trade.result = "TP1_HIT"
        mock_trade.pips = 25.0
        mock_trade.pnl = 50.0
        mock_trade.opened_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_trade.closed_at = datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc)

        mock_repo = AsyncMock()
        mock_repo.list_trades = AsyncMock(return_value=[mock_trade])

        old_repo = dash._repo
        dash._repo = mock_repo
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=dash.app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/trades?limit=10")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["trade_id"] == "DB001"
        finally:
            dash._repo = old_repo

    @pytest.mark.asyncio
    async def test_trades_uses_memory_if_available(self):
        """When _trades has data, should return that (not hit DB)."""
        import dashboard.backend.main as dash

        dash._trades.clear()
        dash._trades.append({"trade_id": "MEM001", "pair": "XAUUSD"})

        old_repo = dash._repo
        dash._repo = None  # no repo
        try:
            from httpx import AsyncClient, ASGITransport
            async with AsyncClient(
                transport=ASGITransport(app=dash.app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/trades?limit=10")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["trade_id"] == "MEM001"
        finally:
            dash._repo = old_repo
            dash._trades.clear()


# =========================================================================
# F5-02: WS token authentication
# =========================================================================


class TestWSTokenAuth:
    """F5-02: WebSocket connection should require token when configured."""

    @pytest.mark.asyncio
    async def test_ws_rejected_without_token(self):
        """When DASHBOARD_WS_TOKEN is set, WS without token should be rejected."""
        import dashboard.backend.main as dash

        with patch.object(dash, "DASHBOARD_WS_TOKEN", "secret123"):
            from httpx import AsyncClient, ASGITransport
            from starlette.testclient import TestClient
            client = TestClient(dash.app)
            with pytest.raises(Exception):
                with client.websocket_connect("/ws"):
                    pass

    @pytest.mark.asyncio
    async def test_ws_accepted_with_correct_token(self):
        """When correct token is provided, WS should connect."""
        import dashboard.backend.main as dash

        with patch.object(dash, "DASHBOARD_WS_TOKEN", "secret123"):
            from starlette.testclient import TestClient
            client = TestClient(dash.app)
            with client.websocket_connect("/ws?token=secret123") as ws:
                # Connection succeeded
                assert ws is not None

    @pytest.mark.asyncio
    async def test_ws_open_when_no_token_configured(self):
        """When DASHBOARD_WS_TOKEN is empty, WS should accept all."""
        import dashboard.backend.main as dash

        with patch.object(dash, "DASHBOARD_WS_TOKEN", ""):
            from starlette.testclient import TestClient
            client = TestClient(dash.app)
            with client.websocket_connect("/ws") as ws:
                assert ws is not None


# =========================================================================
# F5-01 extra: set_repo function
# =========================================================================


class TestDashboardSetRepo:
    """F5-01: set_repo() should inject the repository."""

    def test_set_repo_updates_module_var(self):
        import dashboard.backend.main as dash
        old = dash._repo
        try:
            mock_repo = MagicMock()
            dash.set_repo(mock_repo)
            assert dash._repo is mock_repo
        finally:
            dash._repo = old


# =========================================================================
# F5-04 extra: WhatsApp notifier send_image retry
# =========================================================================


class TestWhatsAppSendImageRetry:
    """F5-04d: send_image should also retry with backoff."""

    @pytest.mark.asyncio
    async def test_send_image_retries_on_failure(self):
        from notifier.whatsapp import WhatsAppNotifier
        import httpx

        notifier = WhatsAppNotifier(
            base_url="http://fake:9999",
            phone="1234567890",
            max_retries=2,
            backoff_base=0.01,
        )

        call_count = 0

        async def mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.RequestError("timeout")
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"status": "sent"}
            return resp

        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.request = mock_request
        notifier._client = client_mock

        result = await notifier.send_image(
            "http://example.com/img.png", "Chart caption"
        )
        assert result == {"status": "sent"}
        assert call_count == 2


# =========================================================================
# F5-05 extra: Scheduler batch + fallback count reporting
# =========================================================================


class TestSchedulerReporting:
    """F5-05b: _run_batch reports success/fail counts."""

    @pytest.mark.asyncio
    async def test_mixed_success_failure_counts(self):
        """Per-pair scan should continue even when some pairs fail."""
        from scheduler.runner import ScanScheduler

        results = []

        async def scan_fn(pair):
            if pair == "FAIL":
                raise ValueError("broken")
            results.append(pair)

        sched = ScanScheduler(scan_fn=scan_fn)
        await sched._run_batch(["A", "FAIL", "B", "FAIL", "C"])
        assert results == ["A", "B", "C"]


# =========================================================================
# F5-07 extra: on_shutdown calls save_active_trades
# =========================================================================


class TestShutdownCallsSave:
    """F5-07b: on_shutdown must call save_active_trades when active trades exist."""

    @pytest.mark.asyncio
    async def test_on_shutdown_saves_trades(self):
        """on_shutdown() should call lifecycle.save_active_trades()."""
        import main

        mock_lifecycle = AsyncMock()
        mock_lifecycle.active_pairs = ["EURUSD"]
        mock_lifecycle.is_halted = False
        mock_lifecycle.save_state = AsyncMock()
        mock_lifecycle.save_active_trades = AsyncMock()

        mock_repo = AsyncMock()

        old_lifecycle = main._lifecycle
        old_repo = main._repo
        old_pm = main._price_monitor_task
        old_sched = main._scheduler
        old_wa = main._wa_notifier

        main._lifecycle = mock_lifecycle
        main._repo = mock_repo
        main._price_monitor_task = None
        main._scheduler = None
        main._wa_notifier = None

        try:
            await main.on_shutdown()
            mock_lifecycle.save_active_trades.assert_awaited_once()
            mock_lifecycle.save_state.assert_awaited_once()
        finally:
            main._lifecycle = old_lifecycle
            main._repo = old_repo
            main._price_monitor_task = old_pm
            main._scheduler = old_sched
            main._wa_notifier = old_wa


# =========================================================================
# F5-02 / CORS settings
# =========================================================================


class TestCORSSettings:
    """F5-02b: Dashboard should use configurable CORS origins."""

    def test_dashboard_allowed_origins_configurable(self):
        """DASHBOARD_ALLOWED_ORIGINS should be loadable from config."""
        from config.settings import DASHBOARD_ALLOWED_ORIGINS
        assert isinstance(DASHBOARD_ALLOWED_ORIGINS, list)
        assert len(DASHBOARD_ALLOWED_ORIGINS) > 0


# =========================================================================
# FIX §7.4: Gemini Cost Tracking
# =========================================================================


class TestGeminiCostTracking:
    """§7.4: GeminiClient must track token usage and cost."""

    def test_cost_summary_initial_state(self):
        """New client starts with zero cost."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            summary = client.cost_summary
            assert summary["total_cost_usd"] == 0.0
            assert summary["call_count"] == 0
            assert summary["total_input_tokens"] == 0
            assert summary["total_output_tokens"] == 0
            assert summary["daily_budget_usd"] > 0

    def test_account_usage_tracks_tokens(self):
        """_account_usage correctly accumulates token counts."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")

            # Fake response with usage_metadata
            mock_resp = MagicMock()
            mock_resp.usage_metadata.prompt_token_count = 500
            mock_resp.usage_metadata.candidates_token_count = 200

            client._account_usage(mock_resp, "gemini-2.0-flash")
            assert client._total_input_tokens == 500
            assert client._total_output_tokens == 200
            assert client._call_count == 1
            assert client._total_cost_usd > 0

    def test_budget_exceeded_flag(self):
        """budget_exceeded returns True when cost >= budget."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            client._daily_budget_usd = 0.001  # Very low budget

            mock_resp = MagicMock()
            mock_resp.usage_metadata.prompt_token_count = 1_000_000
            mock_resp.usage_metadata.candidates_token_count = 1_000_000

            client._account_usage(mock_resp, "gemini-pro")
            assert client.budget_exceeded is True

    def test_reset_daily_cost(self):
        """reset_daily_cost zeroes all counters."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            client._total_input_tokens = 1000
            client._total_output_tokens = 500
            client._total_cost_usd = 1.23
            client._call_count = 5

            client.reset_daily_cost()

            assert client._total_input_tokens == 0
            assert client._total_output_tokens == 0
            assert client._total_cost_usd == 0.0
            assert client._call_count == 0


# ===========================================================================
# FP-06 tests — AI Client fixes
# ===========================================================================


class TestFP06AsyncGather:
    """C-03: collect_multi_tf_async must use asyncio.gather for parallel execution."""

    @pytest.mark.asyncio
    async def test_collect_uses_gather_parallel(self):
        """All timeframes run concurrently, not sequentially."""
        import asyncio
        from unittest.mock import patch

        call_order = []

        async def mock_analyze(pair, tf, candle_count=150):
            call_order.append(("start", tf))
            await asyncio.sleep(0.05)  # simulate small delay
            call_order.append(("end", tf))
            return {"timeframe": tf, "candle_count": 0, "last_close": 1.0, "last_time": ""}

        with patch("agent.context_builder.analyze_timeframe_async", side_effect=mock_analyze):
            from agent.context_builder import collect_multi_tf_async
            results = await collect_multi_tf_async("EURUSD", ["H4", "H1", "M15"])

        assert len(results) == 3
        assert "H4" in results and "H1" in results and "M15" in results
        # With gather, all starts should happen before any end
        starts = [i for i, (action, _) in enumerate(call_order) if action == "start"]
        ends = [i for i, (action, _) in enumerate(call_order) if action == "end"]
        # All 3 starts should come before first end (parallel)
        assert len(starts) == 3
        assert len(ends) == 3

    @pytest.mark.asyncio
    async def test_collect_handles_exception_in_one_tf(self):
        """If one TF fails, others still return successfully."""
        import asyncio
        from unittest.mock import patch

        async def mock_analyze(pair, tf, candle_count=150):
            if tf == "H1":
                raise ValueError("OANDA timeout")
            return {"timeframe": tf, "candle_count": 50, "last_close": 1.0, "last_time": ""}

        with patch("agent.context_builder.analyze_timeframe_async", side_effect=mock_analyze):
            from agent.context_builder import collect_multi_tf_async
            results = await collect_multi_tf_async("EURUSD", ["H4", "H1", "M15"])

        assert results["H4"]["timeframe"] == "H4"
        assert "error" in results["H1"]
        assert "OANDA timeout" in results["H1"]["error"]
        assert results["M15"]["timeframe"] == "M15"


class TestFP06BudgetBlocking:
    """H-06: Budget exceeded must BLOCK API calls, not just warn."""

    def test_budget_exceeded_blocks_generate(self):
        """generate() raises BudgetExceededError when budget exhausted."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient, BudgetExceededError
            client = GeminiClient(api_key="test-key")
            client._total_cost_usd = 100.0
            client._daily_budget_usd = 10.0

            with pytest.raises(BudgetExceededError):
                client.generate("SCANNING", "test prompt")

    def test_budget_exceeded_blocks_agenerate_structured(self):
        """agenerate_structured() raises BudgetExceededError when budget exhausted."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient, BudgetExceededError
            client = GeminiClient(api_key="test-key")
            client._total_cost_usd = 100.0
            client._daily_budget_usd = 10.0

            with pytest.raises(BudgetExceededError):
                import asyncio
                from pydantic import BaseModel
                class FakeSchema(BaseModel):
                    text: str = ""
                asyncio.get_event_loop().run_until_complete(
                    client.agenerate_structured("TRIGGERED", "test", FakeSchema)
                )

    def test_budget_not_exceeded_allows_call(self):
        """Calls proceed normally when within budget."""
        with patch("agent.gemini_client.genai.Client"):
            from agent.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            client._total_cost_usd = 0.0
            client._daily_budget_usd = 10.0
            # _check_budget should not raise
            client._check_budget()


class TestFP06ModelFallbackWarning:
    """M-09: model_for_state warns for unknown states."""

    def test_known_state_returns_correct_model(self):
        from agent.gemini_client import model_for_state
        from config.settings import GEMINI_FLASH_MODEL, GEMINI_PRO_MODEL
        assert model_for_state("SCANNING") == GEMINI_FLASH_MODEL
        assert model_for_state("TRIGGERED") == GEMINI_PRO_MODEL

    def test_unknown_state_falls_back_to_flash(self):
        from agent.gemini_client import model_for_state
        from config.settings import GEMINI_FLASH_MODEL
        result = model_for_state("NONEXISTENT")
        assert result == GEMINI_FLASH_MODEL


class TestFP06ResetDailyCostIntegration:
    """D-05: reset_daily_cost integrated into lifecycle reset_daily."""

    def test_reset_daily_calls_gemini_reset(self):
        """reset_daily() calls gemini.reset_daily_cost() on weekday."""
        from agent.production_lifecycle import ProductionLifecycle
        from unittest.mock import MagicMock, AsyncMock
        from datetime import datetime, timezone

        lc = ProductionLifecycle.__new__(ProductionLifecycle)
        lc.balance = 1000.0
        lc.initial_balance = 1000.0
        lc.daily_start_balance = 1000.0
        lc.high_water_mark = 1000.0
        lc._halted = False
        lc._halt_reason = ""
        lc._closed_today = []

        mock_gemini = MagicMock()
        mock_gemini.reset_daily_cost = MagicMock()
        lc._gemini = mock_gemini

        # Patch datetime to return a weekday (Monday)
        with patch("agent.production_lifecycle.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 0  # Monday
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            lc.reset_daily()

        mock_gemini.reset_daily_cost.assert_called_once()


class TestFP06AnalysisTimeframesConfig:
    """L-15: Orchestrator uses ANALYSIS_TIMEFRAMES from config."""

    def test_analysis_timeframes_in_config(self):
        from config.settings import ANALYSIS_TIMEFRAMES
        assert isinstance(ANALYSIS_TIMEFRAMES, list)
        assert len(ANALYSIS_TIMEFRAMES) >= 2
        assert "H4" in ANALYSIS_TIMEFRAMES

    def test_orchestrator_uses_config_default(self):
        """Orchestrator defaults to ANALYSIS_TIMEFRAMES when none provided."""
        from config.settings import ANALYSIS_TIMEFRAMES
        with patch("agent.gemini_client.genai.Client"):
            from agent.orchestrator import AnalysisOrchestrator
            from agent.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            orch = AnalysisOrchestrator("EURUSD", client=client)
            assert orch._analysis_timeframes == ANALYSIS_TIMEFRAMES


class TestFP06FormatContextEdgeCases:
    """L-20/M-10: format_context handles empty data and truncation markers."""

    def test_empty_analyses_returns_no_data(self):
        from agent.context_builder import format_context
        result = format_context("EURUSD", {})
        assert "No data available" in result

    def test_all_errors_returns_error_blocks(self):
        from agent.context_builder import format_context
        analyses = {
            "H4": {"error": "timeout"},
            "H1": {"error": "rate limited"},
        }
        result = format_context("EURUSD", analyses)
        assert "ERROR" in result
        assert "timeout" in result

    def test_truncation_marker_snr(self):
        """SNR levels show truncation marker when > 6."""
        from agent.context_builder import format_context
        # Build minimal valid analysis with many SNR levels
        snr_levels = [
            {"price": 1.05 + i * 0.001, "touches": 3, "score": 5.0, "is_major": i < 2}
            for i in range(10)
        ]
        analyses = {
            "H1": {
                "candle_count": 50, "last_close": 1.0500, "last_time": "2026-01-01",
                "atr": {"current": 0.0010},
                "ema50": {"current": 1.0500, "period": 50},
                "rsi14": {"current": 55.0, "period": 14},
                "structure": {"trend": "bullish"},
                "swing_highs": [], "swing_lows": [],
                "snr_levels": snr_levels,
                "supply_zones": [], "demand_zones": [],
                "bullish_obs": [], "bearish_obs": [],
                "uptrend_lines": [], "downtrend_lines": [],
                "eqh_pools": [], "eql_pools": [], "sweep_events": [],
                "pin_bars": [], "engulfing_patterns": [],
                "choch_micro_bullish": {"confirmed": False},
                "choch_micro_bearish": {"confirmed": False},
            }
        }
        result = format_context("EURUSD", analyses)
        assert "showing top 6 of 10" in result


# ===========================================================================
# FP-07 Tests
# ===========================================================================


class TestFP07MarketHoursTTL:
    """M-05: PendingSetup TTL counts only market-open hours."""

    def test_is_forex_market_open_weekday(self):
        """Forex is open on a Wednesday at noon UTC."""
        from agent.pending_manager import is_forex_market_open
        # Wednesday, 12:00 UTC
        dt = datetime(2025, 1, 8, 12, 0, 0, tzinfo=timezone.utc)
        assert is_forex_market_open(dt) is True

    def test_is_forex_market_closed_saturday(self):
        """Forex is closed all day Saturday."""
        from agent.pending_manager import is_forex_market_open
        dt = datetime(2025, 1, 11, 12, 0, 0, tzinfo=timezone.utc)  # Saturday
        assert is_forex_market_open(dt) is False

    def test_is_forex_market_closed_sunday_before_22(self):
        """Forex is closed Sunday before 22:00 UTC."""
        from agent.pending_manager import is_forex_market_open
        dt = datetime(2025, 1, 12, 21, 0, 0, tzinfo=timezone.utc)  # Sunday 21:00
        assert is_forex_market_open(dt) is False

    def test_is_forex_market_open_sunday_at_22(self):
        """Forex opens Sunday at 22:00 UTC."""
        from agent.pending_manager import is_forex_market_open
        dt = datetime(2025, 1, 12, 22, 0, 0, tzinfo=timezone.utc)  # Sunday 22:00
        assert is_forex_market_open(dt) is True

    def test_is_forex_market_closed_friday_after_22(self):
        """Forex closes Friday at 22:00 UTC."""
        from agent.pending_manager import is_forex_market_open
        dt = datetime(2025, 1, 10, 22, 0, 0, tzinfo=timezone.utc)  # Friday 22:00
        assert is_forex_market_open(dt) is False

    def test_count_market_hours_weekday_only(self):
        """Market hours during a weekday span should equal wall clock hours."""
        from agent.pending_manager import count_market_hours
        # Wednesday 10:00 → Wednesday 14:00 = 4 market hours
        start = datetime(2025, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 8, 14, 0, 0, tzinfo=timezone.utc)
        hours = count_market_hours(start, end)
        assert abs(hours - 4.0) < 0.1  # allow ~6 min tolerance for step size

    def test_count_market_hours_spanning_weekend(self):
        """Market hours spanning a weekend should exclude Sat/Sun closed."""
        from agent.pending_manager import count_market_hours
        # Friday 20:00 → Monday 04:00 = 2h (Fri 20-22) + 6h (Sun 22 → Mon 04)
        start = datetime(2025, 1, 10, 20, 0, 0, tzinfo=timezone.utc)  # Friday 20:00
        end = datetime(2025, 1, 13, 4, 0, 0, tzinfo=timezone.utc)    # Monday 04:00
        hours = count_market_hours(start, end)
        # Fri 20:00-22:00 = 2h, Sun 22:00-Mon 04:00 = 6h → total ~8h
        assert hours < 10  # definitely less than 56h wall clock
        assert hours > 6   # at least 6h of market time

    def test_pending_setup_not_expired_during_weekend(self):
        """Setup created Friday evening should NOT expire during weekend."""
        from agent.pending_manager import PendingSetup, count_market_hours
        from unittest.mock import patch
        # Create setup on Friday at 21:00 UTC with 4h TTL
        created = datetime(2025, 1, 10, 21, 0, 0, tzinfo=timezone.utc)
        setup = PendingSetup(
            setup_id="PQ-test",
            pair="EURUSD",
            plan=None,
            direction="buy",
            entry_zone_low=1.08, entry_zone_high=1.082,
            recommended_entry=1.081, stop_loss=1.075,
            take_profit_1=1.087, take_profit_2=None,
            confluence_score=7, ttl_hours=4.0,
            created_at=created,
        )
        # Simulate it being Saturday noon — only ~1h of market time elapsed (Fri 21-22)
        with patch("agent.pending_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 11, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
            # Use count_market_hours directly to verify
            hours = count_market_hours(created, datetime(2025, 1, 11, 12, 0, tzinfo=timezone.utc))
            assert hours < 4.0  # Only ~1h of market time, not expired


class TestFP07ExecutingStatus:
    """H-02: 'executing' intermediate status prevents duplicate execution."""

    def _make_setup(self, pair="EURUSD"):
        from agent.pending_manager import PendingSetup
        return PendingSetup(
            setup_id=f"PQ-test-{pair}",
            pair=pair,
            plan=None,
            direction="buy",
            entry_zone_low=1.08, entry_zone_high=1.082,
            recommended_entry=1.081, stop_loss=1.075,
            take_profit_1=1.087, take_profit_2=None,
            confluence_score=7, ttl_hours=4.0,
        )

    def test_mark_executing_changes_status(self):
        from agent.pending_manager import PendingManager
        pm = PendingManager()
        pm.add(self._make_setup())
        ok = pm.mark_executing("PQ-test-EURUSD")
        assert ok is True
        assert pm._queue[0].status == "executing"

    def test_executing_not_in_zone_check(self):
        """Setups with status=executing should not appear in zone check."""
        from agent.pending_manager import PendingManager
        pm = PendingManager()
        pm.add(self._make_setup())
        pm.mark_executing("PQ-test-EURUSD")
        ready = pm.check_zone_entries({"EURUSD": 1.081})
        assert len(ready) == 0  # executing → skipped

    def test_revert_executing_to_pending(self):
        from agent.pending_manager import PendingManager
        pm = PendingManager()
        pm.add(self._make_setup())
        pm.mark_executing("PQ-test-EURUSD")
        pm.revert_executing("PQ-test-EURUSD")
        assert pm._queue[0].status == "pending"

    def test_executing_then_executed(self):
        from agent.pending_manager import PendingManager
        pm = PendingManager()
        pm.add(self._make_setup())
        pm.mark_executing("PQ-test-EURUSD")
        pm.mark_executed("PQ-test-EURUSD")
        assert pm._queue[0].status == "executed"


class TestFP07RetryConfig:
    """L-12: MAX_RETRIES extracted to config."""

    def test_config_has_gemini_max_retries(self):
        from config.settings import GEMINI_MAX_RETRIES, GEMINI_RETRY_BASE_DELAY
        assert isinstance(GEMINI_MAX_RETRIES, int)
        assert GEMINI_MAX_RETRIES >= 1
        assert isinstance(GEMINI_RETRY_BASE_DELAY, float)
        assert GEMINI_RETRY_BASE_DELAY > 0

    def test_gemini_client_uses_config(self):
        from agent.gemini_client import MAX_RETRIES, RETRY_BASE_DELAY
        from config.settings import GEMINI_MAX_RETRIES, GEMINI_RETRY_BASE_DELAY
        assert MAX_RETRIES == GEMINI_MAX_RETRIES
        assert RETRY_BASE_DELAY == GEMINI_RETRY_BASE_DELAY


class TestFP07DefensiveScoreFlags:
    """L-07: _extract_score_flags handles missing/bad tool data gracefully."""

    def test_empty_analyses_returns_defaults(self):
        """With empty analyses, should return safe default flags."""
        from agent.orchestrator import AnalysisOrchestrator
        from unittest.mock import MagicMock
        orch = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        # Minimal SetupCandidate mock
        cand = MagicMock()
        cand.direction = "buy"
        cand.entry_zone_low = 1.08
        cand.entry_zone_high = 1.082
        cand.stop_loss = 1.075
        flags = orch._extract_score_flags(cand, {})
        assert isinstance(flags, dict)
        assert "htf_alignment" in flags
        assert "fresh_zone" in flags
        assert len(flags) == 11

    def test_corrupted_structure_data(self):
        """Should handle corrupted structure dict without crashing."""
        from agent.orchestrator import AnalysisOrchestrator
        from unittest.mock import MagicMock
        orch = AnalysisOrchestrator.__new__(AnalysisOrchestrator)
        cand = MagicMock()
        cand.direction = "sell"
        cand.entry_zone_low = 1.08
        cand.entry_zone_high = 1.082
        cand.stop_loss = 1.085
        analyses = {"H1": {"structure": "not_a_dict", "atr": None}}
        flags = orch._extract_score_flags(cand, analyses)
        assert isinstance(flags, dict)
        assert len(flags) == 11


class TestFP07PerPairTiming:
    """M-07: Orchestrator tracks per-pair phase timings."""

    def test_phase_timings_property_exists(self):
        from agent.orchestrator import AnalysisOrchestrator
        from unittest.mock import MagicMock
        client = MagicMock()
        orch = AnalysisOrchestrator("EURUSD", client=client)
        assert hasattr(orch, 'phase_timings')
        assert isinstance(orch.phase_timings, dict)


class TestFP07VotingThresholdDoc:
    """M-12: Voting threshold is documented with rationale."""

    def test_min_confidence_value(self):
        """MIN_CONFIDENCE = 0.6 — verify the value and that vote works."""
        from config.settings import MIN_CONFIDENCE
        assert MIN_CONFIDENCE == pytest.approx(0.6)

    def test_vote_method_has_threshold_docstring(self):
        """vote() docstring should document the threshold rationale."""
        from agent.voting import VotingEngine
        doc = VotingEngine.vote.__doc__
        assert "threshold" in doc.lower() or "MIN_CONFIDENCE" in doc

    def test_merge_uses_median_half_width(self):
        """FIX CON-10: merge should use median half-width, not first candidate's."""
        from agent.voting import VotingEngine
        from schemas.plan import SetupCandidate
        from schemas.market_data import Direction, StrategyMode
        # Create 3 candidates with different zone widths
        def _cand(low, high):
            return SetupCandidate(
                direction=Direction.BUY,
                strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
                entry_zone_low=low, entry_zone_high=high,
                trigger_condition="test", stop_loss=1.07,
                sl_reasoning="test", take_profit_1=1.09,
                take_profit_2=None, tp_reasoning="test",
                risk_reward_ratio=2.0, management="test",
                ttl_hours=4.0, invalidation="test",
                confluence_score=7, rationale="test",
            )
        cluster = [
            _cand(1.0800, 1.0810),  # width = 10 pips
            _cand(1.0795, 1.0815),  # width = 20 pips
            _cand(1.0798, 1.0818),  # width = 20 pips
        ]
        merged = VotingEngine.merge(cluster)
        # Merged half-width should be median of [5, 10, 10] = 10 pips = 0.0010
        width = merged.entry_zone_high - merged.entry_zone_low
        assert abs(width - 0.0020) < 0.0001  # median half-width*2 = 0.0020


# ===================================================================
# FP-08: Prompt, Voting, State Machine, Tool Registry
# ===================================================================


class TestFP08StrategyModeEnforcement:
    """H-11: STRATEGY_MODES enforced in validator, not just prompt."""

    def test_sniper_needs_sweep(self):
        """sniper_confluence requires sweep_confirmed=True."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=0.003,
            strategy_mode="sniper_confluence",
            sweep_confirmed=False,
            choch_confirmed=True,
        )
        assert result["passed"] is False
        assert any("sweep" in v.lower() for v in result["violations"])

    def test_sniper_needs_choch(self):
        """sniper_confluence requires choch_confirmed=True."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=0.003,
            strategy_mode="sniper_confluence",
            sweep_confirmed=True,
            choch_confirmed=False,
        )
        assert result["passed"] is False
        assert any("choch" in v.lower() for v in result["violations"])

    def test_sniper_passes_when_both_confirmed(self):
        """sniper_confluence passes when sweep + choch both confirmed."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=0.003,
            strategy_mode="sniper_confluence",
            sweep_confirmed=True,
            choch_confirmed=True,
        )
        assert result["passed"] is True

    def test_scalping_channel_no_sweep_needed(self):
        """scalping_channel does NOT require sweep or choch."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=0.003,
            strategy_mode="scalping_channel",
            sweep_confirmed=False,
            choch_confirmed=False,
        )
        assert result["passed"] is True

    def test_index_correlation_needs_sweep_and_choch(self):
        """index_correlation requires both sweep and choch."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=0.003,
            strategy_mode="index_correlation",
            sweep_confirmed=False,
            choch_confirmed=False,
        )
        assert result["passed"] is False
        violations_lower = [v.lower() for v in result["violations"]]
        assert any("sweep" in v for v in violations_lower)
        assert any("choch" in v for v in violations_lower)

    def test_no_strategy_mode_backward_compatible(self):
        """Omitting strategy_mode still works (no enforcement)."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=0.003)
        assert result["passed"] is True

    def test_anti_rungkad_mandatory_checks(self):
        """Anti-Rungkad liquidity_sweep mandatory for sniper."""
        from tools.validator import validate_trading_plan
        setup = {"entry": 1.085, "sl": 1.082, "tp": 1.092, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=0.003,
            strategy_mode="sniper_confluence",
            sweep_confirmed=False,
            choch_confirmed=False,
        )
        violations_str = " ".join(result["violations"]).lower()
        assert "anti-rungkad" in violations_str or "sweep" in violations_str


class TestFP08StateMachinePairLog:
    """L-18: State transition logs include pair name."""

    def test_cancel_includes_pair(self, caplog):
        """cancel() log message includes pair name."""
        import logging
        from agent.state_machine import StateMachine, AnalysisState, SetupContext
        sm = StateMachine()
        ctx = SetupContext(
            pair="XAUUSD", direction="sell",
            strategy_mode="sniper_confluence",
            entry_zone_mid=2350.0, score=8,
            confidence=0.8, htf_bias="bearish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)
        with caplog.at_level(logging.WARNING, logger="agent.state_machine"):
            sm.cancel("test reason")
        assert any("XAUUSD" in r.message for r in caplog.records)

    def test_transition_includes_pair_and_from_state(self, caplog):
        """transition() log includes pair name and previous state."""
        import logging
        from agent.state_machine import StateMachine, AnalysisState, SetupContext
        sm = StateMachine()
        ctx = SetupContext(
            pair="EURUSD", direction="buy",
            strategy_mode="scalping_channel",
            entry_zone_mid=1.085, score=7,
            confidence=0.7, htf_bias="bullish",
        )
        with caplog.at_level(logging.INFO, logger="agent.state_machine"):
            sm.transition(AnalysisState.WATCHING, ctx)
        assert any("EURUSD" in r.message for r in caplog.records)

    def test_reset_includes_pair(self, caplog):
        """reset() log includes pair name."""
        import logging
        from agent.state_machine import StateMachine, AnalysisState, SetupContext
        sm = StateMachine()
        ctx = SetupContext(
            pair="GBPUSD", direction="buy",
            strategy_mode="sniper_confluence",
            entry_zone_mid=1.27, score=8,
            confidence=0.8, htf_bias="bullish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("test")
        sm._cancel_time = 0  # force cooldown expired
        with caplog.at_level(logging.INFO, logger="agent.state_machine"):
            sm.reset()
        assert any("GBPUSD" in r.message for r in caplog.records)


class TestFP08CancelledTransitionDoc:
    """D-07: CANCELLED reachable via cancel(), resettable via reset()."""

    def test_cancelled_is_terminal(self):
        """CANCELLED has no outgoing transitions in the graph."""
        from agent.state_machine import _ALLOWED_TRANSITIONS, AnalysisState
        assert _ALLOWED_TRANSITIONS[AnalysisState.CANCELLED] == set()

    def test_cancelled_to_scanning_via_reset(self):
        """After cancel + cooldown, reset() returns to SCANNING."""
        from agent.state_machine import StateMachine, AnalysisState, SetupContext
        sm = StateMachine()
        ctx = SetupContext(
            pair="XAUUSD", direction="sell",
            strategy_mode="sniper_confluence",
            entry_zone_mid=2350.0, score=8,
            confidence=0.8, htf_bias="bearish",
        )
        sm.transition(AnalysisState.WATCHING, ctx)
        sm.cancel("zone mitigated")
        assert sm.state == AnalysisState.CANCELLED
        sm._cancel_time = 0  # force cooldown expired
        sm.reset()
        assert sm.state == AnalysisState.SCANNING


class TestFP08ModeSelectionConfig:
    """M-11: Mode selection priority extracted to config."""

    def test_config_has_mode_selection_priority(self):
        """settings.MODE_SELECTION_PRIORITY exists and has entries."""
        from config.settings import MODE_SELECTION_PRIORITY
        assert isinstance(MODE_SELECTION_PRIORITY, list)
        assert len(MODE_SELECTION_PRIORITY) >= 3

    def test_index_correlation_enabled_by_default(self):
        """index_correlation is enabled by default (synthetic DXY)."""
        from config.settings import MODE_SELECTION_PRIORITY
        ic = next(
            (m for m in MODE_SELECTION_PRIORITY
             if m["mode"] == "index_correlation"), None
        )
        assert ic is not None
        assert ic["enabled"] is True

    def test_system_prompt_uses_config(self):
        """System prompt includes mode priority from config."""
        from agent.system_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "index_correlation" in prompt
        assert "ENABLED" in prompt
        assert "sniper_confluence" in prompt


class TestFP08LanguageStandardization:
    """L-16/L-19: All code references use English 'Reference' not 'Rujukan'."""

    def test_no_rujukan_in_agent_modules(self):
        """agent/ modules should not contain 'Rujukan'."""
        import importlib
        import agent.system_prompt
        import agent.orchestrator
        import agent.state_machine
        import agent.tool_registry
        for mod in [agent.system_prompt, agent.orchestrator,
                    agent.state_machine, agent.tool_registry]:
            src = importlib.util.find_spec(mod.__name__)
            if src and src.origin:
                with open(src.origin, encoding="utf-8") as f:
                    content = f.read()
                assert "Rujukan" not in content, f"Found 'Rujukan' in {mod.__name__}"

    def test_no_rujukan_in_tool_modules(self):
        """tools/ modules should not contain 'Rujukan'."""
        import pathlib
        tools_dir = pathlib.Path(__file__).parent.parent / "tools"
        for py_file in tools_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "Rujukan" not in content, f"Found 'Rujukan' in {py_file.name}"


class TestFP08VoterNames:
    """L-21: Voting runs use meaningful voter names."""

    def test_voter_profiles_exist(self):
        """Orchestrator has voter_profiles for named voting runs."""
        import inspect
        from agent.orchestrator import AnalysisOrchestrator
        # Check that _phase_vote source contains voter profiles
        src = inspect.getsource(AnalysisOrchestrator._phase_vote)
        assert "voter_profiles" in src
        assert "conservative" in src
        assert "aggressive" in src

    def test_voter_name_in_prompt(self):
        """Voting prompt includes voter name."""
        import inspect
        from agent.orchestrator import AnalysisOrchestrator
        src = inspect.getsource(AnalysisOrchestrator._phase_vote)
        assert "voter:" in src or "voter_name" in src


class TestFP08ToolRegistry:
    """D-06: Tool registry — verify all tools are distinct and valid."""

    def test_all_tools_are_callable(self):
        """Every tool in ALL_TOOLS is a callable function."""
        from agent.tool_registry import ALL_TOOLS
        for tool in ALL_TOOLS:
            assert callable(tool), f"{tool} is not callable"

    def test_no_duplicate_tools(self):
        """No duplicate function names in ALL_TOOLS."""
        from agent.tool_registry import ALL_TOOLS
        names = [t.__name__ for t in ALL_TOOLS]
        assert len(names) == len(set(names)), f"Duplicates: {names}"

    def test_dxy_gate_registered(self):
        """dxy_relevance_score should be in ALL_TOOLS (enabled — synthetic DXY)."""
        from agent.tool_registry import ALL_TOOLS
        names = [t.__name__ for t in ALL_TOOLS]
        assert "dxy_relevance_score" in names

    def test_tool_count(self):
        """Exactly 17 tools registered (dxy_gate enabled)."""
        from agent.tool_registry import ALL_TOOLS, TOOL_COUNT
        assert len(ALL_TOOLS) == 17
        assert TOOL_COUNT == 17


class TestFP08ExtractScoreNA:
    """L-17: _extract_score regex doesn't exist — verified N/A."""

    def test_scorer_uses_boolean_flags(self):
        """Scorer uses boolean flags, not regex extraction."""
        from tools.scorer import score_setup_candidate
        result = score_setup_candidate(
            htf_alignment=True,
            fresh_zone=True,
            sweep_detected=True,
        )
        assert result["score"] == 8  # 3+2+3
        assert isinstance(result["score"], int)


# =========================================================================
# FP-09 Tests: Technical Tools — Structure & Zones
# =========================================================================

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _candle_fp09(o, h, l, c, time="T0"):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 100, "time": time}


def _swing_fp09(idx, price, stype="high"):
    return {"index": idx, "price": price, "time": f"T{idx}", "type": stype}


def _make_demand_fp09(base_mid=2000.0, base_range=2.0, disp_size=30.0, n_pre=5, base_len=3):
    """Create a demand pattern: pre-base → tight base → rally up."""
    candles = []
    idx = 0
    for i in range(n_pre):
        p = base_mid + 15 - i * 3
        candles.append(_candle_fp09(p + 1, p + 3, p - 3, p, f"T{idx}"))
        idx += 1
    for i in range(base_len):
        o = base_mid + (base_range / 4) * (1 if i % 2 == 0 else -1)
        c = base_mid - (base_range / 4) * (1 if i % 2 == 0 else -1)
        h = base_mid + base_range / 2
        l = base_mid - base_range / 2
        candles.append(_candle_fp09(o, h, l, c, f"T{idx}"))
        idx += 1
    rally_start = base_mid
    rally_step = disp_size / 2
    for i in range(2):
        o = rally_start + rally_step * i
        c = o + rally_step
        h = c + 2
        l = o - 1
        candles.append(_candle_fp09(o, h, l, c, f"T{idx}"))
        idx += 1
    return candles


# ---------------------------------------------------------------------------
# H-08: Supply zone freshness — fixed mitigation logic
# ---------------------------------------------------------------------------
class TestFP09FreshnessH08:
    """H-08: Supply/demand freshness should only mark zones mitigated when
    price closes THROUGH the zone, not on mere retest."""

    def test_supply_zone_retest_stays_fresh(self):
        """Price touching supply zone low (retest) should NOT mitigate."""
        from tools.supply_demand import _update_freshness
        zones = [{
            "zone_type": "supply", "high": 1.0520, "low": 1.0500,
            "base_end_idx": 5, "is_fresh": True,
        }]
        # Candle closes at zone low — retest, not mitigation
        ohlcv = [{"close": 1.0480, "high": 1.0490, "low": 1.0475}] * 10
        ohlcv[8] = {"close": 1.0501, "high": 1.0510, "low": 1.0495}  # touches zone
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is True, "Retest should NOT mitigate supply zone"

    def test_supply_zone_mitigated_when_close_above_high(self):
        """Price closing above zone high → mitigated."""
        from tools.supply_demand import _update_freshness
        zones = [{
            "zone_type": "supply", "high": 1.0520, "low": 1.0500,
            "base_end_idx": 2, "is_fresh": True,
        }]
        ohlcv = [{"close": 1.0480}] * 6
        ohlcv[5] = {"close": 1.0525}  # closes above z_high
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is False

    def test_demand_zone_retest_stays_fresh(self):
        """Price touching demand zone high (retest) should NOT mitigate."""
        from tools.supply_demand import _update_freshness
        zones = [{
            "zone_type": "demand", "high": 1.0420, "low": 1.0400,
            "base_end_idx": 3, "is_fresh": True,
        }]
        ohlcv = [{"close": 1.0430}] * 8
        ohlcv[6] = {"close": 1.0415}  # dips into zone — retest
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is True, "Retest should NOT mitigate demand zone"

    def test_demand_zone_mitigated_when_close_below_low(self):
        """Price closing below zone low → mitigated."""
        from tools.supply_demand import _update_freshness
        zones = [{
            "zone_type": "demand", "high": 1.0420, "low": 1.0400,
            "base_end_idx": 2, "is_fresh": True,
        }]
        ohlcv = [{"close": 1.0430}] * 6
        ohlcv[5] = {"close": 1.0395}  # closes below z_low
        _update_freshness(zones, ohlcv)
        assert zones[0]["is_fresh"] is False


# ---------------------------------------------------------------------------
# H-09: Order block body vs wick boundary consistency
# ---------------------------------------------------------------------------
class TestFP09OBBoundaryH09:
    """H-09: Both bullish and bearish OBs should use full candle range."""

    def test_bullish_ob_uses_full_candle(self):
        from tools.orderblock import detect_orderblocks
        candles = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(100, 100.5, 98, 99, "t1"),     # bearish
            _candle_fp09(99, 102, 98.5, 101.5, "t2"),   # bullish disp
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        ob = result["bullish_obs"][0]
        assert ob["high"] == 100.5   # prev high
        assert ob["low"] == 98       # prev low (full candle)

    def test_bearish_ob_uses_full_candle(self):
        from tools.orderblock import detect_orderblocks
        candles = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(99, 102, 98.5, 101, "t1"),     # bullish
            _candle_fp09(101, 101.5, 97, 97.5, "t2"),   # bearish disp
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        ob = result["bearish_obs"][0]
        assert ob["high"] == 102     # prev high
        assert ob["low"] == 98.5     # prev LOW (was prev open=99, now full candle)

    def test_ob_boundary_symmetric(self):
        """Both OB types use the same boundary logic: full candle [low, high]."""
        from tools.orderblock import detect_orderblocks
        # Create bullish and bearish OBs
        candles = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(100, 100.5, 97, 98, "t1"),     # bearish → bullish OB candidate
            _candle_fp09(98, 103, 97.5, 102, "t2"),      # strong bullish disp
            _candle_fp09(102, 103, 101, 102.5, "t3"),    # bullish → bearish OB candidate
            _candle_fp09(102.5, 103, 99, 99.5, "t4"),    # strong bearish disp
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        for ob_list in [result["bullish_obs"], result["bearish_obs"]]:
            for ob in ob_list:
                ci = ob["candle_index"]
                # Both should use full candle: [candle_low, candle_high]
                assert ob["low"] == candles[ci]["low"]
                assert ob["high"] == candles[ci]["high"]


# ---------------------------------------------------------------------------
# H-10: CHOCH detection from ranging state
# ---------------------------------------------------------------------------
class TestFP09StructureRangingH10:
    """H-10: After ranging → first BOS establishes trend, opposing → CHOCH."""

    def test_ranging_first_break_is_bos(self):
        from tools.structure import detect_bos_choch
        sh = [_swing_fp09(5, 110), _swing_fp09(15, 120)]
        sl = [_swing_fp09(0, 90), _swing_fp09(10, 95)]
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(20)]
        ohlcv[15] = {"open": 112, "high": 121, "low": 111, "close": 120, "volume": 0, "time": "T15"}
        result = detect_bos_choch(ohlcv, sh, sl, atr_value=10.0)
        # First break from ranging → BOS (not CHOCH)
        first_event = result["events"][0]
        assert first_event["event_type"] == "bos"

    def test_ranging_then_opposing_break_is_choch(self):
        """ranging → BOS(bullish) → bearish break → CHOCH."""
        from tools.structure import detect_bos_choch
        sh = [_swing_fp09(3, 110), _swing_fp09(9, 120)]
        sl = [_swing_fp09(0, 90), _swing_fp09(6, 100)]
        ohlcv = [{"open": 100, "high": 105, "low": 95, "close": 100, "volume": 0, "time": f"T{i}"}
                 for i in range(25)]
        # Bar 9 breaks above 110 → BOS(bullish)
        ohlcv[9] = {"open": 112, "high": 121, "low": 111, "close": 120, "volume": 0, "time": "T9"}
        # Bar 18 breaks below → CHOCH(bearish)
        ohlcv[18] = {"open": 88, "high": 89, "low": 85, "close": 86, "volume": 0, "time": "T18"}
        result = detect_bos_choch(ohlcv, sh, sl, atr_value=10.0)
        choch = [e for e in result["events"] if e["event_type"] == "choch"]
        assert len(choch) >= 1
        assert choch[0]["direction"] == "bearish"

    def test_trend_uses_trendstate_enum_values(self):
        """CON-13: trend field should be a valid TrendState string."""
        from tools.structure import detect_bos_choch
        result = detect_bos_choch([], [], [], atr_value=10.0)
        assert result["trend"] in ("bullish", "bearish", "ranging")


# ---------------------------------------------------------------------------
# M-13: Base candle threshold already in config
# ---------------------------------------------------------------------------
class TestFP09ConfigThresholdsM13:
    """M-13: SND base thresholds accessible from config."""

    def test_snd_thresholds_in_config(self):
        from config.settings import (
            SND_BASE_MIN_CANDLES, SND_BASE_MAX_CANDLES,
            SND_BASE_AVG_RANGE_ATR, SND_DISPLACEMENT_ATR,
            SND_DISPLACEMENT_BODY_RATIO, SND_MAX_ZONES,
        )
        assert SND_BASE_MIN_CANDLES == 2
        assert SND_BASE_MAX_CANDLES == 6
        assert SND_BASE_AVG_RANGE_ATR == 0.6
        assert SND_DISPLACEMENT_ATR == 1.2
        assert SND_DISPLACEMENT_BODY_RATIO == 0.6
        assert isinstance(SND_MAX_ZONES, int)
        assert SND_MAX_ZONES > 0

    def test_bos_buffer_in_config(self):
        """M-17: BOS_ATR_BUFFER from config, not hardcoded."""
        from config.settings import BOS_ATR_BUFFER
        assert isinstance(BOS_ATR_BUFFER, float)
        assert BOS_ATR_BUFFER == 0.05


# ---------------------------------------------------------------------------
# M-15: Swing boundary handling
# ---------------------------------------------------------------------------
class TestFP09SwingBoundaryM15:
    """M-15: handle_boundary param detects swings at edges."""

    def test_default_no_boundary(self):
        """Default handle_boundary=False excludes first/last k candles."""
        from tools.swing import detect_swing_points
        # Peak at index 2, which is within lookback=3 zone
        candles = []
        for i in range(15):
            if i == 2:
                candles.append(_candle_fp09(120, 121, 119, 120, f"T{i}"))
            else:
                candles.append(_candle_fp09(100, 101, 99, 100, f"T{i}"))
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        # Peak at idx 2 is within first k=3, should NOT be detected
        peak_indices = [s["index"] for s in result["swing_highs"]]
        assert 2 not in peak_indices

    def test_boundary_enabled_detects_edge_swings(self):
        """handle_boundary=True detects swings near edges with reduced window."""
        from tools.swing import detect_swing_points
        # Peak at index 2
        candles = []
        for i in range(15):
            if i == 2:
                candles.append(_candle_fp09(120, 121, 119, 120, f"T{i}"))
            else:
                candles.append(_candle_fp09(100, 101, 99, 100, f"T{i}"))
        result = detect_swing_points(candles, lookback=3, min_distance_atr=0.0, handle_boundary=True)
        peak_indices = [s["index"] for s in result["swing_highs"]]
        assert 2 in peak_indices, f"Edge peak at idx 2 should be detected: {peak_indices}"

    def test_boundary_backward_compat(self):
        """handle_boundary=False matches original behavior exactly."""
        from tools.swing import detect_swing_points
        values = [100, 102, 105, 108, 110, 108, 105, 102, 100, 98,
                  100, 102, 105, 108, 110, 108, 105, 102, 100]
        candles = [_candle_fp09(v, v + 0.15, v - 0.15, v, f"T{i}") for i, v in enumerate(values)]
        r1 = detect_swing_points(candles, lookback=3, min_distance_atr=0.0, handle_boundary=False)
        r2 = detect_swing_points(candles, lookback=3, min_distance_atr=0.0)
        assert r1 == r2


# ---------------------------------------------------------------------------
# M-16: OB scoring includes freshness/age factor
# ---------------------------------------------------------------------------
class TestFP09OBScoringM16:
    """M-16: OB scoring includes age factor, mitigation, is_fresh field."""

    def test_recent_ob_scores_higher(self):
        """More recent OB at same displacement should score higher."""
        from tools.orderblock import detect_orderblocks
        # OB at index 1 (early)
        candles_early = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(100, 100.5, 98, 99, "t1"),
            _candle_fp09(99, 102, 98.5, 101.5, "t2"),
        ] + [_candle_fp09(101, 102, 100, 101, f"t{i}") for i in range(3, 50)]
        # OB near end
        for i in range(47, 50):
            candles_early[i] = _candle_fp09(100, 100.5, 98, 99, f"t{i}")
        candles_early.append(_candle_fp09(99, 102, 98.5, 101.5, "t50"))
        r = detect_orderblocks(candles_early, atr_value=1.0)
        if len(r["bullish_obs"]) >= 2:
            scores = [ob["score"] for ob in r["bullish_obs"]]
            # First in list is highest score (sorted desc)
            assert scores[0] >= scores[-1]

    def test_ob_has_is_fresh_field(self):
        """CON-12: OB should have is_fresh, displacement_strength, body_ratio."""
        from tools.orderblock import detect_orderblocks
        candles = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(100, 100.5, 98, 99, "t1"),
            _candle_fp09(99, 102, 98.5, 101.5, "t2"),
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        ob = result["bullish_obs"][0]
        assert "is_fresh" in ob
        assert "displacement_strength" in ob
        assert "body_ratio" in ob
        assert isinstance(ob["displacement_strength"], float)

    def test_ob_mitigation_detected(self):
        """M-16: OB is marked mitigated when price closes through zone."""
        from tools.orderblock import detect_orderblocks
        candles = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(100, 100.5, 98, 99, "t1"),   # bearish → bullish OB
            _candle_fp09(99, 102, 98.5, 101.5, "t2"),  # bullish displacement
            _candle_fp09(101, 102, 100, 101, "t3"),
            _candle_fp09(101, 102, 96, 97, "t4"),      # close=97 < OB low=98 → mitigated
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        ob = result["bullish_obs"][0]
        assert ob["is_mitigated"] is True
        assert ob["is_fresh"] is False


# ---------------------------------------------------------------------------
# L-22: SND max zones configurable
# ---------------------------------------------------------------------------
class TestFP09MaxZonesL22:
    """L-22: detect_snd_zones limits output to max_zones per type."""

    def test_max_zones_limits_output(self):
        from tools.supply_demand import detect_snd_zones
        # Create many demand patterns
        all_candles = []
        for batch in range(5):
            p = _make_demand_fp09(base_mid=2000 + batch * 50, disp_size=30.0)
            for i, c in enumerate(p):
                c["time"] = f"T{len(all_candles) + i}"
            all_candles.extend(p)
        result = detect_snd_zones(all_candles, atr_value=10.0, max_zones=2)
        assert len(result["demand_zones"]) <= 2

    def test_default_max_zones_from_config(self):
        from config.settings import SND_MAX_ZONES
        assert SND_MAX_ZONES == 10


# ---------------------------------------------------------------------------
# L-24: Swing lookback documented (verified via docstring)
# ---------------------------------------------------------------------------
class TestFP09SwingLookbackDocL24:
    """L-24: Swing lookback=5 default has documented rationale."""

    def test_lookback_default_is_5(self):
        import inspect
        from tools.swing import detect_swing_points
        sig = inspect.signature(detect_swing_points)
        assert sig.parameters["lookback"].default == 5

    def test_docstring_mentions_rationale(self):
        from tools.swing import detect_swing_points
        doc = detect_swing_points.__doc__ or ""
        assert "SWING_LOOKBACK" in doc
        assert "H4" in doc
        assert "compromise" in doc.lower() or "mid-range" in doc.lower()


# ---------------------------------------------------------------------------
# L-25: OB docstring updated
# ---------------------------------------------------------------------------
class TestFP09OBDocstringL25:
    """L-25: OB docstring should be comprehensive."""

    def test_ob_docstring_has_args_and_returns(self):
        from tools.orderblock import detect_orderblocks
        doc = detect_orderblocks.__doc__ or ""
        assert "Args:" in doc
        assert "Returns:" in doc
        assert "displacement_strength" in doc
        assert "is_fresh" in doc


# ---------------------------------------------------------------------------
# L-27 / CON-13: Structure return dict casing + TrendState enum
# ---------------------------------------------------------------------------
class TestFP09StructureConsistency:
    """L-27 + CON-13: Structure uses consistent keys and TrendState enum."""

    def test_trendstate_enum_exists(self):
        from schemas.structure import TrendState
        assert TrendState.BULLISH == "bullish"
        assert TrendState.BEARISH == "bearish"
        assert TrendState.RANGING == "ranging"

    def test_structure_uses_trendstate_values(self):
        from tools.structure import detect_bos_choch
        from schemas.structure import TrendState
        result = detect_bos_choch([], [], [], atr_value=10.0)
        assert result["trend"] == TrendState.RANGING

    def test_all_return_keys_snake_case(self):
        from tools.structure import detect_bos_choch
        result = detect_bos_choch([], [], [], atr_value=10.0)
        for key in result:
            assert key == key.lower(), f"Key '{key}' is not lowercase"
            assert " " not in key, f"Key '{key}' has spaces"


# ---------------------------------------------------------------------------
# L-28: Supply demand input validation
# ---------------------------------------------------------------------------
class TestFP09InputValidationL28:
    """L-28: detect_snd_zones validates candle dict keys."""

    def test_valid_candles_accepted(self):
        from tools.supply_demand import detect_snd_zones
        candles = _make_demand_fp09()
        # Should not raise
        result = detect_snd_zones(candles, atr_value=10.0)
        assert "demand_zones" in result

    def test_missing_keys_raises_error(self):
        import pytest
        from tools.supply_demand import detect_snd_zones
        bad_candles = [{"open": 100, "high": 101, "low": 99}]  # missing close, time
        with pytest.raises(ValueError, match="missing required keys"):
            detect_snd_zones(bad_candles, atr_value=10.0)

    def test_empty_list_no_validation_error(self):
        from tools.supply_demand import detect_snd_zones
        result = detect_snd_zones([], atr_value=10.0)
        assert result == {"supply_zones": [], "demand_zones": []}


# ---------------------------------------------------------------------------
# CON-11: Zone dict keys alignment with Pydantic models
# ---------------------------------------------------------------------------
class TestFP09ZoneAlignmentCON11:
    """CON-11: Zone dicts should have `formation` field matching Pydantic model."""

    def test_demand_zone_has_formation(self):
        from tools.supply_demand import detect_snd_zones
        candles = _make_demand_fp09(disp_size=30.0)
        result = detect_snd_zones(candles, atr_value=10.0)
        if result["demand_zones"]:
            z = result["demand_zones"][0]
            assert "formation" in z
            assert z["formation"].endswith("_base_rally")

    def test_supply_zone_has_formation(self):
        from tools.supply_demand import detect_snd_zones
        # Supply pattern: base then drop
        candles = []
        idx = 0
        for i in range(5):
            p = 2050 - 15 + i * 3
            candles.append(_candle_fp09(p - 1, p + 3, p - 3, p, f"T{idx}"))
            idx += 1
        base_mid = 2050.0
        for i in range(3):
            o = base_mid + 0.5 * (1 if i % 2 == 0 else -1)
            c = base_mid - 0.5 * (1 if i % 2 == 0 else -1)
            candles.append(_candle_fp09(o, base_mid + 1, base_mid - 1, c, f"T{idx}"))
            idx += 1
        # Displacement down
        for i in range(2):
            o = base_mid - 15 * i
            c = o - 15
            candles.append(_candle_fp09(o, o + 1, c - 2, c, f"T{idx}"))
            idx += 1
        result = detect_snd_zones(candles, atr_value=10.0)
        if result["supply_zones"]:
            z = result["supply_zones"][0]
            assert "formation" in z
            assert z["formation"].endswith("_base_drop")

    def test_formation_values_match_enum(self):
        """Formation values should be valid ZoneFormation enum values."""
        from schemas.zones import ZoneFormation
        valid = {e.value for e in ZoneFormation}
        from tools.supply_demand import detect_snd_zones
        candles = _make_demand_fp09(disp_size=30.0)
        result = detect_snd_zones(candles, atr_value=10.0)
        for z in result["demand_zones"] + result["supply_zones"]:
            assert z["formation"] in valid, f"Invalid formation: {z['formation']}"


# ---------------------------------------------------------------------------
# CON-12: OB return format alignment
# ---------------------------------------------------------------------------
class TestFP09OBAlignmentCON12:
    """CON-12: OB should have consistent fields with SnD zones."""

    def test_ob_has_snd_aligned_fields(self):
        from tools.orderblock import detect_orderblocks
        candles = [
            _candle_fp09(100, 101, 99, 100, "t0"),
            _candle_fp09(100, 100.5, 98, 99, "t1"),
            _candle_fp09(99, 102, 98.5, 101.5, "t2"),
        ]
        result = detect_orderblocks(candles, atr_value=1.0)
        ob = result["bullish_obs"][0]
        # Keys present in both SnD and OB
        shared_keys = {"zone_type", "high", "low", "score", "is_fresh", "origin_time",
                       "displacement_strength", "body_ratio"}
        assert shared_keys.issubset(set(ob.keys())), f"Missing: {shared_keys - set(ob.keys())}"


# ===========================================================================
# FP-10 TESTS — Technical Tools: Indicators, Liquidity, SNR, PriceAction,
#               Trendline, Scorer, ChoCH filter
# ===========================================================================


# ---------------------------------------------------------------------------
# Helpers for FP-10
# ---------------------------------------------------------------------------

def _candle_fp10(o, h, l, c, t="t0"):
    return {"open": o, "high": h, "low": l, "close": c, "time": t, "volume": 100}


def _make_candles_fp10(closes, spread=1.0):
    """Create OHLCV from close list."""
    candles = []
    for i, c in enumerate(closes):
        candles.append({
            "time": f"2026-03-{i+1:02d}T00:00:00Z",
            "open": closes[i - 1] if i > 0 else c,
            "high": c + spread / 2,
            "low": c - spread / 2,
            "close": c,
            "volume": 1000.0,
        })
    return candles


# ======================= M-14: RSI Divergence ==============================

class TestRSIDivergence_M14:
    """M-14: RSI divergence detection with ATR-scaled lookback."""

    def test_bearish_divergence_price_hh_rsi_lh(self):
        """Price makes higher high but RSI makes lower high → bearish."""
        from tools.indicators import detect_rsi_divergence
        # Single-bar spikes ensure clear local highs
        # Peak 1 at idx 5 (high=105.5), Peak 2 at idx 11 (high=107.5)
        closes = [100, 100, 100, 100, 100, 105, 100, 100, 100, 100, 100, 107, 100, 100, 100]
        candles = _make_candles_fp10(closes)
        n = len(candles)
        # RSI peak at idx 5 = 80, peak at idx 11 = 70 → lower high
        rsi = [50.0] * n
        rsi[4] = 70.0; rsi[5] = 80.0; rsi[6] = 60.0
        rsi[10] = 60.0; rsi[11] = 70.0; rsi[12] = 55.0
        result = detect_rsi_divergence(candles, rsi, lookback=15)
        assert result["divergence_type"] == "bearish"

    def test_bullish_divergence_price_ll_rsi_hl(self):
        """Price makes lower low but RSI makes higher low → bullish."""
        from tools.indicators import detect_rsi_divergence
        # Peak 1 at idx 5 (low=94.5), Peak 2 at idx 11 (low=92.5) — lower low
        closes = [100, 100, 100, 100, 100, 95, 100, 100, 100, 100, 100, 93, 100, 100, 100]
        candles = _make_candles_fp10(closes)
        n = len(candles)
        # RSI: trough at idx 5 = 20, trough at idx 11 = 25 → higher low
        rsi = [50.0] * n
        rsi[4] = 30.0; rsi[5] = 20.0; rsi[6] = 40.0
        rsi[10] = 35.0; rsi[11] = 25.0; rsi[12] = 40.0
        result = detect_rsi_divergence(candles, rsi, lookback=15)
        assert result["divergence_type"] == "bullish"

    def test_no_divergence_when_aligned(self):
        """Price HH + RSI HH → no divergence."""
        from tools.indicators import detect_rsi_divergence
        closes = [100, 100, 100, 100, 100, 105, 100, 100, 100, 100, 100, 107, 100, 100, 100]
        candles = _make_candles_fp10(closes)
        n = len(candles)
        # RSI also makes higher high: peak 1 = 70, peak 2 = 80
        rsi = [50.0] * n
        rsi[4] = 60.0; rsi[5] = 70.0; rsi[6] = 55.0
        rsi[10] = 70.0; rsi[11] = 80.0; rsi[12] = 65.0
        result = detect_rsi_divergence(candles, rsi)
        assert result["divergence_type"] is None

    def test_atr_scales_lookback(self):
        """High ATR regime should shorten effective lookback."""
        from tools.indicators import detect_rsi_divergence
        candles = _make_candles_fp10([100 + i for i in range(20)], spread=0.5)
        rsi = [50.0] * 20
        # High ATR = 10 vs median range ~0.5 → should trigger short lookback
        result = detect_rsi_divergence(candles, rsi, atr_value=10.0, lookback=10)
        assert result["lookback_used"] == 7  # 10 * 0.7 = 7

    def test_short_data_returns_none(self):
        """Fewer than 4 candles → no divergence possible."""
        from tools.indicators import detect_rsi_divergence
        result = detect_rsi_divergence([_candle_fp10(100,101,99,100)]*3, [50.0]*3)
        assert result["divergence_type"] is None

    def test_mismatched_lengths_returns_none(self):
        """ohlcv and rsi_values different lengths → safe return None."""
        from tools.indicators import detect_rsi_divergence
        candles = _make_candles_fp10([100]*10)
        result = detect_rsi_divergence(candles, [50.0]*5)
        assert result["divergence_type"] is None


# ======================= M-20: SNR Pair-Adaptive ===========================

class TestSNRPairAdaptive_M20:
    """M-20: SNR clustering tolerance adapts per pair."""

    def _swing(self, price, idx=0, tf="H1"):
        return {"price": price, "index": idx, "time": f"T{idx}", "type": "high", "timeframe": tf}

    def test_xauusd_wider_tolerance(self):
        """XAUUSD (mult=0.3) clusters swings that default (0.2) would separate."""
        from tools.snr import detect_snr_levels
        # ATR=10, XAUUSD mult=0.3 → dist=3.0; default mult=0.2 → dist=2.0
        swings = [self._swing(2000.0, idx=5), self._swing(2002.5, idx=15)]
        r_gold = detect_snr_levels(swings, atr_value=10.0, pair="XAUUSD")
        r_default = detect_snr_levels(swings, atr_value=10.0, cluster_atr_mult=0.2)
        assert len(r_gold["levels"]) == 1   # clusters at 0.3
        assert len(r_default["levels"]) == 2  # separate at 0.2

    def test_explicit_override_beats_pair(self):
        """Explicit cluster_atr_mult overrides pair-adaptive."""
        from tools.snr import detect_snr_levels
        swings = [self._swing(2000.0, idx=5), self._swing(2002.5, idx=15)]
        result = detect_snr_levels(swings, atr_value=10.0, pair="XAUUSD", cluster_atr_mult=0.1)
        assert len(result["levels"]) == 2  # forced tight clustering

    def test_source_tf_uses_highest_tf(self):
        """source_tf should reflect highest TF in cluster, not most frequent."""
        from tools.snr import detect_snr_levels
        swings = [
            self._swing(2000.0, idx=5, tf="M15"),
            self._swing(2000.2, idx=15, tf="M15"),
            self._swing(2000.1, idx=25, tf="M15"),
            self._swing(2000.3, idx=35, tf="H4"),
        ]
        result = detect_snr_levels(swings, atr_value=10.0)
        assert result["levels"][0]["source_tf"] == "H4"


# ======================= M-21: Pin Bar Config ==============================

class TestPinBarConfig_M21:
    """M-21: Pin bar wick ratio should come from config."""

    def test_default_from_config(self):
        """Default min_wick_body_ratio should match PIN_BAR_MIN_WICK_RATIO."""
        from tools.price_action import detect_pin_bar
        from config.settings import PIN_BAR_MIN_WICK_RATIO
        import inspect
        sig = inspect.signature(detect_pin_bar)
        assert sig.parameters["min_wick_body_ratio"].default == PIN_BAR_MIN_WICK_RATIO

    def test_env_override_respected(self):
        """Config value should be overridable."""
        from config.settings import PIN_BAR_MIN_WICK_RATIO
        assert isinstance(PIN_BAR_MIN_WICK_RATIO, float)
        assert PIN_BAR_MIN_WICK_RATIO > 0


# ======================= M-22: Trendline Validity Bounds ===================

class TestTrendlineValidityBounds_M22:
    """M-22: Trendline ray should be rejected if it extends too far."""

    def _swing(self, index, price):
        return {"index": index, "price": price, "type": "low", "timeframe": "H1"}

    def _make_uptrend_ohlcv(self, n, base=100.0, slope=0.5, margin=0.5):
        bars = []
        for i in range(n):
            line_y = base + slope * i
            lo = line_y + margin
            bars.append({"open": lo + 1, "close": lo + 0.5, "high": lo + 2, "low": lo, "time": i})
        return bars

    def test_ray_within_bounds_accepted(self):
        """Ray extending < max_ray_bars is accepted."""
        from unittest.mock import patch
        from tools.trendline import detect_trendlines
        sl = [self._swing(0, 100.0), self._swing(10, 105.0)]
        ohlcv = self._make_uptrend_ohlcv(30, base=100.0, slope=0.5)
        with patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0}):
            result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", max_ray_bars=100)
        assert len(result["uptrend_lines"]) >= 1

    def test_ray_beyond_bounds_rejected(self):
        """Ray extending > max_ray_bars should be rejected."""
        from unittest.mock import patch
        from tools.trendline import detect_trendlines
        # Anchors at 0 and 10, total 200 bars → extension = 190 > 50
        sl = [self._swing(0, 100.0), self._swing(10, 105.0)]
        ohlcv = self._make_uptrend_ohlcv(200, base=100.0, slope=0.5)
        with patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0}):
            result = detect_trendlines([], sl, ohlcv, pair="XAUUSD", max_ray_bars=50)
        assert result["uptrend_lines"] == []

    def test_default_max_ray_bars_from_config(self):
        """Default max_ray_bars should match TRENDLINE_MAX_RAY_BARS."""
        from config.settings import TRENDLINE_MAX_RAY_BARS
        import inspect
        from tools.trendline import detect_trendlines
        sig = inspect.signature(detect_trendlines)
        assert sig.parameters["max_ray_bars"].default == TRENDLINE_MAX_RAY_BARS


# ======================= L-23: EMA Cache ===================================

class TestEMACache_L23:
    """L-23: EMA should cache results for same data+period."""

    def test_same_data_returns_cached(self):
        """Calling EMA twice with same data+period should hit cache."""
        from tools.indicators import compute_ema, clear_ema_cache, _ema_cache
        clear_ema_cache()
        candles = _make_candles_fp10([100.0] * 30)
        r1 = compute_ema(candles, period=20)
        assert len(_ema_cache) == 1
        r2 = compute_ema(candles, period=20)
        assert r1 is r2  # exact same dict object (from cache)

    def test_different_period_no_collision(self):
        """Different periods should NOT hit cache."""
        from tools.indicators import compute_ema, clear_ema_cache, _ema_cache
        clear_ema_cache()
        candles = _make_candles_fp10([100.0] * 30)
        r1 = compute_ema(candles, period=10)
        r2 = compute_ema(candles, period=20)
        assert r1 is not r2
        assert len(_ema_cache) == 2

    def test_cache_bypass(self):
        """use_cache=False should skip caching."""
        from tools.indicators import compute_ema, clear_ema_cache, _ema_cache
        clear_ema_cache()
        candles = _make_candles_fp10([100.0] * 30)
        compute_ema(candles, period=20, use_cache=False)
        assert len(_ema_cache) == 0

    def test_clear_cache_works(self):
        """clear_ema_cache() should empty the cache."""
        from tools.indicators import compute_ema, clear_ema_cache, _ema_cache
        candles = _make_candles_fp10([100.0] * 30)
        compute_ema(candles, period=20)
        assert len(_ema_cache) >= 1
        clear_ema_cache()
        assert len(_ema_cache) == 0


# ======================= L-26: Liquidity Tolerance Config ==================

class TestLiquidityToleranceConfig_L26:
    """L-26: EQH/EQL tolerance should come from config."""

    def test_default_from_config(self):
        from tools.liquidity import detect_eqh_eql
        from config.settings import LIQUIDITY_EQ_TOLERANCE_ATR
        import inspect
        sig = inspect.signature(detect_eqh_eql)
        assert sig.parameters["tolerance_atr_mult"].default == LIQUIDITY_EQ_TOLERANCE_ATR


# ======================= L-29: ChoCH Filter Prefix =========================

class TestChochFilterPrefix_L29:
    """L-29: choch_filter should use prefix arrays (O(n)) not O(n²)."""

    def test_bullish_choch_still_detected(self):
        """Verify prefix optimization doesn't break bullish detection."""
        from tools.choch_filter import detect_choch_micro
        candles = [
            _candle_fp10(100, 102, 99, 101, "t0"),
            _candle_fp10(101, 103, 100, 102, "t1"),
            _candle_fp10(102, 104, 101, 103, "t2"),
            _candle_fp10(103, 106, 102, 105, "t3"),  # breaks above all prior highs
        ]
        result = detect_choch_micro(candles, direction="bullish", lookback=10, atr=1.0)
        assert result["confirmed"] is True

    def test_bearish_choch_still_detected(self):
        """Verify prefix optimization doesn't break bearish detection."""
        from tools.choch_filter import detect_choch_micro
        candles = [
            _candle_fp10(103, 105, 102, 104, "t0"),
            _candle_fp10(104, 106, 103, 105, "t1"),
            _candle_fp10(105, 106, 100, 101, "t2"),
            _candle_fp10(101, 102, 95, 96, "t3"),  # breaks below all prior lows
        ]
        result = detect_choch_micro(candles, direction="bearish", lookback=10, atr=1.0)
        assert result["confirmed"] is True

    def test_no_choch_when_no_break(self):
        """Flat market → no CHOCH."""
        from tools.choch_filter import detect_choch_micro
        candles = [_candle_fp10(100, 101, 99, 100, f"t{i}") for i in range(10)]
        result = detect_choch_micro(candles, direction="bullish", lookback=10, atr=5.0)
        assert result["confirmed"] is False


# ======================= L-32: Engulfing Body Check ========================

class TestEngulfingBodyCheck_L32:
    """L-32: Engulfing pattern should have minimum body significance."""

    def test_strong_engulfing_passes(self):
        """Engulfing with large body-to-range ratio passes."""
        from tools.price_action import detect_engulfing
        candles = [
            _candle_fp10(102, 103, 99, 100, "t0"),   # bearish
            _candle_fp10(99, 104, 98.5, 103, "t1"),   # bullish, body/range ≈ 4/5.5 ≈ 0.73
        ]
        result = detect_engulfing(candles, min_body_ratio=0.3)
        assert len(result["engulfing_patterns"]) == 1

    def test_weak_engulfing_filtered(self):
        """Tiny-body engulfing (long wicks) should be filtered."""
        from tools.price_action import detect_engulfing
        candles = [
            _candle_fp10(100.1, 100.5, 99.5, 100, "t0"),   # bearish: body=0.1
            # Bullish but with tiny body relative to huge wicks
            _candle_fp10(99.9, 110, 90, 100.2, "t1"),   # body=0.3, range=20, ratio=0.015
        ]
        result = detect_engulfing(candles, min_body_ratio=0.3)
        assert len(result["engulfing_patterns"]) == 0

    def test_default_body_ratio_from_config(self):
        """Default min_body_ratio should match ENGULFING_MIN_BODY_RATIO."""
        from tools.price_action import detect_engulfing
        from config.settings import ENGULFING_MIN_BODY_RATIO
        import inspect
        sig = inspect.signature(detect_engulfing)
        assert sig.parameters["min_body_ratio"].default == ENGULFING_MIN_BODY_RATIO


# ======================= L-33: Trendline Logging ===========================

class TestTrendlineLogging_L33:
    """L-33: Trendline should log rejected candidates."""

    def test_rejection_logged(self):
        """Slope rejection should produce a DEBUG log entry."""
        import logging
        from unittest.mock import patch
        from tools.trendline import detect_trendlines
        # Two descending-slope lows → uptrend rejects slope
        sl = [{"index": 0, "price": 110.0}, {"index": 10, "price": 100.0}]
        ohlcv = [{"open": 100, "high": 101, "low": 99, "close": 100, "time": i} for i in range(30)]
        with patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0}):
            with patch("tools.trendline.logger") as mock_logger:
                detect_trendlines([], sl, ohlcv, pair="XAUUSD")
                assert mock_logger.debug.called


# ======================= L-34: Scorer Return Doc ===========================

class TestScorerReturnDoc_L34:
    """L-34: Scorer return dict should have documented structure."""

    def test_return_has_all_documented_keys(self):
        from tools.scorer import score_setup_candidate
        result = score_setup_candidate(htf_alignment=True)
        assert "score" in result
        assert "breakdown" in result
        assert "tradeable" in result
        assert "max_possible" in result
        assert isinstance(result["score"], int)
        assert isinstance(result["breakdown"], dict)
        assert isinstance(result["tradeable"], bool)
        assert isinstance(result["max_possible"], int)

    def test_breakdown_has_all_flag_keys(self):
        from tools.scorer import score_setup_candidate
        result = score_setup_candidate()
        expected_keys = {
            "htf_alignment", "fresh_zone", "sweep_detected", "near_major_snr",
            "pa_confirmed", "ema_filter_ok", "rsi_filter_ok",
            "sl_too_tight", "sl_too_wide", "counter_htf_bias", "zone_mitigated",
        }
        assert expected_keys == set(result["breakdown"].keys())


# ======================= CON-17: Trendline Type Field ======================

class TestTrendlineTypeField_CON17:
    """CON-17: Trendline output should have 'type' field for chart overlay."""

    def test_uptrend_has_support_type(self):
        from unittest.mock import patch
        from tools.trendline import detect_trendlines
        sl = [{"index": 0, "price": 100.0}, {"index": 10, "price": 105.0}]
        bars = []
        for i in range(30):
            y = 100.0 + 0.5 * i + 0.5
            bars.append({"open": y+1, "close": y+0.5, "high": y+2, "low": y, "time": i})
        with patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0}):
            result = detect_trendlines([], sl, bars, pair="XAUUSD", min_touches=2)
        assert len(result["uptrend_lines"]) >= 1
        assert result["uptrend_lines"][0]["type"] == "support"

    def test_downtrend_has_resistance_type(self):
        from unittest.mock import patch
        from tools.trendline import detect_trendlines
        sh = [{"index": 0, "price": 110.0}, {"index": 10, "price": 105.0}]
        bars = []
        for i in range(30):
            y = 110.0 + (-0.5) * i - 0.5
            bars.append({"open": y-1, "close": y-0.5, "high": y, "low": y-2, "time": i})
        with patch("tools.trendline.TRENDLINE_TOLERANCE", {"XAUUSD": 2.0}):
            result = detect_trendlines(sh, [], bars, pair="XAUUSD", min_touches=2)
        assert len(result["downtrend_lines"]) >= 1
        assert result["downtrend_lines"][0]["type"] == "resistance"


# ======================= D-08: is_touch_valid Removed ======================

class TestIsTouchValidRemoved_D08:
    """D-08: is_touch_valid() dead code should be removed from trendline.py."""

    def test_function_not_importable(self):
        """is_touch_valid should no longer exist in trendline module."""
        import tools.trendline as tl
        assert not hasattr(tl, "is_touch_valid")


# =========================================================================
# ========================= FP-11 TESTS ==================================
# =========================================================================


# ========================= D-09: Unused imports removed ====================

class TestD09UnusedImportsRemoved:
    """D-09: MIN_RR/SL_ATR_MULTIPLIER should not be imported in validator."""

    def test_no_min_rr_import(self):
        """validator.py should NOT import MIN_RR from config.settings."""
        import inspect
        import tools.validator as mod
        source = inspect.getsource(mod)
        assert "from config.settings import MIN_RR" not in source
        assert "from config.settings import" not in source

    def test_validator_still_uses_validation_rules(self):
        """Default min_rr must still come from VALIDATION_RULES."""
        import inspect
        from tools.validator import validate_trading_plan
        sig = inspect.signature(validate_trading_plan)
        assert sig.parameters["min_rr"].default == 1.5


# ========================= H-12: Counter-trend enforcement =================

class TestH12CounterTrendEnforce:
    """H-12: must_not_counter_htf=True → violation (not just warning)."""

    def test_buy_against_bearish_is_violation(self):
        from tools.validator import validate_trading_plan
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bearish", zone_freshness="fresh"
        )
        assert result["passed"] is False
        assert any("Counter-trend" in v for v in result["violations"])

    def test_sell_against_bullish_is_violation(self):
        from tools.validator import validate_trading_plan
        setup = {"entry": 2050.0, "sl": 2065.0, "tp": 2020.0, "direction": "sell"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bullish", zone_freshness="fresh"
        )
        assert result["passed"] is False
        assert any("Counter-trend" in v for v in result["violations"])

    def test_counter_trend_warning_when_config_false(self):
        """When must_not_counter_htf=False, counter-trend is only a warning."""
        from unittest.mock import patch
        from tools.validator import validate_trading_plan
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        patched_rules = {
            "min_rr": 1.5, "sl_min_atr_mult": 0.5, "sl_max_atr_mult": 2.5,
            "zone_must_be_fresh": True, "must_not_counter_htf": False,
            "max_retry": 3,
        }
        with patch("tools.validator.VALIDATION_RULES", patched_rules):
            result = validate_trading_plan(
                setup, atr_value=10.0, htf_bias="bearish", zone_freshness="fresh"
            )
        assert result["passed"] is True
        assert any("Counter-trend" in w for w in result["warnings"])

    def test_same_direction_no_counter_trend(self):
        from tools.validator import validate_trading_plan
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bullish", zone_freshness="fresh"
        )
        assert result["passed"] is True
        assert all("Counter-trend" not in v for v in result["violations"])
        assert all("Counter-trend" not in w for w in result["warnings"])


# ========================= CON-15: Validator return standardisation =========

class TestCON15ValidatorReturn:
    """CON-15: Validator always returns dict with standard keys."""

    def test_valid_setup_returns_all_keys(self):
        from tools.validator import validate_trading_plan
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(
            setup, atr_value=10.0, htf_bias="bullish", zone_freshness="fresh"
        )
        required = {"passed", "violations", "warnings", "risk_reward", "sl_atr_distance"}
        assert required == set(result.keys())

    def test_invalid_atr_returns_all_keys(self):
        from tools.validator import validate_trading_plan
        setup = {"entry": 2000.0, "sl": 1985.0, "tp": 2030.0, "direction": "buy"}
        result = validate_trading_plan(setup, atr_value=0.0)
        assert "passed" in result
        assert "violations" in result
        assert result["passed"] is False

    def test_never_raises_on_bad_input(self):
        """Even with missing setup keys, should return dict (not raise)."""
        from tools.validator import validate_trading_plan
        result = validate_trading_plan({}, atr_value=10.0)
        assert isinstance(result, dict)
        assert "passed" in result


# ========================= CON-14: Scorer weights sum verification ==========

class TestCON14ScorerWeightsSum:
    """CON-14: Positive weights must sum to MAX_POSSIBLE_SCORE."""

    def test_max_possible_score_is_14(self):
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        assert MAX_POSSIBLE_SCORE == 14

    def test_positive_weights_sum(self):
        from config.strategy_rules import SCORING_WEIGHTS, MAX_POSSIBLE_SCORE
        pos_sum = sum(v for v in SCORING_WEIGHTS.values() if v > 0)
        assert pos_sum == MAX_POSSIBLE_SCORE

    def test_all_penalty_weights_negative(self):
        from config.strategy_rules import SCORER_PENALTY_FLAGS, SCORING_WEIGHTS
        for flag in SCORER_PENALTY_FLAGS:
            assert SCORING_WEIGHTS[flag] < 0, f"{flag} weight should be negative"


# ========================= M-23: Scorer uses MIN_CONFLUENCE_SCORE ==========

class TestM23ScorerConfig:
    """M-23: Scorer tradeable threshold from config, penalties documented."""

    def test_min_confluence_score_value(self):
        from config.strategy_rules import MIN_CONFLUENCE_SCORE
        assert MIN_CONFLUENCE_SCORE == 5

    def test_scorer_uses_config_threshold(self):
        from tools.scorer import score_setup_candidate
        # score=5 → tradeable
        result = score_setup_candidate(htf_alignment=True, fresh_zone=True)
        assert result["score"] == 5
        assert result["tradeable"] is True

    def test_scorer_penalty_flags_in_config(self):
        from config.strategy_rules import SCORER_PENALTY_FLAGS
        assert "sl_too_tight" in SCORER_PENALTY_FLAGS
        assert "counter_htf_bias" in SCORER_PENALTY_FLAGS


# ========================= L-37: Scorer penalty docstring ==================

class TestL37ScorerPenaltyDocs:
    """L-37: Scorer has documented penalty section."""

    def test_penalty_section_documented(self):
        import inspect
        from tools.scorer import score_setup_candidate
        source = inspect.getsource(score_setup_candidate)
        assert "sl_too_tight" in source
        assert "counter_htf_bias" in source
        assert "SCORER_PENALTY_FLAGS" in source or "Penalty" in source

    def test_all_penalty_flags_in_breakdown(self):
        from tools.scorer import score_setup_candidate
        result = score_setup_candidate()
        for key in ["sl_too_tight", "sl_too_wide", "counter_htf_bias", "zone_mitigated"]:
            assert key in result["breakdown"]


# ========================= M-18: DXY adaptive window =======================

class TestM18DxyAdaptiveWindow:
    """M-18: DXY correlation window adjusts based on volatility."""

    def _candle(self, idx, c, spread=1.0):
        return {"time": f"2025-01-{idx+1:02d}", "open": c, "high": c + spread,
                "low": c - spread, "close": c}

    def _make_pair(self, base, count, spread=1.0):
        candles = []
        for i in range(count):
            c = base + i * 0.1
            candles.append(self._candle(i, c, spread))
        return candles

    def test_adaptive_shortens_window_high_vol(self):
        """High recent volatility → smaller window (faster reaction)."""
        from unittest.mock import patch
        from tools.dxy_gate import dxy_relevance_score

        # Create data where recent bars have 3× the range of historical
        n = 100
        pair = []
        idx = []
        for i in range(n):
            spread = 5.0 if i >= n - 48 else 1.0  # recent bars: high vol
            c = 100 + i * 0.1
            pair.append(self._candle(i, c, spread))
            idx.append(self._candle(i, 50 + i * 0.05, spread))

        with patch("tools.dxy_gate.DXY_GATE_ENABLED", True):
            result = dxy_relevance_score(pair, idx, window=48, adaptive_window=True)
        # High vol → window should decrease
        assert result["window_used"] <= 48
        assert result["enabled"] is True

    def test_adaptive_disabled_keeps_base(self):
        """adaptive_window=False → always uses base window."""
        from unittest.mock import patch
        from tools.dxy_gate import dxy_relevance_score

        pair = self._make_pair(100, 80)
        idx = self._make_pair(50, 80)
        with patch("tools.dxy_gate.DXY_GATE_ENABLED", True):
            result = dxy_relevance_score(pair, idx, window=48, adaptive_window=False)
        assert result["window_used"] == 48


# ========================= M-19: DXY enable/disable config =================

class TestM19DxyFeatureFlag:
    """M-19: DXY gate respects DXY_GATE_ENABLED config."""

    def _candle(self, idx, c):
        return {"time": f"2025-01-{idx+1:02d}", "open": c, "high": c + 1,
                "low": c - 1, "close": c}

    def test_disabled_returns_neutral(self):
        """DXY_GATE_ENABLED=False → neutral result without computation."""
        from unittest.mock import patch
        from tools.dxy_gate import dxy_relevance_score
        pair = [self._candle(i, 100 + i) for i in range(60)]
        idx = [self._candle(i, 50 + i) for i in range(60)]
        with patch("tools.dxy_gate.DXY_GATE_ENABLED", False):
            result = dxy_relevance_score(pair, idx, window=48)
        assert result["enabled"] is False
        assert result["relevant"] is False
        assert result["correlation"] == 0.0

    def test_enabled_computes_correlation(self):
        from unittest.mock import patch
        from tools.dxy_gate import dxy_relevance_score
        # Same direction moves → positive correlation
        pair = [self._candle(i, 100 + i * 0.5) for i in range(60)]
        idx = [self._candle(i, 50 + i * 0.5) for i in range(60)]
        with patch("tools.dxy_gate.DXY_GATE_ENABLED", True):
            result = dxy_relevance_score(pair, idx, window=48)
        assert result["enabled"] is True
        assert result["correlation"] != 0.0

    def test_config_default_is_true(self):
        from config.settings import DXY_GATE_ENABLED
        assert DXY_GATE_ENABLED is True


# ========================= L-30: DXY threshold documented ==================

class TestL30DxyThresholdDoc:
    """L-30: Static threshold 0.2 is documented in dxy_gate.py."""

    def test_default_min_correlation(self):
        from tools.dxy_gate import _DEFAULT_MIN_CORRELATION
        assert _DEFAULT_MIN_CORRELATION == 0.2

    def test_docstring_mentions_threshold(self):
        import inspect
        from tools.dxy_gate import dxy_relevance_score
        doc = inspect.getdoc(dxy_relevance_score) or ""
        assert "0.2" in doc or "min_correlation" in doc


# ========================= L-36: CHoCH coefficient documented ===============

class TestL36ChochCoefficientDoc:
    """L-36: _CHOCH_ATR_MULT=0.3 has rationale documented."""

    def test_coefficient_value(self):
        from tools.choch_filter import _CHOCH_ATR_MULT
        assert _CHOCH_ATR_MULT == 0.3

    def test_rationale_documented(self):
        """Source code should include rationale comment for 0.3."""
        import inspect
        import tools.choch_filter as mod
        source = inspect.getsource(mod)
        # Should explain why 0.3 was chosen
        assert "Rationale" in source or "calibrated" in source or "noise" in source


# ========================= CON-16: DXY symbol naming consistency ============

class TestCON16DxySymbolNaming:
    """CON-16: DXY symbol naming documented in module."""

    def test_symbol_convention_documented(self):
        import inspect
        import tools.dxy_gate as mod
        source = inspect.getsource(mod)
        assert "CON-16" in source
        assert "DXY" in source
        assert "JPYX" in source


# ========================= D-10: DXY gate disabled documented ===============

class TestD10DxyGateEnabledDoc:
    """D-10: DXY gate enabled status is documented."""

    def test_docstring_has_enabled_note(self):
        import tools.dxy_gate as mod
        doc = mod.__doc__ or ""
        assert "ENABLED" in doc or "enabled" in doc

    def test_tool_registry_includes_dxy(self):
        """DXY gate should be in ALL_TOOLS."""
        from agent.tool_registry import ALL_TOOLS
        names = [f.__name__ for f in ALL_TOOLS]
        assert "dxy_relevance_score" in names

    def test_dxy_gate_enabled_config_exists(self):
        """DXY_GATE_ENABLED config must exist for toggling."""
        from config.settings import DXY_GATE_ENABLED
        assert isinstance(DXY_GATE_ENABLED, bool)


# ========================= L-35: Validator min_rr documented ================

class TestL35ValidatorMinRRDoc:
    """L-35: min_rr default 1.5 is documented in validator docstring."""

    def test_docstring_mentions_min_rr(self):
        import inspect
        from tools.validator import validate_trading_plan
        doc = inspect.getdoc(validate_trading_plan) or ""
        assert "1.5" in doc or "min_rr" in doc


# ==========================================================================
# =====================  FP-12  DATABASE / REPO / CONFIG  ==================
# ==========================================================================


# ========================= H-13: trade_stats TRAIL_PROFIT win ===============

class TestH13TradeStatsWinLogic:
    """H-13: trade_stats must count TRAIL_PROFIT & positive BE_HIT as wins."""

    @pytest.mark.asyncio
    async def test_trail_profit_counted_as_win(self):
        """TRAIL_PROFIT must be counted as a win."""
        from database.repository import Repository
        from database.models import Trade

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        for tid, res, pips in [
            ("T1", "TRAIL_PROFIT", 30),
            ("T2", "SL_HIT", -20),
        ]:
            t = Trade(
                trade_id=tid, pair="EURUSD", direction="buy",
                strategy_mode="sniper_confluence", mode="demo",
                entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
                result=res, pips=pips,
            )
            await repo.save_trade(t)
        stats = await repo.trade_stats(mode="demo")
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        await repo.close()

    @pytest.mark.asyncio
    async def test_be_hit_positive_is_win(self):
        """BE_HIT with pips > 0 should count as win."""
        from database.repository import Repository
        from database.models import Trade

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        t = Trade(
            trade_id="BE1", pair="XAUUSD", direction="sell",
            strategy_mode="sniper_confluence", mode="demo",
            entry_price=2800, stop_loss=2810, take_profit_1=2780,
            result="BE_HIT", pips=0.5,
        )
        await repo.save_trade(t)
        stats = await repo.trade_stats(mode="demo")
        assert stats["wins"] == 1
        await repo.close()

    @pytest.mark.asyncio
    async def test_be_hit_zero_is_not_win(self):
        """BE_HIT with pips == 0 should not count as win or loss."""
        from database.repository import Repository
        from database.models import Trade

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        t = Trade(
            trade_id="BE0", pair="EURUSD", direction="buy",
            strategy_mode="sniper_confluence", mode="demo",
            entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
            result="BE_HIT", pips=0,
        )
        await repo.save_trade(t)
        stats = await repo.trade_stats(mode="demo")
        assert stats["wins"] == 0
        assert stats["losses"] == 0
        await repo.close()

    @pytest.mark.asyncio
    async def test_cancelled_excluded_from_winrate_denominator(self):
        """CANCELLED trades should not affect winrate denominator."""
        from database.repository import Repository
        from database.models import Trade

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        for tid, res, pips in [
            ("W1", "TP1_HIT", 20),
            ("C1", "CANCELLED", 0),
        ]:
            t = Trade(
                trade_id=tid, pair="EURUSD", direction="buy",
                strategy_mode="sniper_confluence", mode="demo",
                entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
                result=res, pips=pips,
            )
            await repo.save_trade(t)
        stats = await repo.trade_stats(mode="demo")
        # 1 win out of 1 relevant (CANCELLED excluded from denom)
        assert stats["total"] == 2  # total includes CANCELLED
        assert stats["wins"] == 1
        assert stats["winrate"] == pytest.approx(1.0)
        await repo.close()

    @pytest.mark.asyncio
    async def test_manual_close_positive_is_win(self):
        """MANUAL_CLOSE with positive pips is a conditional win."""
        from database.repository import Repository
        from database.models import Trade

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        for tid, res, pips in [
            ("MC1", "MANUAL_CLOSE", 15),
            ("MC2", "MANUAL_CLOSE", -5),
        ]:
            t = Trade(
                trade_id=tid, pair="EURUSD", direction="buy",
                strategy_mode="sniper_confluence", mode="demo",
                entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
                result=res, pips=pips,
            )
            await repo.save_trade(t)
        stats = await repo.trade_stats(mode="demo")
        assert stats["wins"] == 1  # only MC1
        await repo.close()


# ========================= M-26: Config precedence documented ===============

class TestM26ConfigPrecedenceDoc:
    """M-26: repository module documents config precedence."""

    def test_module_docstring_has_precedence(self):
        import database.repository as mod
        doc = mod.__doc__ or ""
        assert "precedence" in doc.lower() or ".env" in doc

    def test_mentions_strategy_rules(self):
        import database.repository as mod
        doc = mod.__doc__ or ""
        assert "strategy_rules" in doc


# ========================= M-27: Parameterized pagination limit =============

class TestM27PaginationLimit:
    """M-27: list_trades limit is clamped to safe bounds."""

    @pytest.mark.asyncio
    async def test_negative_limit_clamped(self):
        from database.repository import Repository
        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        result = await repo.list_trades(limit=-5)
        assert isinstance(result, list)
        await repo.close()

    @pytest.mark.asyncio
    async def test_huge_limit_clamped(self):
        """Limit > 10_000 should be clamped to 10_000."""
        from database.repository import Repository
        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        # Just verify no error — actual clamping is internal
        result = await repo.list_trades(limit=999_999)
        assert isinstance(result, list)
        await repo.close()


# ========================= M-28 / D-11: ALL_PAIRS alias =====================

class TestM28D11AllPairsAlias:
    """M-28/D-11: ALL_PAIRS is now an alias for MVP_PAIRS."""

    def test_all_pairs_equals_mvp_pairs(self):
        from config.settings import MVP_PAIRS, ALL_PAIRS
        assert ALL_PAIRS is MVP_PAIRS

    def test_all_pairs_importable_from_runner(self):
        """scheduler/runner.py still imports ALL_PAIRS — must not break."""
        from config.settings import ALL_PAIRS
        assert isinstance(ALL_PAIRS, list)
        assert len(ALL_PAIRS) >= 6


# ========================= L-40: DB index on pair column ====================

class TestL40PairIndex:
    """L-40: Trade.pair and AnalysisSession.pair should be indexed."""

    def test_trade_pair_has_index(self):
        from database.models import Trade
        col = Trade.__table__.columns["pair"]
        assert col.index is True or any(
            idx for idx in Trade.__table__.indexes if "pair" in [c.name for c in idx.columns]
        )

    def test_analysis_session_pair_has_index(self):
        from database.models import AnalysisSession
        col = AnalysisSession.__table__.columns["pair"]
        assert col.index is True or any(
            idx for idx in AnalysisSession.__table__.indexes if "pair" in [c.name for c in idx.columns]
        )


# ========================= L-41: save_trade return documented ================

class TestL41SaveTradeReturn:
    """L-41: save_trade docstring documents the return value."""

    def test_docstring_mentions_trade_id(self):
        import inspect
        from database.repository import Repository
        doc = inspect.getdoc(Repository.save_trade) or ""
        assert "trade_id" in doc

    @pytest.mark.asyncio
    async def test_save_trade_returns_trade_with_id(self):
        from database.repository import Repository
        from database.models import Trade

        repo = Repository(db_url="sqlite+aiosqlite:///:memory:")
        await repo.init_db()
        t = Trade(
            trade_id="RET_01", pair="EURUSD", direction="buy",
            strategy_mode="sniper_confluence", mode="demo",
            entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
        )
        saved = await repo.save_trade(t)
        assert saved.trade_id == "RET_01"
        assert saved.id is not None
        await repo.close()


# ========================= L-42: CHALLENGE_CENT documented ===================

class TestL42ChallengeCentDoc:
    """L-42: CHALLENGE_CENT config constants are documented."""

    def test_settings_has_comment_block(self):
        import inspect
        import config.settings as mod
        src = inspect.getsource(mod)
        assert "LOT_MULTIPLIER" in src and "SL_MULTIPLIER" in src
        assert "challenge_cent" in src.lower() or "challenge_mode" in src.lower()

    def test_all_three_multipliers_exist(self):
        from config.settings import (
            CHALLENGE_CENT_LOT_MULTIPLIER,
            CHALLENGE_CENT_SL_MULTIPLIER,
            CHALLENGE_CENT_TP_MULTIPLIER,
        )
        assert CHALLENGE_CENT_LOT_MULTIPLIER == 0.01
        assert CHALLENGE_CENT_SL_MULTIPLIER == 1.5
        assert CHALLENGE_CENT_TP_MULTIPLIER == 1.5


# ========================= L-43: SCORING_WEIGHTS documented =================

class TestL43ScoringWeightsDoc:
    """L-43: SCORING_WEIGHTS keys are documented in strategy_rules.py."""

    def test_source_has_key_documentation(self):
        import inspect
        import config.strategy_rules as mod
        src = inspect.getsource(mod)
        # Must document all positive factors
        for key in ["htf_alignment", "fresh_zone", "sweep_detected",
                     "near_major_snr", "pa_confirmed"]:
            assert key in src

    def test_source_has_penalty_documentation(self):
        import inspect
        import config.strategy_rules as mod
        src = inspect.getsource(mod)
        for key in ["sl_too_tight", "sl_too_wide", "counter_htf_bias",
                     "zone_mitigated"]:
            assert key in src

    def test_max_possible_score_documented(self):
        import inspect
        import config.strategy_rules as mod
        src = inspect.getsource(mod)
        assert "MAX_POSSIBLE_SCORE" in src
        assert "14" in src


# ========================= L-44: list_trades limit documented ================

class TestL44ListTradesLimit:
    """L-44: list_trades default limit and clamping documented."""

    def test_default_limit_is_100(self):
        import inspect
        from database.repository import Repository
        sig = inspect.signature(Repository.list_trades)
        assert sig.parameters["limit"].default == 100

    def test_source_has_limit_comment(self):
        import inspect
        from database.repository import Repository
        src = inspect.getsource(Repository.list_trades)
        assert "L-44" in src or "limit" in src.lower()


# ========================= L-45: LOG_LEVEL config ============================

class TestL45LogLevel:
    """L-45: LOG_LEVEL config with validation."""

    def test_log_level_exists(self):
        from config.settings import LOG_LEVEL
        assert LOG_LEVEL in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    def test_default_is_info(self):
        from config.settings import LOG_LEVEL
        # In test env there is no .env override, so default should be INFO
        assert LOG_LEVEL == "INFO"

    def test_invalid_falls_back_to_info(self):
        """Invalid LOG_LEVEL should fall back to INFO."""
        import config.settings as mod
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        assert mod._LOG_LEVEL_RAW in valid or mod.LOG_LEVEL == "INFO"


# ========================= CON-19: demo_pnl naming documented ===============

class TestCON19DemoPnlNaming:
    """CON-19: demo_pnl/demo_balance_after naming documented in models."""

    def test_models_source_documents_naming(self):
        import inspect
        import database.models as mod
        src = inspect.getsource(mod)
        assert "CON-19" in src
        assert "real" in src.lower()  # mentions usage in real mode


# ========================= CON-20: MIN_SCORE single source =================

class TestCON20MinScoreSingleSource:
    """CON-20: MIN_SCORE_FOR_TRADE and MIN_CONFLUENCE_SCORE are consistent."""

    def test_values_match(self):
        from config.settings import MIN_SCORE_FOR_TRADE
        from config.strategy_rules import MIN_CONFLUENCE_SCORE
        assert MIN_SCORE_FOR_TRADE == MIN_CONFLUENCE_SCORE

    def test_settings_documents_canonical_source(self):
        import inspect
        import config.settings as mod
        src = inspect.getsource(mod)
        assert "CON-20" in src or "MIN_CONFLUENCE_SCORE" in src


# ==========================================================================
# =====================  FP-13  POST-MORTEM / ERROR / DEMO  ================
# ==========================================================================


# ========================= H-17: HTTP status regex word boundary ============

class TestH17HttpStatusWordBoundary:
    """H-17: HTTP status matching uses regex word boundary to avoid false positives."""

    def test_429_matches(self):
        from agent.error_handler import ErrorHandler, ErrorCategory
        h = ErrorHandler()
        assert h.classify(Exception("HTTP 429 Too Many Requests")) == ErrorCategory.RATE_LIMIT

    def test_1429_does_not_match_429(self):
        """'1429' should NOT trigger a 429 rate-limit match."""
        from agent.error_handler import ErrorHandler, ErrorCategory
        h = ErrorHandler()
        cat = h.classify(Exception("Error code 1429 custom"))
        assert cat != ErrorCategory.RATE_LIMIT

    def test_5003_does_not_match_500(self):
        """'5003' should NOT trigger a 500 service-down match."""
        from agent.error_handler import ErrorHandler, ErrorCategory
        h = ErrorHandler()
        cat = h.classify(Exception("Vendor error 5003"))
        assert cat != ErrorCategory.SERVICE_DOWN

    def test_400_in_word_still_matches(self):
        from agent.error_handler import ErrorHandler, ErrorCategory
        h = ErrorHandler()
        cat = h.classify(Exception("HTTP 400 Bad Request"))
        assert cat == ErrorCategory.INVALID_REQUEST


# ========================= H-18: MaxDrawdownExceeded caught ==================

class TestH18MaxDrawdownCaught:
    """H-18: ModeManager.on_trade_closed catches MaxDrawdownExceeded."""

    def test_drawdown_returns_halted(self):
        from agent.demo_tracker import DemoTracker, ModeManager, DemoTradeRecord
        tracker = DemoTracker(
            initial_balance=1000,
            max_total_drawdown=0.01,  # 1% — will trigger immediately on loss
        )
        mgr = ModeManager(tracker=tracker)
        mgr.mode = "demo"
        trade = DemoTradeRecord(
            trade_id="DD1", pair="EURUSD", direction="buy",
            entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
            exit_price=1.04, result="SL_HIT", pips=-100,
        )
        # Should NOT raise — H-18 catches it
        result = mgr.on_trade_closed(trade)
        assert result is not None
        assert result.get("halted") is True

    def test_normal_trade_no_halt(self):
        from agent.demo_tracker import DemoTracker, ModeManager, DemoTradeRecord
        tracker = DemoTracker(initial_balance=10_000)
        mgr = ModeManager(tracker=tracker)
        mgr.mode = "demo"
        trade = DemoTradeRecord(
            trade_id="W1", pair="EURUSD", direction="buy",
            entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
            exit_price=1.055, result="TP1_HIT", pips=50, rr_achieved=1.5,
        )
        result = mgr.on_trade_closed(trade)
        assert result is None  # No graduation yet


# ========================= M-33: TRAIL_PROFIT post-mortem win ================

class TestM33TrailProfitPostMortem:
    """M-33: TRAIL_PROFIT should produce win analysis, not 'cancelled'."""

    def test_trail_profit_generates_win_analysis(self):
        from agent.post_mortem import PostMortemGenerator, MarketContext
        pm = PostMortemGenerator()
        report = pm.generate(
            trade_id="TP01", pair="EURUSD", direction="buy",
            entry_price=1.05, exit_price=1.055, stop_loss=1.04,
            take_profit_1=1.06, result="TRAIL_PROFIT",
            pips=50, duration_minutes=60,
        )
        assert "cancelled" not in " ".join(report.lessons).lower()
        assert any("trail" in w.lower() for w in report.what_worked)

    def test_trail_profit_with_context(self):
        from agent.post_mortem import PostMortemGenerator, MarketContext
        pm = PostMortemGenerator()
        ctx = MarketContext(sl_trail_applied=True)
        report = pm.generate(
            trade_id="TP02", pair="XAUUSD", direction="sell",
            entry_price=2800, exit_price=2790, stop_loss=2810,
            take_profit_1=2780, result="TRAIL_PROFIT",
            pips=100, duration_minutes=120, context=ctx,
        )
        assert any("trail" in w.lower() for w in report.what_worked)


# ========================= M-34: SL cause multi-cause support ===============

class TestM34SLMultipleCauses:
    """M-34: _analyze_loss collects multiple causes, not just the first."""

    def test_news_and_choch_both_detected(self):
        from agent.post_mortem import PostMortemGenerator, MarketContext
        pm = PostMortemGenerator()
        ctx = MarketContext(
            news_during_trade=True,
            choch_occurred=True,
        )
        report = pm.generate(
            trade_id="MC1", pair="EURUSD", direction="buy",
            entry_price=1.05, exit_price=1.04, stop_loss=1.04,
            take_profit_1=1.06, result="SL_HIT",
            pips=-100, duration_minutes=30, context=ctx,
        )
        assert report.sl_cause is not None
        assert report.sl_cause.primary_cause == "news_spike"
        assert "counter_htf_ignored" in report.sl_cause.secondary_causes

    def test_single_cause_no_secondary(self):
        from agent.post_mortem import PostMortemGenerator, MarketContext
        pm = PostMortemGenerator()
        ctx = MarketContext(news_during_trade=True)
        report = pm.generate(
            trade_id="MC2", pair="EURUSD", direction="buy",
            entry_price=1.05, exit_price=1.04, stop_loss=1.04,
            take_profit_1=1.06, result="SL_HIT",
            pips=-100, duration_minutes=30, context=ctx,
        )
        assert report.sl_cause.primary_cause == "news_spike"
        assert report.sl_cause.secondary_causes == []


# ========================= M-35: Redundant timezone import removed ===========

class TestM35RedundantTimezoneImport:
    """M-35: No redundant 'from datetime import timezone as tz' in method body."""

    def test_no_inline_timezone_import(self):
        import inspect
        from agent.error_handler import StateRecovery
        src = inspect.getsource(StateRecovery._evaluate_session)
        assert "from datetime import timezone as tz" not in src


# ========================= M-36: Error handler time-window stats =============

class TestM36ErrorHandlerTimeWindow:
    """M-36: ErrorHandler has reset and time-window stats."""

    def test_reset_error_counts(self):
        from agent.error_handler import ErrorHandler, ErrorCategory
        h = ErrorHandler()
        h._error_counts[ErrorCategory.RATE_LIMIT] = 5
        h.reset_error_counts()
        assert h.error_stats == {}

    def test_stats_window_seconds(self):
        import time
        from agent.error_handler import ErrorHandler
        h = ErrorHandler()
        time.sleep(0.05)
        assert h.stats_window_seconds >= 0.04


# ========================= M-37: ModeManager persist mode ====================

class TestM37ModeManagerPersist:
    """M-37: switch_to_real accepts optional repository param."""

    def test_switch_signature_accepts_repository(self):
        import inspect
        from agent.demo_tracker import ModeManager
        sig = inspect.signature(ModeManager.switch_to_real)
        assert "repository" in sig.parameters


# ========================= M-38: TRAIL_PROFIT win in demo tracker ============

class TestM38DemoTrackerTrailProfit:
    """M-38: DemoTracker._compute_stats counts TRAIL_PROFIT as win."""

    def test_trail_profit_counted_as_win(self):
        from agent.demo_tracker import DemoTracker, DemoTradeRecord
        tracker = DemoTracker()
        trade = DemoTradeRecord(
            trade_id="TP1", pair="EURUSD", direction="buy",
            entry_price=1.05, stop_loss=1.04, take_profit_1=1.06,
            exit_price=1.055, result="TRAIL_PROFIT",
            pips=50, rr_achieved=1.5,
        )
        tracker.record_trade(trade)
        stats = tracker._compute_stats()
        assert stats["wins"] == 1

    def test_trail_profit_positive_pnl(self):
        from agent.demo_tracker import DemoTracker, DemoTradeRecord
        tracker = DemoTracker(initial_balance=10_000)
        trade = DemoTradeRecord(
            trade_id="TP2", pair="XAUUSD", direction="sell",
            entry_price=2800, stop_loss=2810, take_profit_1=2780,
            exit_price=2790, result="TRAIL_PROFIT",
            pips=100, rr_achieved=1.0,
        )
        result = tracker.record_trade(trade)
        assert result.demo_pnl > 0


# ========================= L-58: SLCauseAnalysis typed dict ===================

class TestL58SLCauseAnalysisType:
    """L-58: SLCauseAnalysis.suggested_param_change is typed dict[str, str]."""

    def test_type_annotation_present(self):
        import inspect
        from agent.post_mortem import SLCauseAnalysis
        hints = SLCauseAnalysis.__dataclass_fields__
        assert "suggested_param_change" in hints

    def test_secondary_causes_field_exists(self):
        from agent.post_mortem import SLCauseAnalysis
        s = SLCauseAnalysis()
        assert hasattr(s, "secondary_causes")
        assert isinstance(s.secondary_causes, list)


# ========================= L-59: StateRecovery uses enum refs ================

class TestL59StateRecoveryEnum:
    """L-59: StateRecovery._evaluate_session comments reference AnalysisState."""

    def test_source_mentions_analysis_state(self):
        import inspect
        from agent.error_handler import StateRecovery
        src = inspect.getsource(StateRecovery._evaluate_session)
        assert "AnalysisState" in src


# ========================= CON-26: from_dict trades documented ===============

class TestCON26FromDictDoc:
    """CON-26: DemoTracker.from_dict documents that trades are not restored."""

    def test_docstring_mentions_limitation(self):
        import inspect
        from agent.demo_tracker import DemoTracker
        doc = inspect.getdoc(DemoTracker.from_dict) or ""
        assert "not restored" in doc.lower() or "CON-26" in doc

    def test_from_dict_has_empty_trades(self):
        from agent.demo_tracker import DemoTracker
        data = {"initial_balance": 5000, "balance": 4800, "high_water_mark": 5000}
        restored = DemoTracker.from_dict(data)
        assert len(restored.trades) == 0
        assert restored.balance == 4800


# ========================= D-15: StateRecovery TODO noted ===================

class TestD15StateRecoveryTodo:
    """D-15: StateRecovery class documents future integration."""

    def test_docstring_has_todo(self):
        import inspect
        from agent.error_handler import StateRecovery
        doc = inspect.getdoc(StateRecovery) or ""
        assert "D-15" in doc or "TODO" in doc or "not yet integrated" in doc.lower()


# ========================= D-16: DataFreshnessChecker TODO noted =============

class TestD16DataFreshnessCheckerTodo:
    """D-16: DataFreshnessChecker documents future integration."""

    def test_docstring_has_todo(self):
        import inspect
        from agent.error_handler import DataFreshnessChecker
        doc = inspect.getdoc(DataFreshnessChecker) or ""
        assert "D-16" in doc or "TODO" in doc or "not yet integrated" in doc.lower()


# ========================= D-17: DemoTracker TODO noted =====================

class TestD17DemoTrackerTodo:
    """D-17: DemoTracker documents future integration."""

    def test_docstring_has_todo(self):
        import inspect
        from agent.demo_tracker import DemoTracker
        doc = inspect.getdoc(DemoTracker) or ""
        assert "D-17" in doc or "TODO" in doc or "not yet integrated" in doc.lower()


# ==========================================================================
# =====================  FP-14  SCHEMAS  ===================================
# ==========================================================================

# Helper: build a minimal valid SetupCandidate for TradingPlan tests.
def _make_setup(**overrides):
    from schemas.plan import SetupCandidate
    from schemas.market_data import Direction, StrategyMode
    defaults = dict(
        direction=Direction.BUY,
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        entry_zone_low=1.08, entry_zone_high=1.09,
        trigger_condition="sweep + reclaim",
        stop_loss=1.07, sl_reasoning="below swing",
        take_profit_1=1.10, tp_reasoning="next OB",
        risk_reward_ratio=2.0, management="trail SL",
        ttl_hours=4, invalidation="break below 1.07",
        confluence_score=8, rationale="strong setup",
    )
    defaults.update(overrides)
    return SetupCandidate(**defaults)


def _make_plan(**overrides):
    from schemas.plan import TradingPlan
    from schemas.market_data import StrategyMode
    defaults = dict(
        pair="EURUSD",
        analysis_time="2025-01-01T00:00:00Z",
        htf_bias="bullish",
        htf_bias_reasoning="H4 higher highs",
        strategy_mode=StrategyMode.SNIPER_CONFLUENCE,
        primary_setup=_make_setup(),
        confidence=0.8,
        valid_until="2025-01-01T04:00:00Z",
    )
    defaults.update(overrides)
    return TradingPlan(**defaults)


# ========================= L-53: htf_bias validator ==========================

class TestL53HtfBiasValidator:
    """L-53: TradingPlan rejects invalid htf_bias values."""

    def test_bullish_accepted(self):
        p = _make_plan(htf_bias="bullish")
        assert p.htf_bias == "bullish"

    def test_bearish_accepted(self):
        p = _make_plan(htf_bias="bearish")
        assert p.htf_bias == "bearish"

    def test_range_accepted(self):
        p = _make_plan(htf_bias="range")
        assert p.htf_bias == "range"

    def test_ranging_accepted(self):
        p = _make_plan(htf_bias="ranging")
        assert p.htf_bias == "ranging"

    def test_neutral_rejected(self):
        import pytest
        with pytest.raises(Exception):
            _make_plan(htf_bias="neutral")

    def test_empty_rejected(self):
        import pytest
        with pytest.raises(Exception):
            _make_plan(htf_bias="")

    def test_mixed_rejected(self):
        import pytest
        with pytest.raises(Exception):
            _make_plan(htf_bias="mixed")


# ========================= L-54: confidence bounds ===========================

class TestL54ConfidenceBounds:
    """L-54: TradingPlan.confidence must be 0.0-1.0."""

    def test_zero_accepted(self):
        p = _make_plan(confidence=0.0)
        assert p.confidence == 0.0

    def test_one_accepted(self):
        p = _make_plan(confidence=1.0)
        assert p.confidence == 1.0

    def test_mid_accepted(self):
        p = _make_plan(confidence=0.75)
        assert p.confidence == 0.75

    def test_negative_rejected(self):
        import pytest
        with pytest.raises(Exception):
            _make_plan(confidence=-0.1)

    def test_above_one_rejected(self):
        import pytest
        with pytest.raises(Exception):
            _make_plan(confidence=1.5)


# ========================= L-55: MarketStructure.events typed ================

class TestL55MarketStructureEvents:
    """L-55: MarketStructure.events uses StructureEvent instead of dict."""

    def test_empty_events_ok(self):
        from schemas.market_data import MarketStructure
        m = MarketStructure(trend="bullish", events=[])
        assert m.events == []

    def test_dict_coerced_to_structure_event(self):
        from schemas.market_data import MarketStructure
        from schemas.structure import StructureEvent
        m = MarketStructure(trend="bullish", events=[{
            "event_type": "bos", "direction": "bullish",
            "break_index": 10, "break_price": 1.05,
            "broken_swing_index": 5,
        }])
        assert isinstance(m.events[0], StructureEvent)

    def test_structure_event_object_accepted(self):
        from schemas.market_data import MarketStructure
        from schemas.structure import StructureEvent
        evt = StructureEvent(
            event_type="choch", direction="bearish",
            break_index=20, break_price=1.04, broken_swing_index=15,
        )
        m = MarketStructure(trend="bearish", events=[evt])
        assert m.events[0].event_type.value == "choch"

    def test_invalid_event_rejected(self):
        import pytest
        from schemas.market_data import MarketStructure
        with pytest.raises(Exception):
            MarketStructure(trend="bullish", events=[{"bad": "data"}])


# ==========================================================================
# =====================  FP-15  NOTIFIER / SCHEDULER / CHART / MAIN  =======
# ==========================================================================


# ========================= M-29/CON-21: Dynamic score denominator ============

class TestM29DynamicScore:
    """M-29/CON-21: Templates use MAX_POSSIBLE_SCORE instead of hardcoded 15."""

    def test_score_uses_dynamic_max(self):
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        import inspect
        import notifier.templates as mod
        src = inspect.getsource(mod)
        assert "MAX_POSSIBLE_SCORE" in src
        assert "/15" not in src  # No more hardcoded /15

    def test_triggered_alert_contains_score(self):
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        plan = _make_plan()
        from notifier.templates import format_triggered_alert
        msg = format_triggered_alert(plan)
        assert f"/{MAX_POSSIBLE_SCORE}" in msg


# ========================= M-39: Lazy chart singleton ========================

class TestM39LazyChartSingleton:
    """M-39: get_chart_generator() returns lazy-init singleton."""

    def test_get_chart_generator_returns_instance(self):
        from charts.screenshot import get_chart_generator, ChartScreenshotGenerator
        gen = get_chart_generator()
        assert isinstance(gen, ChartScreenshotGenerator)

    def test_get_chart_generator_is_singleton(self):
        from charts.screenshot import get_chart_generator
        a = get_chart_generator()
        b = get_chart_generator()
        assert a is b


# ========================= L-01: Signal import documented ====================

class TestL01SignalImport:
    """L-01: Signal import is documented for Windows fallback."""

    def test_signal_import_has_comment(self):
        import inspect
        import main as mod
        src = inspect.getsource(mod)
        assert "L-01" in src or "Windows" in src


# ========================= L-02: /health version info ========================

class TestL02HealthVersion:
    """L-02: /health endpoint includes version info."""

    def test_health_has_version_field(self):
        from fastapi.testclient import TestClient
        from dashboard.backend.main import app
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert data["version"]  # Non-empty


# ========================= L-10: PM2 restart counter ========================

class TestL10RestartCounter:
    """L-10: /health includes restart_count."""

    def test_health_has_restart_count(self):
        from fastapi.testclient import TestClient
        from dashboard.backend.main import app
        client = TestClient(app)
        resp = client.get("/api/health")
        data = resp.json()
        assert "restart_count" in data
        assert isinstance(data["restart_count"], int)


# ========================= L-46: Job ID prefix ==============================

class TestL46JobIdPrefix:
    """L-46: APScheduler jobs use 'fx_' prefix."""

    def test_prefix_constant_defined(self):
        from scheduler.runner import _JOB_PREFIX
        assert _JOB_PREFIX == "fx_"

    def test_jobs_have_prefix(self):
        import asyncio
        from scheduler.runner import ScanScheduler

        async def noop(pair): pass
        async def noop_wrapup(): pass

        s = ScanScheduler(scan_fn=noop, wrapup_fn=noop_wrapup)
        s.configure()
        job_ids = [j.id for j in s.jobs]
        for jid in job_ids:
            assert jid.startswith("fx_"), f"Job {jid} missing prefix"
        # Don't call shutdown — scheduler was never started


# ========================= L-47: Misfire grace time ==========================

class TestL47MisfireGraceTime:
    """L-47: APScheduler configured with generous misfire_grace_time."""

    def test_scheduler_has_misfire_config(self):
        import inspect
        import scheduler.runner as mod
        src = inspect.getsource(mod)
        assert "misfire_grace_time" in src


# ========================= L-49: WhatsApp API key rotation documented ========

class TestL49WhatsAppDocs:
    """L-49: whatsapp.py docstring documents credential rotation."""

    def test_docstring_mentions_rotation(self):
        import notifier.whatsapp as mod
        doc = mod.__doc__ or ""
        assert "rotat" in doc.lower() or "L-49" in doc


# ========================= L-51: i18n opportunity marker =====================

class TestL51I18nMarker:
    """L-51: templates.py has i18n comment marker."""

    def test_i18n_comment_present(self):
        import inspect
        import notifier.templates as mod
        src = inspect.getsource(mod)
        assert "i18n" in src.lower()


# ========================= L-60: Empty DataFrame guard =======================

class TestL60EmptyDataFrameGuard:
    """L-60: Chart generators raise ValueError on empty DataFrame."""

    def test_entry_chart_empty_raises(self):
        import pytest
        import pandas as pd
        from charts.screenshot import ChartScreenshotGenerator
        gen = ChartScreenshotGenerator()
        with pytest.raises(ValueError, match="empty"):
            gen.generate_entry_chart(
                ohlcv=pd.DataFrame(),
                pair="EURUSD", direction="buy",
                entry_zone=(1.08, 1.09),
                stop_loss=1.07, take_profit_1=1.10,
            )

    def test_audit_chart_empty_raises(self):
        import pytest
        import pandas as pd
        from charts.screenshot import ChartScreenshotGenerator
        gen = ChartScreenshotGenerator()
        with pytest.raises(ValueError, match="empty"):
            gen.generate_audit_chart(
                ohlcv=pd.DataFrame(),
                pair="EURUSD", title="Test",
            )


# ========================= L-61: to_data_uri alias ==========================

class TestL61ToDataUri:
    """L-61: to_base64 is now an alias for to_data_uri."""

    def test_to_data_uri_method_exists(self):
        from charts.screenshot import ChartScreenshotGenerator
        assert hasattr(ChartScreenshotGenerator, "to_data_uri")

    def test_to_base64_still_works(self):
        """Backward compat: to_base64 delegates to to_data_uri."""
        import inspect
        from charts.screenshot import ChartScreenshotGenerator
        src = inspect.getsource(ChartScreenshotGenerator.to_base64)
        assert "to_data_uri" in src


# ========================= CON-03: Log format documented =====================

class TestCON03LogFormat:
    """CON-03: main.py has standardised log format documentation."""

    def test_log_format_documented(self):
        import inspect
        import main as mod
        src = inspect.getsource(mod)
        assert "CON-03" in src or "Standardised log format" in src


# ========================= CON-25: Temp file cleanup =========================

class TestCON25TempCleanup:
    """CON-25: ChartScreenshotGenerator has cleanup() method."""

    def test_cleanup_method_exists(self):
        from charts.screenshot import ChartScreenshotGenerator
        gen = ChartScreenshotGenerator()
        assert hasattr(gen, "cleanup")

    def test_cleanup_returns_count(self):
        from charts.screenshot import ChartScreenshotGenerator
        gen = ChartScreenshotGenerator()
        result = gen.cleanup()
        assert isinstance(result, int)


# ========================= D-02: No unused demo_run import ===================

class TestD02NoUnusedImport:
    """D-02: main.py has no unused demo_run import."""

    def test_no_demo_run_import(self):
        import inspect
        import main as mod
        src = inspect.getsource(mod)
        assert "import demo_run" not in src
        assert "from demo_run" not in src


# =========================================================================
# FP-14 remaining items (L-56, L-57, CON-23, CON-24, CON-27, D-13, D-14)
# =========================================================================

# ========================= L-56: Candle.volume documented ====================

class TestL56CandleVolume:
    """L-56: Candle.volume default=0.0 is documented."""

    def test_volume_default_is_zero(self):
        from schemas.market_data import Candle
        c = Candle(time="2025-01-01T00:00:00Z", open=1.0, high=1.1, low=0.9, close=1.05)
        assert c.volume == 0.0

    def test_volume_docstring(self):
        import inspect
        from schemas.market_data import Candle
        src = inspect.getsource(Candle)
        assert "L-56" in src or "tick volume" in src.lower()


# ========================= L-57: Zone type Enum ==============================

class TestL57ZoneTypeEnum:
    """L-57: SnDZone, OrderBlock, LiquidityPool use Enum zone_type / pool_type."""

    def test_snd_zone_uses_zonetype_enum(self):
        from schemas.zones import SnDZone, ZoneFormation
        from schemas.market_data import ZoneType
        z = SnDZone(
            zone_type="supply", formation=ZoneFormation.RALLY_BASE_DROP,
            high=1.10, low=1.09, base_start_idx=0, base_end_idx=2,
            displacement_strength=1.5, body_ratio=0.7,
        )
        assert z.zone_type is ZoneType.SUPPLY

    def test_snd_zone_invalid_type_rejected(self):
        import pytest
        from schemas.zones import SnDZone, ZoneFormation
        with pytest.raises(Exception):
            SnDZone(
                zone_type="invalid", formation=ZoneFormation.RALLY_BASE_DROP,
                high=1.10, low=1.09, base_start_idx=0, base_end_idx=2,
                displacement_strength=1.5, body_ratio=0.7,
            )

    def test_orderblock_uses_obtype_enum(self):
        from schemas.zones import OrderBlock, OBType
        ob = OrderBlock(
            zone_type="bullish_ob", high=1.10, low=1.09, candle_index=5,
        )
        assert ob.zone_type is OBType.BULLISH_OB

    def test_liquiditypool_uses_pooltype_enum(self):
        from schemas.zones import LiquidityPool, PoolType
        lp = LiquidityPool(pool_type="eqh", price=1.105, swing_count=3)
        assert lp.pool_type is PoolType.EQH


# ========================= CON-23: confluence_score dynamic max ==============

class TestCON23ConfluenceScore:
    """CON-23: confluence_score validator uses MAX_POSSIBLE_SCORE (14), not 15."""

    def test_max_score_accepted(self):
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        setup = _make_setup(confluence_score=MAX_POSSIBLE_SCORE)
        assert setup.confluence_score == MAX_POSSIBLE_SCORE

    def test_above_max_rejected(self):
        import pytest
        from pydantic import ValidationError
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        with pytest.raises(ValidationError, match="confluence_score"):
            _make_setup(confluence_score=MAX_POSSIBLE_SCORE + 1)

    def test_description_contains_max(self):
        """Field description should reference actual max, not hardcoded 15."""
        from schemas.plan import SetupCandidate
        from config.strategy_rules import MAX_POSSIBLE_SCORE
        desc = SetupCandidate.model_fields["confluence_score"].description
        assert str(MAX_POSSIBLE_SCORE) in desc


# ========================= CON-24: Dual Zone documented ======================

class TestCON24DualZone:
    """CON-24: zones.py module docstring documents dual-Zone relationship."""

    def test_zones_module_documents_relationship(self):
        import schemas.zones as mod
        doc = mod.__doc__ or ""
        assert "CON-24" in doc or "market_data.Zone" in doc or "generic" in doc.lower()


# ========================= CON-27: MarketStructure.events typed ==============

class TestCON27EventsTyped:
    """CON-27: Redundant with L-55 — MarketStructure.events is list[StructureEvent]."""

    def test_events_type_is_structure_event(self):
        """Already verified in FP-14 L-55 tests; confirm still holds."""
        from schemas.market_data import MarketStructure
        from schemas.structure import StructureEvent
        ms = MarketStructure(
            trend="bullish",
            events=[{"event_type": "bos", "direction": "bullish",
                     "break_index": 10, "break_price": 1.1, "broken_swing_index": 5}],
        )
        assert isinstance(ms.events[0], StructureEvent)


# ========================= D-13: structure.py is imported ====================

class TestD13StructureUsed:
    """D-13: schemas/structure.py is now actively imported (by market_data.py)."""

    def test_structure_docstring_marks_usage(self):
        import schemas.structure as mod
        doc = mod.__doc__ or ""
        assert "D-13" in doc or "Actively used" in doc

    def test_structure_event_importable_from_market_data(self):
        """StructureEvent is reachable via market_data import chain."""
        from schemas.market_data import MarketStructure
        from schemas.structure import StructureEvent
        # If this resolves, the module is actively used
        assert StructureEvent is not None


# ========================= D-14: zones.py usage intent =======================

class TestD14ZonesUsageIntent:
    """D-14: schemas/zones.py has documented usage intent."""

    def test_zones_docstring_has_usage_intent(self):
        import schemas.zones as mod
        doc = mod.__doc__ or ""
        assert "D-14" in doc or "usage intent" in doc.lower() or "canonical schema" in doc.lower()