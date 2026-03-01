"""
Tests for scheduler.py — Timing logic and state management.

Tests cover:
- Day reset at boundary
- Nudge window enforcement
- Sleep stops nudging
- Digest runs only once per day
- State transitions
"""

import sys
from datetime import datetime, timedelta
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
# Day reset
# ============================================================

class TestDayReset:

    def test_resets_on_new_day(self):
        s = DigestScheduler()
        s._sleep_received = True
        s._digest_generated = True
        s._today = "2026-02-28"  # Yesterday

        s._reset_if_new_day()

        # Should reset because today is different
        today = datetime.now(SGT).strftime("%Y-%m-%d")
        if today != "2026-02-28":
            assert not s._sleep_received
            assert not s._digest_generated

    def test_no_reset_same_day(self):
        s = DigestScheduler()
        today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._today = today
        s._sleep_received = True
        s._digest_generated = True

        s._reset_if_new_day()

        assert s._sleep_received
        assert s._digest_generated


# ============================================================
# Digest job
# ============================================================

class TestDigestJob:

    @pytest.mark.asyncio
    async def test_digest_calls_callback(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_digest_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")

        await s._digest_job()

        mock_cb.assert_called_once()
        assert s._digest_generated

    @pytest.mark.asyncio
    async def test_digest_skips_if_already_generated(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_digest_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True

        await s._digest_job()

        mock_cb.assert_not_called()


# ============================================================
# Nudge job
# ============================================================

class TestNudgeJob:

    @pytest.mark.asyncio
    async def test_nudge_skips_if_sleep_received(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = True

        await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_skips_if_no_digest(self):
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = False

        await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_fires_in_window(self):
        """Nudge should fire when: digest generated, no sleep, within 22:30-07:00."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        # Mock time to 23:30 (within window)
        mock_now = datetime.now(SGT).replace(hour=23, minute=30)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_skips_outside_window(self):
        """Nudge should NOT fire during daytime (e.g., 14:00)."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        # Mock time to 14:00 (outside window)
        mock_now = datetime.now(SGT).replace(hour=14, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()
