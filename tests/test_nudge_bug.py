"""
Bug #1: Nudging doesn't fire even within the 22:30-07:00 window.

Root cause: generate_digest() and crash recovery never called
_scheduler.mark_digest_generated(), so the scheduler didn't know
a digest was active.

Nudge requirements (comprehensive):
  1. IN window + ACTIVE → nudge
  2. IN window + NOT active (asleep / no digest) → no nudge
  3. OUT of window + ACTIVE → no nudge
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler import DigestScheduler
from config import SGT


def _make_scheduler(digest_generated=False, sleep_received=False):
    """Helper: create a scheduler with given state."""
    s = DigestScheduler()
    s._on_nudge_callback = AsyncMock()
    s._today = datetime.now(SGT).strftime("%Y-%m-%d")
    s._digest_generated = digest_generated
    s._sleep_received = sleep_received
    return s


async def _run_nudge_at(scheduler, hour, minute=0):
    """Helper: run nudge job as if it's a specific time."""
    mock_now = datetime.now(SGT).replace(hour=hour, minute=minute, second=0)
    with patch("scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        await scheduler._nudge_job()


# ============================================================
# Scenario 1: IN window + ACTIVE → MUST nudge
# ============================================================

class TestInWindowActive:
    """When inside 22:30-07:00 AND digest is active, nudge must fire."""

    @pytest.mark.asyncio
    async def test_nudge_at_2230(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 22, 30)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_2300(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 23, 0)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_2330(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 23, 30)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_0000(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 0, 0)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_0200(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 2, 0)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_0500(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 5, 0)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_0630(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 6, 30)
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_at_0700(self):
        """07:00 is the boundary — still in window."""
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 7, 0)
        s._on_nudge_callback.assert_called_once()


# ============================================================
# Scenario 2: IN window + NOT active → must NOT nudge
# ============================================================

class TestInWindowNotActive:
    """Inside window but not active (asleep or no digest) → no nudge."""

    @pytest.mark.asyncio
    async def test_no_nudge_when_asleep_at_2300(self):
        """Boyang already /sleep'd — no nudge even in window."""
        s = _make_scheduler(digest_generated=True, sleep_received=True)
        await _run_nudge_at(s, 23, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_when_asleep_at_0100(self):
        s = _make_scheduler(digest_generated=True, sleep_received=True)
        await _run_nudge_at(s, 1, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_when_no_digest_at_2300(self):
        """No digest generated yet — no nudge."""
        s = _make_scheduler(digest_generated=False)
        await _run_nudge_at(s, 23, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_when_no_digest_at_0200(self):
        s = _make_scheduler(digest_generated=False)
        await _run_nudge_at(s, 2, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_when_both_false(self):
        """Neither digest nor sleep — no nudge."""
        s = _make_scheduler(digest_generated=False, sleep_received=False)
        await _run_nudge_at(s, 23, 30)
        s._on_nudge_callback.assert_not_called()


# ============================================================
# Scenario 3: OUTSIDE window + ACTIVE → must NOT nudge
# ============================================================

class TestOutsideWindowActive:
    """Outside 22:30-07:00, even with active digest, no nudge."""

    @pytest.mark.asyncio
    async def test_no_nudge_at_0800(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 8, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_0900(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 9, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_1200(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 12, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_1500(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 15, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_1800(self):
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 18, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_2200(self):
        """22:00 is before window start (22:30)."""
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 22, 0)
        s._on_nudge_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_0730(self):
        """07:30 is after window end (07:00)."""
        s = _make_scheduler(digest_generated=True)
        await _run_nudge_at(s, 7, 30)
        s._on_nudge_callback.assert_not_called()


# ============================================================
# Scheduler notification (the actual bug)
# ============================================================

class TestSchedulerNotification:
    """generate_digest() and crash recovery must inform the scheduler."""

    def test_generate_digest_marks_scheduler(self):
        import inspect, main
        source = inspect.getsource(main.generate_digest)
        assert "mark_digest_generated" in source

    def test_crash_recovery_marks_scheduler(self):
        import inspect, main
        source = inspect.getsource(main.post_init)
        assert "mark_digest_generated" in source
