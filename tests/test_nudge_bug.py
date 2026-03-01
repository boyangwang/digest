"""
Bug #1: Nudging completely broken for manual /digest.

Reported: 2026-03-01. Bot in ACTIVE state for hours with zero nudges.

Root cause:
  1. Nudge cron only fires at hours 22,23,0-7. Manual /digest during
     daytime (08:00-21:59) → nudge function NEVER called.
  2. Even at 22:00, in-window check requires minute >= 30 → 22:00 nudge skipped.
  3. After crash recovery, _digest_generated stays False → no nudges.

Expected behavior:
  - Manual /digest at ANY time → nudging starts immediately
  - Nudge every 30 min until /sleep or safety timeout
  - After crash recovery with active file → nudging resumes
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler import DigestScheduler
from config import SGT


class TestNudgeDuringDaytime:
    """Nudging must work when /digest is called during daytime hours."""

    @pytest.mark.asyncio
    async def test_nudge_fires_at_1500_after_manual_digest(self):
        """FAILING BEFORE FIX: /digest at 15:00 → nudge at 15:30 should fire."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        # Mock time to 15:30 (daytime — previously outside window)
        mock_now = datetime.now(SGT).replace(hour=15, minute=30)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_fires_at_0900_after_manual_digest(self):
        """Nudge should fire at 09:00 if digest was triggered."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=9, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_fires_at_2200_sharp(self):
        """FAILING BEFORE FIX: 22:00 nudge was skipped by in_window check."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=22, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()


class TestNudgeGating:
    """Nudge should ONLY fire when digest is active and no sleep received."""

    @pytest.mark.asyncio
    async def test_no_nudge_without_digest(self):
        """No digest generated → no nudge, even during prime time."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = False
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=23, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_after_sleep(self):
        """Sleep received → no nudge, even if digest was generated."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = True

        mock_now = datetime.now(SGT).replace(hour=23, minute=30)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()


class TestNudgeSafetyTimeout:
    """Nudging should auto-stop after a safety timeout to prevent infinite nudges."""

    @pytest.mark.asyncio
    async def test_nudge_stops_after_timeout(self):
        """If digest has been active for >10 hours, stop nudging."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False
        # Digest started 11 hours ago
        s._digest_generated_at = datetime.now(SGT) - timedelta(hours=11)

        mock_now = datetime.now(SGT)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_continues_within_timeout(self):
        """If digest has been active for <10 hours, keep nudging."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False
        s._digest_generated_at = datetime.now(SGT) - timedelta(hours=3)

        mock_now = datetime.now(SGT)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()


class TestNudgeCronSchedule:
    """The nudge cron must fire at ALL hours, not just 22-7."""

    @pytest.mark.asyncio
    async def test_nudge_cron_covers_all_hours(self):
        """Nudge job should be scheduled for all 24 hours."""
        s = DigestScheduler()
        s.set_callbacks(on_digest=AsyncMock(), on_nudge=AsyncMock())
        s.start()

        nudge_job = s.scheduler.get_job("nudge")
        assert nudge_job is not None

        # Check the trigger fields — should NOT restrict hours
        for field in nudge_job.trigger.fields:
            if field.name == "hour":
                exprs = str(field)
                assert exprs == "*", \
                    f"Nudge cron restricts hours to '{exprs}' — must be '*' for all hours"
                break

        s.stop()


class TestCrashRecoveryNudging:
    """After crash recovery, nudging should resume if there's an active file."""

    def test_mark_digest_after_recovery(self):
        """When recovering an active file, scheduler must know digest exists."""
        s = DigestScheduler()
        # Simulate recovery
        s.mark_digest_generated()
        assert s.digest_generated
        assert s._digest_generated_at is not None
