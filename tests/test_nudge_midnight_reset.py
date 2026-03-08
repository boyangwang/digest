"""
Tests for nudge state machine — pure state, no date logic.

ARCHITECTURE ENFORCEMENT: These tests make it impossible to implement
with date-based resets. The scheduler must be a pure state machine:

  digest_job → starts cycle (digest_generated=True, sleep_received=False)
  mark_sleep → ends nudging (sleep_received=True)
  nudge_job  → checks flags + time window → fire or skip

No _today. No _reset_if_new_day. No midnight/noon reset jobs.
The digest job IS the only reset point.

Production bug (Mar 5-6, 2026): nudges stopped at midnight because
_reset_if_new_day() cleared _digest_generated when the date changed.
The fix is not "smarter date logic" — it's removing date logic entirely.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from config import SGT
from scheduler import DigestScheduler


# ============================================================
# Architecture enforcement — these tests PREVENT date-based designs
# ============================================================

class TestArchitectureEnforcement:
    """Ensure scheduler uses pure state machine, not date tracking."""

    def test_no_today_attribute(self):
        """Scheduler must NOT have a _today field. Date tracking is forbidden."""
        s = DigestScheduler()
        assert not hasattr(s, '_today'), (
            "Scheduler has _today attribute. Date tracking is forbidden. "
            "The digest job is the only reset point — no date comparison needed."
        )

    def test_no_reset_if_new_day_method(self):
        """_reset_if_new_day must not exist. Dates don't drive state."""
        s = DigestScheduler()
        assert not hasattr(s, '_reset_if_new_day'), (
            "Scheduler has _reset_if_new_day method. "
            "State resets happen in digest_job, not on date boundaries."
        )

    def test_no_midnight_reset_job(self):
        """No separate 'reset' job should exist. Only digest + nudge jobs."""
        s = DigestScheduler()
        assert not hasattr(s, '_midnight_reset'), (
            "Scheduler has _midnight_reset method. "
            "There should be no standalone reset mechanism."
        )


# ============================================================
# State machine: digest_job as cycle start
# ============================================================

class TestDigestJobCycleStart:
    """digest_job (22:30) sends reminder only — /digest command enables nudging."""

    @pytest.mark.asyncio
    async def test_digest_job_resets_sleep_and_sends_reminder(self):
        """After digest_job, sleep_received=False and reminder sent. digest_generated stays False."""
        s = DigestScheduler()
        s._on_reminder_callback = AsyncMock()
        s._on_digest_callback = AsyncMock()
        s._on_nudge_callback = AsyncMock()

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 5, 22, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._digest_job()

        assert s._digest_generated is False  # reminder does NOT enable nudging
        assert s._sleep_received is False
        s._on_reminder_callback.assert_called_once()
        s._on_digest_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_digest_job_resets_sleep_from_previous_cycle(self):
        """22:30 reminder clears sleep_received from last night's /sleep."""
        s = DigestScheduler()
        s._on_reminder_callback = AsyncMock()
        # Simulate: last night's /sleep was received
        s._sleep_received = True
        s._digest_generated = False

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 22, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._digest_job()

        assert s._sleep_received is False, (
            "digest_job must clear sleep_received from previous cycle. "
            "The new cycle starts fresh."
        )
        assert s._digest_generated is False  # still False until /digest sent

    @pytest.mark.asyncio
    async def test_digest_job_always_sends_reminder(self):
        """22:30 reminder always fires — no guard should block it."""
        s = DigestScheduler()
        s._on_reminder_callback = AsyncMock()
        # Simulate: digest_generated still True from last night's /digest
        s._digest_generated = True
        s._sleep_received = True

        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 22, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._digest_job()

        s._on_reminder_callback.assert_called_once(), (
            "digest_job must ALWAYS send reminder — no guard should block it."
        )
        assert s._sleep_received is False, "New cycle must clear sleep"


# ============================================================
# State machine: nudge_job pure flag checks
# ============================================================

