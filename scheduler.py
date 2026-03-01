"""
Scheduler — APScheduler-based timing for digest generation and nudging.

Manages:
  - 22:30 SGT: trigger digest generation
  - Every 30 min after 22:30 until /sleep or 07:00: nudge cycle
  - State: has /sleep been received, has digest been generated

Pure timing logic. No LLM. No file I/O (delegates to recorder/collector).
"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    SGT,
    DIGEST_HOUR, DIGEST_MINUTE,
    NUDGE_INTERVAL_MINUTES,
)

logger = logging.getLogger("digest-bot.scheduler")


NUDGE_SAFETY_TIMEOUT_HOURS = 10  # Auto-stop nudging after this many hours


class DigestScheduler:
    """Manages digest and nudge scheduling with in-memory state."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=SGT)
        self._sleep_received = False
        self._digest_generated = False
        self._digest_generated_at = None  # Track when digest was generated for safety timeout
        self._today: str = ""
        self._on_digest_callback = None
        self._on_nudge_callback = None

    def set_callbacks(self, on_digest, on_nudge):
        """Set async callbacks for digest generation and nudging."""
        self._on_digest_callback = on_digest
        self._on_nudge_callback = on_nudge

    def _reset_if_new_day(self):
        """Reset state at day boundary."""
        today = datetime.now(SGT).strftime("%Y-%m-%d")
        if today != self._today:
            self._today = today
            self._sleep_received = False
            self._digest_generated = False
            self._digest_generated_at = None
            logger.info(f"New day: {today}. State reset.")

    async def _digest_job(self):
        """Triggered at 22:30 SGT."""
        self._reset_if_new_day()
        if self._digest_generated:
            logger.info("Digest already generated today. Skipping.")
            return
        logger.info("Digest job triggered.")
        self._digest_generated = True
        if self._on_digest_callback:
            await self._on_digest_callback()

    async def _nudge_job(self):
        """Triggered every 30 min. Gates: digest active, no sleep, within safety timeout."""
        self._reset_if_new_day()

        if self._sleep_received:
            return

        if not self._digest_generated:
            return

        # Safety timeout: stop nudging after N hours to prevent infinite nudges
        now = datetime.now(SGT)
        if self._digest_generated_at:
            elapsed = (now - self._digest_generated_at).total_seconds() / 3600
            if elapsed > NUDGE_SAFETY_TIMEOUT_HOURS:
                logger.info(f"Safety timeout: digest active for {elapsed:.1f}h. Stopping nudges.")
                return

        logger.info("Nudge job triggered.")
        if self._on_nudge_callback:
            await self._on_nudge_callback()

    def mark_sleep(self):
        """Called when /sleep command received."""
        self._sleep_received = True
        logger.info("Sleep received. Nudging stopped.")

    def mark_digest_generated(self):
        """Called after successful digest generation."""
        self._digest_generated = True
        self._digest_generated_at = datetime.now(SGT)

    @property
    def sleep_received(self) -> bool:
        return self._sleep_received

    @property
    def digest_generated(self) -> bool:
        return self._digest_generated

    def start(self):
        """Start the scheduler with digest and nudge jobs."""
        # Digest generation at 22:30 SGT daily
        self.scheduler.add_job(
            self._digest_job,
            CronTrigger(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, timezone=SGT),
            id="digest",
            replace_existing=True,
        )

        # Nudge every 30 min at ALL hours. Gating is purely state-based:
        # _digest_generated, _sleep_received, safety timeout.
        # Bug fix: previously restricted to hours 22-7, breaking manual /digest.
        self.scheduler.add_job(
            self._nudge_job,
            CronTrigger(
                minute=f"0,{NUDGE_INTERVAL_MINUTES}",
                timezone=SGT,
            ),
            id="nudge",
            replace_existing=True,
        )

        # Midnight reset job
        self.scheduler.add_job(
            self._midnight_reset,
            CronTrigger(hour=12, minute=0, timezone=SGT),  # Reset at noon (safe boundary)
            id="reset",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("Scheduler started. Digest at 22:30, nudges every 30 min.")

    async def _midnight_reset(self):
        """Reset daily state. Runs at noon to ensure clean boundary."""
        self._reset_if_new_day()

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=False)

    async def trigger_digest_now(self):
        """Manually trigger digest generation (for testing)."""
        await self._digest_job()
