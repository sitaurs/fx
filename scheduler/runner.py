"""
scheduler/runner.py — APScheduler-based scan scheduler.

Schedules daily scans per masterplan §16:
  - 06:00 WIB  Asian scan   (USDJPY, GBPJPY)
  - 13:30 WIB  London scan  (all pairs)
  - 19:00 WIB  Pre-NY scan  (refresh)
  - 22:30 WIB  Wrap-up      (cancel stale, daily summary)

WIB = UTC+7, so times are stored as UTC offsets.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Awaitable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import MVP_PAIRS

logger = logging.getLogger(__name__)

# WIB offset
_WIB = timezone(timedelta(hours=7))


# L-46: Job ID prefix for log clarity and multi-instance support.
_JOB_PREFIX = "fx_"


class ScanScheduler:
    """Schedule analysis scans using APScheduler.

    Parameters
    ----------
    scan_fn:
        Async callable ``scan_fn(pair: str) -> None`` that runs the
        orchestrator for a single pair.
    wrapup_fn:
        Async callable ``wrapup_fn() -> None`` for end-of-day routine.
    pairs:
        List of pairs to scan.  Defaults to ``MVP_PAIRS``.
    """

    def __init__(
        self,
        scan_fn: Callable[[str], Awaitable[None]],
        wrapup_fn: Optional[Callable[[], Awaitable[None]]] = None,
        pairs: Optional[list[str]] = None,
        batch_fn: Optional[Callable[[list[str]], Awaitable[None]]] = None,
    ) -> None:
        self._scan_fn = scan_fn
        self._batch_fn = batch_fn
        self._wrapup_fn = wrapup_fn
        self.pairs = pairs or MVP_PAIRS
        self._scheduler = AsyncIOScheduler(
            timezone="Asia/Jakarta",
            # L-47: Allow up to 5 min late execution before considering a
            # misfire.  APScheduler default is 1 s which is too strict for
            # I/O-bound async workloads.
            job_defaults={"misfire_grace_time": 300},
        )

    # ------------------------------------------------------------------
    # Setup jobs
    # ------------------------------------------------------------------

    def configure(self) -> None:
        """Register all cron jobs.  Call before ``start()``."""
        # Asian scan — 06:00 WIB (Mon-Fri) — JPY pairs only
        jpy_pairs = [p for p in self.pairs if "JPY" in p]
        if jpy_pairs:
            self._scheduler.add_job(
                self._run_batch,
                CronTrigger(hour=6, minute=0, day_of_week="mon-fri"),
                args=[jpy_pairs],
                id=f"{_JOB_PREFIX}asian_scan",
                name="Asian Scan (JPY)",
                replace_existing=True,
            )

        # London scan — 13:30 WIB (Mon-Fri) — all pairs
        self._scheduler.add_job(
            self._run_batch,
            CronTrigger(hour=13, minute=30, day_of_week="mon-fri"),
            args=[self.pairs],
            id=f"{_JOB_PREFIX}london_scan",
            name="London Scan (all)",
            replace_existing=True,
        )

        # Pre-NY scan — 19:00 WIB (Mon-Fri) — all pairs refresh
        self._scheduler.add_job(
            self._run_batch,
            CronTrigger(hour=19, minute=0, day_of_week="mon-fri"),
            args=[self.pairs],
            id=f"{_JOB_PREFIX}preny_scan",
            name="Pre-NY Scan (refresh)",
            replace_existing=True,
        )

        # Wrap-up — 22:30 WIB (Mon-Fri)
        if self._wrapup_fn:
            self._scheduler.add_job(
                self._wrapup_fn,
                CronTrigger(hour=22, minute=30, day_of_week="mon-fri"),
                id=f"{_JOB_PREFIX}wrapup",
                name="Daily Wrap-Up",
                replace_existing=True,
            )

        # FIX C-04: DNS refresh every 6 hours to keep OANDA IPs current
        self._scheduler.add_job(
            self._refresh_dns,
            CronTrigger(hour="*/6", minute=15, day_of_week="mon-fri"),
            id=f"{_JOB_PREFIX}dns_refresh",
            name="DNS Override Refresh",
            replace_existing=True,
        )

        logger.info(
            "Scheduler configured: %d jobs", len(self._scheduler.get_jobs())
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the scheduler (non-blocking)."""
        self._scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shut down gracefully."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    @property
    def jobs(self) -> list:
        return self._scheduler.get_jobs()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _refresh_dns(self) -> None:
        """FIX C-04: Periodic DNS override refresh via DoH.

        Re-validates cached IPs for OANDA hostnames and updates if stale.
        """
        try:
            from data.fetcher import refresh_dns_overrides, _dns_overrides_ref
            if _dns_overrides_ref:
                updated = refresh_dns_overrides(_dns_overrides_ref)
                _dns_overrides_ref.update(updated)
                logger.info("DNS overrides refreshed: %s", list(updated.keys()))
            else:
                logger.debug("No DNS overrides configured — skip refresh")
        except Exception as exc:
            logger.warning("DNS refresh failed: %s", exc)

    async def _run_batch(self, pairs: list[str]) -> None:
        """Run batch_fn (ranked cherry-pick) if available, else scan_fn per pair.

        Fault isolation: if batch_fn fails, fall back to per-pair scanning.
        Per-pair scanning isolates failures so one bad pair doesn't block the rest.
        """
        if self._batch_fn:
            try:
                await self._batch_fn(pairs)
                return
            except Exception as exc:
                logger.error(
                    "Batch scan failed: %s — falling back to per-pair scan",
                    exc,
                )
                # Fall through to per-pair scanning

        logger.info("Scan batch start: %s", pairs)
        success = 0
        failed = 0
        for pair in pairs:
            try:
                await self._scan_fn(pair)
                success += 1
            except Exception as exc:
                failed += 1
                logger.error("Scan failed for %s: %s", pair, exc)
        logger.info(
            "Scan batch done: %d success, %d failed out of %d",
            success, failed, len(pairs),
        )
