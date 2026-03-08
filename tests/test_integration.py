"""
tests/test_integration.py — Phase 3 integration tests.

Tests:
  1. ScanScheduler job configuration & lifecycle.
  2. Full pipeline mock: orchestrator → notification handler → dashboard push.
  3. main.py startup/shutdown hooks (FastAPI lifespan).
"""

from __future__ import annotations

import sys
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# ---------------------------------------------------------------------------
# Mock heavy dependencies that may not be installed in test env
# ---------------------------------------------------------------------------
# google.genai is required by agent.gemini_client but may not be installed.
# We insert a stub module so that importing main / agent.orchestrator succeeds.
_google_mock = MagicMock()
sys.modules.setdefault("google", _google_mock)
sys.modules.setdefault("google.genai", _google_mock.genai)

# ──────────────────────────────────────────────────────────────────────
# 1. Scheduler
# ──────────────────────────────────────────────────────────────────────


class TestScanScheduler:
    """Test APScheduler ScanScheduler configuration."""

    def test_configure_creates_jobs_mvp(self):
        """MVP = XAUUSD only → no JPY pairs → 3 jobs (london, preny, dns_refresh) + wrapup."""
        from scheduler.runner import ScanScheduler

        sched = ScanScheduler(
            scan_fn=AsyncMock(),
            wrapup_fn=AsyncMock(),
            pairs=["XAUUSD"],
        )
        sched.configure()
        jobs = sched.jobs

        job_ids = [j.id for j in jobs]
        # No JPY pair → no asian_scan job
        assert "fx_asian_scan" not in job_ids
        assert "fx_london_scan" in job_ids
        assert "fx_preny_scan" in job_ids
        assert "fx_wrapup" in job_ids
        assert "fx_dns_refresh" in job_ids
        assert len(jobs) == 4

    def test_configure_all_pairs_creates_4_jobs(self):
        """All pairs includes JPY → 4 jobs."""
        from scheduler.runner import ScanScheduler

        sched = ScanScheduler(
            scan_fn=AsyncMock(),
            wrapup_fn=AsyncMock(),
            pairs=["XAUUSD", "EURUSD", "GBPJPY", "USDJPY"],
        )
        sched.configure()
        job_ids = [j.id for j in sched.jobs]
        assert "fx_asian_scan" in job_ids
        assert "fx_dns_refresh" in job_ids
        assert len(sched.jobs) == 5

    def test_configure_without_wrapup_skips_wrapup_job(self):
        from scheduler.runner import ScanScheduler

        sched = ScanScheduler(scan_fn=AsyncMock(), pairs=["XAUUSD"])
        sched.configure()
        job_ids = [j.id for j in sched.jobs]
        assert "wrapup" not in job_ids

    @pytest.mark.asyncio
    async def test_start_and_shutdown(self):
        from scheduler.runner import ScanScheduler

        sched = ScanScheduler(scan_fn=AsyncMock(), pairs=["XAUUSD"])
        sched.configure()
        sched.start()
        assert len(sched.jobs) >= 2
        sched.shutdown()

    @pytest.mark.asyncio
    async def test_run_batch_calls_scan_fn(self):
        """_run_batch should invoke scan_fn for each pair."""
        from scheduler.runner import ScanScheduler

        mock_scan = AsyncMock()
        sched = ScanScheduler(scan_fn=mock_scan, pairs=["XAUUSD"])
        await sched._run_batch(["XAUUSD", "EURUSD"])
        assert mock_scan.await_count == 2
        mock_scan.assert_any_await("XAUUSD")
        mock_scan.assert_any_await("EURUSD")

    @pytest.mark.asyncio
    async def test_run_batch_continues_on_error(self):
        """If scan_fn raises for one pair, batch continues to next pair."""
        from scheduler.runner import ScanScheduler

        mock_scan = AsyncMock(side_effect=[Exception("boom"), None])
        sched = ScanScheduler(scan_fn=mock_scan, pairs=["A", "B"])
        await sched._run_batch(["A", "B"])
        assert mock_scan.await_count == 2


# ──────────────────────────────────────────────────────────────────────
# 2. Pipeline integration (orchestrator → WA → dashboard)
# ──────────────────────────────────────────────────────────────────────


