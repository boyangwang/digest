"""
Tests for scheduler.py — Timing logic and state management.

Tests cover:
- Digest job as the only state reset point
- Nudge window enforcement
- Sleep stops nudging
- State transitions
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
    async def test_digest_job_resets_cycle(self):
        """digest_job clears sleep_received and sets digest_generated."""
        s = DigestScheduler()
        s._on_digest_callback = AsyncMock()
        s._sleep_received = True
        s._digest_generated = False

        await s._digest_job()

        assert s._sleep_received is False
        assert s._digest_generated is True

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
    async def test_digest_calls_callback(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_digest_callback = mock_cb

        await s._digest_job()

        mock_cb.assert_called_once()
        assert s._digest_generated

    @pytest.mark.asyncio
    async def test_digest_always_runs_no_guard(self):
        """digest_job always calls callback — no 'already generated' guard."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_digest_callback = mock_cb
        s._digest_generated = True  # Already True from last cycle

        await s._digest_job()

        mock_cb.assert_called_once()


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