class TestNudgePureFlagChecks:
    """Nudge decision depends ONLY on flags + time window. Never on dates."""

    @pytest.fixture
    def active_cycle(self):
        """A scheduler in an active nudge cycle."""
        s = DigestScheduler()
        s._on_nudge_callback = AsyncMock()
        s._digest_generated = True
        s._sleep_received = False
        return s

    @pytest.mark.asyncio
    async def test_nudge_fires_when_active_cycle(self, active_cycle):
        """Nudge fires when: digest_generated=True, sleep=False, in window."""
        s = active_cycle
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 5, 23, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_skips_when_no_cycle(self):
        """Nudge skips when digest not yet generated."""
        s = DigestScheduler()
        s._on_nudge_callback = AsyncMock()
        s._digest_generated = False
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 5, 23, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        assert not s._on_nudge_callback.called

    @pytest.mark.asyncio
    async def test_nudge_skips_when_sleep(self, active_cycle):
        """Nudge skips when /sleep received."""
        s = active_cycle
        s._sleep_received = True
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 5, 23, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        assert not s._on_nudge_callback.called

    @pytest.mark.asyncio
    async def test_nudge_skips_outside_window(self, active_cycle):
        """Nudge skips at 08:00 — outside the 22:30-07:00 window."""
        s = active_cycle
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 8, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        assert not s._on_nudge_callback.called

    @pytest.mark.asyncio
    async def test_nudge_independent_of_calendar(self, active_cycle):
        """Nudge decision must not change based on what DATE it is.
        
        Same flags, same hour — different date — same result.
        This test ensures no date-string comparison happens.
        """
        s = active_cycle
        results = []
        for date in [5, 6, 7, 15, 28]:  # Various March dates
            s._on_nudge_callback = AsyncMock()
            with patch("scheduler.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 3, date, 1, 0, tzinfo=SGT)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                await s._nudge_job()
            results.append(s._on_nudge_callback.called)

        assert all(results), (
            "Nudge should fire on ALL dates with same state. "
            f"Results by date: {dict(zip([5,6,7,15,28], results))}. "
            "If any date differs, there's hidden date logic."
        )


# ============================================================
# Midnight crossover — the original bug scenario
# ============================================================

class TestMidnightCrossover:
    """The nudge window 22:30-07:00 spans midnight. State must survive."""

    @pytest.fixture
    def active_cycle(self):
        s = DigestScheduler()
        s._on_nudge_callback = AsyncMock()
        s._digest_generated = True
        s._sleep_received = False
        return s

    @pytest.mark.asyncio
    async def test_nudge_at_0030(self, active_cycle):
        """00:30 — nudge must fire. This was the exact failure point."""
        s = active_cycle
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 0, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        assert s._on_nudge_callback.called, (
            "Nudge at 00:30 must fire. This is the original production bug."
        )

    @pytest.mark.asyncio
    async def test_nudge_at_0100(self, active_cycle):
        s = active_cycle
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 1, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        assert s._on_nudge_callback.called

    @pytest.mark.asyncio
    async def test_nudge_at_0600(self, active_cycle):
        s = active_cycle
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 6, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()
        assert s._on_nudge_callback.called

    @pytest.mark.asyncio
    async def test_continuous_nudging_full_window(self, active_cycle):
        """Simulate entire 22:30→07:00 window. Every nudge must fire."""
        s = active_cycle
        times = [
            (2026, 3, 5, 22, 30),  # Same evening
            (2026, 3, 5, 23, 0),
            (2026, 3, 5, 23, 30),
            (2026, 3, 6, 0, 0),    # Midnight crossover
            (2026, 3, 6, 0, 30),
            (2026, 3, 6, 1, 0),
            (2026, 3, 6, 2, 0),
            (2026, 3, 6, 3, 0),
            (2026, 3, 6, 4, 0),
            (2026, 3, 6, 5, 0),
            (2026, 3, 6, 6, 0),
            (2026, 3, 6, 6, 30),
        ]
        results = {}
        for t in times:
            s._on_nudge_callback = AsyncMock()
            with patch("scheduler.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(*t, tzinfo=SGT)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                await s._nudge_job()
            label = "%02d:%02d" % (t[3], t[4])
            results[label] = s._on_nudge_callback.called

        failed = [t for t, ok in results.items() if not ok]
        assert not failed, (
            f"Nudges failed at: {failed}. Full results: {results}. "
            "The entire 22:30-07:00 window must work without interruption."
        )

    @pytest.mark.asyncio
    async def test_no_state_mutation_at_midnight(self, active_cycle):
        """Calling _nudge_job at midnight must NOT mutate state flags.
        
        The nudge job should be read-only on state (except triggering callback).
        No resets, no flag changes.
        """
        s = active_cycle
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 0, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        assert s._digest_generated is True, "nudge_job must not clear digest_generated"
        assert s._sleep_received is False, "nudge_job must not change sleep_received"


# ============================================================
# Cycle isolation — sleep persists, new digest resets
# ============================================================

class TestCycleIsolation:
    """State transitions must be clean across cycles."""

    @pytest.mark.asyncio
    async def test_sleep_persists_across_midnight(self):
        """If /sleep at 23:00, nudge at 00:30 must still skip."""
        s = DigestScheduler()
        s._on_nudge_callback = AsyncMock()
        s._digest_generated = True
        s._sleep_received = False

        # /sleep at 23:00
        s.mark_sleep()

        # Nudge at 00:30 (next day)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 0, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        assert not s._on_nudge_callback.called, (
            "/sleep was received — nudge must stay stopped across midnight."
        )

    @pytest.mark.asyncio
    async def test_new_digest_clears_old_sleep(self):
        """22:30 reminder clears /sleep; nudge fires only after /digest command."""
        s = DigestScheduler()
        s._on_reminder_callback = AsyncMock()
        s._on_nudge_callback = AsyncMock()
        s._sleep_received = True  # Last night's /sleep

        # 22:30 reminder fires — clears sleep but does NOT enable nudging
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 22, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._digest_job()

        assert s._sleep_received is False, "22:30 reminder must clear old /sleep"

        # Nudge at 23:00 — should NOT fire yet (no /digest sent)
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 23, 0, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        s._on_nudge_callback.assert_not_called()

        # User sends /digest → mark_digest_generated()
        s.mark_digest_generated()

        # Nudge at 23:30 — should now fire
        with patch("scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 23, 30, tzinfo=SGT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await s._nudge_job()

        s._on_nudge_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_only_digest_job_resets_state(self):
        """No method other than _digest_job should reset flags.
        
        Specifically: _nudge_job must be read-only on state,
        and there should be no hidden reset mechanism.
        """
        s = DigestScheduler()
        s._on_nudge_callback = AsyncMock()
        s._digest_generated = True
        s._sleep_received = True

        # Call nudge multiple times at different "dates"
        for day in [5, 6, 7]:
            with patch("scheduler.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 3, day, 2, 0, tzinfo=SGT)
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                await s._nudge_job()

        # State must be UNCHANGED — nudge_job is read-only
        assert s._digest_generated is True, "nudge_job must never clear digest_generated"
        assert s._sleep_received is True, "nudge_job must never clear sleep_received"