class TestPipelineIntegration:
    """Integration test: mock Gemini, verify WA + dashboard receive events."""

    @pytest.mark.asyncio
    async def test_scan_pair_pushes_to_dashboard(self):
        """scan_pair() should call push_analysis_update."""
        from agent.orchestrator import AnalysisOutcome
        from agent.state_machine import AnalysisState

        mock_outcome = AnalysisOutcome(
            pair="XAUUSD",
            state=AnalysisState.SCANNING,
            elapsed_seconds=1.0,
        )

        with (
            patch("main._get_orchestrator") as mock_orch_factory,
            patch("main.push_analysis_update", new_callable=AsyncMock) as mock_push,
            patch("main.push_state_change", new_callable=AsyncMock),
        ):
            mock_orch = MagicMock()
            mock_orch.state = AnalysisState.SCANNING
            mock_orch.run_scan = AsyncMock(return_value=mock_outcome)
            mock_orch_factory.return_value = mock_orch

            from main import scan_pair

            await scan_pair("XAUUSD")
            mock_push.assert_awaited_once()
            call_args = mock_push.call_args
            assert call_args[0][0] == "XAUUSD"
            assert call_args[0][1]["state"] == "SCANNING"

    @pytest.mark.asyncio
    async def test_scan_pair_pushes_state_change_on_transition(self):
        """When state changes, push_state_change is called."""
        from agent.orchestrator import AnalysisOutcome
        from agent.state_machine import AnalysisState

        mock_outcome = AnalysisOutcome(
            pair="XAUUSD",
            state=AnalysisState.WATCHING,
            elapsed_seconds=0.5,
        )

        with (
            patch("main._get_orchestrator") as mock_orch_factory,
            patch("main.push_analysis_update", new_callable=AsyncMock),
            patch("main.push_state_change", new_callable=AsyncMock) as mock_sc,
        ):
            mock_orch = MagicMock()
            mock_orch.state = AnalysisState.SCANNING  # old state
            mock_orch.run_scan = AsyncMock(return_value=mock_outcome)
            mock_orch_factory.return_value = mock_orch

            from main import scan_pair

            await scan_pair("XAUUSD")
            mock_sc.assert_awaited_once_with("XAUUSD", "SCANNING", "WATCHING")

    @pytest.mark.asyncio
    async def test_scan_pair_notifies_on_state_change(self):
        """When state changes and _notification_handler is set, it's called."""
        from agent.orchestrator import AnalysisOutcome
        from agent.state_machine import AnalysisState

        mock_outcome = AnalysisOutcome(
            pair="XAUUSD",
            state=AnalysisState.WATCHING,
            elapsed_seconds=0.5,
        )

        mock_handler = AsyncMock()

        with (
            patch("main._get_orchestrator") as mock_orch_factory,
            patch("main.push_analysis_update", new_callable=AsyncMock),
            patch("main.push_state_change", new_callable=AsyncMock),
            patch("main._notification_handler", mock_handler),
        ):
            mock_orch = MagicMock()
            mock_orch.state = AnalysisState.SCANNING
            mock_orch.run_scan = AsyncMock(return_value=mock_outcome)
            mock_orch_factory.return_value = mock_orch

            from main import scan_pair

            await scan_pair("XAUUSD")
            mock_handler.on_state_change.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────
# 3. Wrap-up routine
# ──────────────────────────────────────────────────────────────────────


class TestWrapup:
    """Test daily_wrapup routine."""

    @pytest.mark.asyncio
    async def test_wrapup_pushes_stats(self):
        from main import daily_wrapup
        import main as main_mod

        main_mod._orchestrators = {}
        main_mod._notification_handler = None

        with patch("main.update_daily_stats", new_callable=AsyncMock) as mock_stats:
            await daily_wrapup()
            mock_stats.assert_awaited_once()
            data = mock_stats.call_args[0][0]
            assert "date" in data
            assert "total_scans" in data

    @pytest.mark.asyncio
    async def test_wrapup_sends_daily_summary_notification(self):
        import main as main_mod

        main_mod._orchestrators = {}
        mock_handler = MagicMock()
        mock_handler.on_daily_end = AsyncMock()
        main_mod._notification_handler = mock_handler

        with patch("main.update_daily_stats", new_callable=AsyncMock):
            await main_mod.daily_wrapup()
            mock_handler.on_daily_end.assert_awaited_once()
        # Reset
        main_mod._notification_handler = None


# ──────────────────────────────────────────────────────────────────────
# 4. FastAPI startup/shutdown hooks
# ──────────────────────────────────────────────────────────────────────


class TestLifecycle:
    """Test FastAPI on_startup / on_shutdown."""

    @pytest.mark.asyncio
    async def test_on_startup_initialises_scheduler(self):
        import main as main_mod

        # Ensure clean state
        main_mod.app.state._agent_started = False
        main_mod._scheduler = None
        main_mod._wa_notifier = None
        main_mod._notification_handler = None

        # No WA phone → should still start scheduler
        with (
            patch.object(main_mod, "WHATSAPP_PHONE", ""),
            patch.object(main_mod, "MVP_PAIRS", ["XAUUSD"]),
        ):
            await main_mod.on_startup()
            assert main_mod._scheduler is not None
            assert len(main_mod._scheduler.jobs) >= 2
            assert main_mod._wa_notifier is None  # phone not set
            # Clean up
            main_mod._scheduler.shutdown()
            main_mod._scheduler = None

    @pytest.mark.asyncio
    async def test_on_startup_with_whatsapp(self):
        import main as main_mod

        main_mod.app.state._agent_started = False
        main_mod._scheduler = None
        main_mod._wa_notifier = None
        main_mod._notification_handler = None

        with (
            patch.object(main_mod, "WHATSAPP_PHONE", "628123456789"),
            patch.object(main_mod, "WHATSAPP_API_URL", "http://localhost:3000"),
            patch.object(main_mod, "MVP_PAIRS", ["XAUUSD"]),
        ):
            await main_mod.on_startup()
            assert main_mod._wa_notifier is not None
            assert main_mod._notification_handler is not None
            # Clean up
            main_mod._scheduler.shutdown()
            main_mod._scheduler = None
            main_mod._wa_notifier = None
            main_mod._notification_handler = None

    @pytest.mark.asyncio
    async def test_on_shutdown_stops_scheduler(self):
        import main as main_mod
        from scheduler.runner import ScanScheduler

        mock_sched = MagicMock(spec=ScanScheduler)
        main_mod._scheduler = mock_sched
        await main_mod.on_shutdown()
        mock_sched.shutdown.assert_called_once()
        main_mod._scheduler = None
