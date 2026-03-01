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
    NUDGE_START_HOUR, NUDGE_START_MINUTE,
    NUDGE_END_HOUR, NUDGE_END_MINUTE,
)

logger = logging.getLogger("digest-bot.scheduler")


class DigestScheduler:
    """Manages digest and nudge scheduling with in-memory state."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=SGT)
        self._sleep_received = False
        self._digest_generated = False
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
        """Triggered every 30 min during nudge window."""
        self._reset_if_new_day()

        if self._sleep_received:
            logger.info("Sleep received. Skipping nudge.")
            return

        if not self._digest_generated:
            logger.info("Digest not yet generated. Skipping nudge.")
            return

        # Check if within nudge window (22:30 - 07:00)
        now = datetime.now(SGT)
        h, m = now.hour, now.minute
        in_window = False
        if h >= 23 or h < NUDGE_END_HOUR:
            in_window = True
        elif h == NUDGE_START_HOUR and m >= NUDGE_START_MINUTE:
            in_window = True
        elif h == NUDGE_END_HOUR and m <= NUDGE_END_MINUTE:
            in_window = True

        if not in_window:
            logger.info(f"Outside nudge window ({h:02d}:{m:02d}). Skipping.")
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

        # Nudge every 30 min (22:00 - 07:00 range, actual window checked in job)
        # Using cron: minute=0,30 hour=22,23,0,1,2,3,4,5,6,7
        self.scheduler.add_job(
            self._nudge_job,
            CronTrigger(
                minute=f"0,{NUDGE_INTERVAL_MINUTES}",
                hour="22,23,0,1,2,3,4,5,6,7",
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
