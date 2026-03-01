"""
Bug #1: Nudging doesn't work even within the 22:30-07:00 window.

Reported: 2026-03-01. Bot in ACTIVE state for hours with zero nudges.

Root cause: generate_digest() never calls _scheduler.mark_digest_generated().
Manual /digest creates the file but the scheduler's _digest_generated stays
False, so _nudge_job always skips ("Digest not yet generated").

Same issue after crash recovery: recover_active_on_startup() finds the active
file but doesn't inform the scheduler.

The nudge window (22:30-07:00) is correct design — no nudging outside it.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler import DigestScheduler
from config import SGT


class TestManualDigestInformsScheduler:
    """After manual /digest, scheduler must know digest is active."""

    def test_generate_digest_marks_scheduler(self):
        """generate_digest() must call _scheduler.mark_digest_generated()
        so that nudge jobs fire within the 22:30-07:00 window.
        
        This is the core bug: the function creates the file but never
        tells the scheduler, so _digest_generated stays False forever.
        """
        # We can't easily call generate_digest() in a unit test (it needs
        # Telegram bot context), so we verify the contract by checking
        # that main.py's generate_digest references mark_digest_generated.
        import inspect
        import main
        source = inspect.getsource(main.generate_digest)
        assert "mark_digest_generated" in source, \
            "generate_digest() must call _scheduler.mark_digest_generated()"

    def test_cmd_digest_or_generate_informs_scheduler(self):
        """Either cmd_digest or generate_digest must inform the scheduler."""
        import inspect
        import main
        # Check generate_digest
        gen_source = inspect.getsource(main.generate_digest)
        # Check cmd_digest
        cmd_source = inspect.getsource(main.cmd_digest)
        combined = gen_source + cmd_source
        assert "mark_digest_generated" in combined, \
            "Manual /digest path must call mark_digest_generated()"


class TestCrashRecoveryInformsScheduler:
    """After crash recovery, scheduler must know digest is active."""

    def test_post_init_marks_scheduler_on_recovery(self):
        """When an active file is recovered, the scheduler must be told."""
        import inspect
        import main
        source = inspect.getsource(main.post_init)
        assert "mark_digest_generated" in source, \
            "post_init must call mark_digest_generated() after recovering active file"


class TestNudgeWithinWindow:
    """Nudging works correctly within the 22:30-07:00 window."""

    @pytest.mark.asyncio
    async def test_nudge_fires_at_2300_when_digest_generated(self):
        """At 23:00, with digest generated, nudge should fire."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=23, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_fires_at_0200_when_digest_generated(self):
        """At 02:00, with digest generated, nudge should fire."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=2, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_nudge_at_1500_even_with_digest(self):
        """At 15:00 (outside window), no nudge even if digest is active."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=15, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nudge_at_0800_outside_window(self):
        """At 08:00 (outside window), no nudge."""
        s = DigestScheduler()
        mock_cb = AsyncMock()
        s._on_nudge_callback = mock_cb
        s._today = datetime.now(SGT).strftime("%Y-%m-%d")
        s._digest_generated = True
        s._sleep_received = False

        mock_now = datetime.now(SGT).replace(hour=8, minute=0)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        mock_cb.assert_not_called()
