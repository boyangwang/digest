"""
Tests for scheduler.py — Timing logic and state management.

Tests cover:
- 22:30 job sends reminder only (no digest generation, no _digest_generated)
- Nudge window enforcement
- Sleep stops nudging
- State transitions
- trigger_generate_now() for tests that need to simulate /digest
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler import DigestScheduler
from config import SGT


# ============================================================
# State management
# ============================================================

class TestSchedulerState:

    def test_initial_state(self):
        s = DigestScheduler()
        assert not s.sleep_received
        assert not s.digest_generated

    def test_mark_sleep(self):
        s = DigestScheduler()
        s.mark_sleep()
        assert s.sleep_received

    def test_mark_digest_generated(self):
        s = DigestScheduler()
        s.mark_digest_generated()
        assert s.digest_generated


# ============================================================
# Digest job as cycle reset
# ============================================================

class TestDigestCycleReset:

    @pytest.mark.asyncio
    async def test_digest_job_resets_sleep_received_only(self):
        """digest_job (22:30 reminder) clears sleep_received but does NOT set digest_generated."""
        s = DigestScheduler()
        s._on_reminder_callback = AsyncMock()
        s._sleep_received = True
        s._digest_generated = False

        await s._digest_job()

        assert s._sleep_received is False
        assert s._digest_generated is False  # reminder does NOT enable nudging

    @pytest.mark.asyncio
    async def test_nudge_job_does_not_reset_state(self):
        """nudge_job is read-only on state — never resets flags."""
        s = DigestScheduler()
        s._on_nudge_callback = AsyncMock()
        s._digest_generated = True
        s._sleep_received = True

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 1, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        assert s._digest_generated is True
        assert s._sleep_received is True


# ============================================================
# Digest job
# ============================================================

class TestDigestJob:

    @pytest.mark.asyncio
    async def test_digest_job_calls_reminder_callback(self):
        """22:30 cron job calls reminder callback, NOT digest callback."""
        s = DigestScheduler()
        reminder_cb = AsyncMock()
        digest_cb = AsyncMock()
        s._on_reminder_callback = reminder_cb
        s._on_digest_callback = digest_cb

        await s._digest_job()

        reminder_cb.assert_called_once()
        digest_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_digest_job_does_not_set_digest_generated(self):
        """22:30 reminder does NOT set _digest_generated."""
        s = DigestScheduler()
        s._on_reminder_callback = AsyncMock()
        s._digest_generated = False

        await s._digest_job()

        assert s._digest_generated is False

    @pytest.mark.asyncio
    async def test_digest_job_always_sends_reminder(self):
        """22:30 job always sends reminder — no guard on prior state."""
        s = DigestScheduler()
        reminder_cb = AsyncMock()
        s._on_reminder_callback = reminder_cb
        s._digest_generated = True  # Already True from previous /digest

        await s._digest_job()

        reminder_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_generate_now_calls_digest_callback(self):
        """trigger_generate_now() simulates /digest — calls digest callback."""
        s = DigestScheduler()
        digest_cb = AsyncMock()
        s._on_digest_callback = digest_cb

        await s.trigger_generate_now()

        digest_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_skips_after_22_30_without_digest_command(self):
        """22:30 reminder alone does NOT enable nudging."""
        s = DigestScheduler()
        nudge_cb = AsyncMock()
        s._on_nudge_callback = nudge_cb
        s._on_reminder_callback = AsyncMock()

        # 22:30 fires — only reminder
        await s._digest_job()

        # Nudge fires at 23:00 — should skip because /digest was never sent
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 23, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        nudge_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_enabled_after_mark_digest_generated(self):
        """After mark_digest_generated() (called by /digest), nudging works."""
        s = DigestScheduler()
        nudge_cb = AsyncMock()
        s._on_nudge_callback = nudge_cb
        s.mark_digest_generated()

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 9, 23, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        nudge_cb.assert_called_once()


# ============================================================
# Nudge job
# ============================================================

class TestNudgeJob:

    @pytest.mark.asyncio
    async def test_nudge_skips_if_sleep_received(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._digest_generated = True
        s._sleep_received = True

        await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_skips_if_no_digest(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._digest_generated = False

        await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_fires_in_window(self):
        """Nudge should fire when: digest generated, no sleep, within 22:30-07:00."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._digest_generated = True
        s._sleep_received = False

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 5, 23, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_skips_outside_window(self):
        """Nudge should NOT fire during daytime (e.g., 14:00)."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._digest_generated = True
        s._sleep_received = False

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 5, 14, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()
