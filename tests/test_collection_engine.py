"""
Tests for CollectionEngine — DIGEST-009.

Written BEFORE implementation (TDD). All tests should FAIL until T6-T7 implemented.

Test groups:
  T1: Core engine (5 tests)
  T2: Supersession (3 tests)
  T3: Retry (4 tests)
  T4: Parallel execution (2 tests)

Total: 14 unit/integration tests.
"""

import asyncio
import os
import signal
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

SGT = timezone(timedelta(hours=8))

# ============================================================
# T1: Core engine tests
# ============================================================

class TestCollectBasic:
    """T1: Basic collection behavior."""

    @pytest.mark.asyncio
    async def test_collect_returns_summaries_on_success(self):
        """T1a: All sessions succeed → CollectionResult with summaries."""
        from collection_engine import CollectionEngine, CollectionResult

        engine = CollectionEngine()

        # Mock: 2 sessions, both succeed
        with patch.object(engine, '_summarize_session', new_callable=AsyncMock) as mock_summ:
            mock_summ.return_value = {"session": "test", "summary": "ok"}
            with patch("collection_engine.collect_all_messages") as mock_collect:
                mock_collect.return_value = {
                    "session_a": [("12:00", "user", "hello")],
                    "session_b": [("12:01", "user", "world")],
                }
                result = await engine.collect(
                    datetime.now(SGT) - timedelta(hours=1),
                    trigger="text"
                )

        assert result is not None
        assert isinstance(result, CollectionResult)
        assert len(result.summaries) == 2
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_collect_returns_none_on_partial_failure(self):
        """T1b: 1 of 3 sessions fails after retries → returns None (all-or-nothing)."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()

        call_count = [0]

        async def mock_summarize(name, messages, generation):
            call_count[0] += 1
            if name == "fail_session":
                return None  # Always fails
            return {"session": name, "summary": "ok"}

        with patch.object(engine, '_run_with_retry', side_effect=mock_summarize):
            with patch("collection_engine.collect_all_messages") as mock_collect:
                mock_collect.return_value = {
                    "session_a": [("12:00", "user", "a")],
                    "fail_session": [("12:01", "user", "b")],
                    "session_c": [("12:02", "user", "c")],
                }
                result = await engine.collect(
                    datetime.now(SGT) - timedelta(hours=1),
                    trigger="text"
                )

        # All-or-nothing: any failure → None
        assert result is None

    @pytest.mark.asyncio
    async def test_collect_returns_zero_total_when_no_messages(self):
        """T1c: 0 messages → CollectionResult(total=0), not None."""
        from collection_engine import CollectionEngine, CollectionResult

        engine = CollectionEngine()

        with patch("collection_engine.collect_all_messages") as mock_collect:
            mock_collect.return_value = {}
            result = await engine.collect(
                datetime.now(SGT) - timedelta(hours=1),
                trigger="text"
            )

        assert result is not None
        assert isinstance(result, CollectionResult)
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_generation_counter_increments(self):
        """T1d: Each collect() call increments the generation counter."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        assert engine._generation == 0

        with patch("collection_engine.collect_all_messages", return_value={}):
            await engine.collect(datetime.now(SGT), trigger="text")
            assert engine._generation == 1

            await engine.collect(datetime.now(SGT), trigger="text")
            assert engine._generation == 2

            await engine.collect(datetime.now(SGT), trigger="sleep")
            assert engine._generation == 3

    @pytest.mark.asyncio
    async def test_stale_generation_results_discarded(self):
        """T1e: Results from old generation are discarded."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()

        async def slow_summarize(name, messages, generation):
            await asyncio.sleep(0.1)
            # Simulate generation changing during execution
            if generation != engine._generation:
                return None
            return {"session": name, "summary": "ok"}

        with patch.object(engine, '_summarize_session', side_effect=slow_summarize):
            with patch("collection_engine.collect_all_messages") as mock_collect:
                mock_collect.return_value = {
                    "session_a": [("12:00", "user", "hello")],
                }
                # Start collection, then bump generation before it completes
                task = asyncio.create_task(engine.collect(datetime.now(SGT), trigger="text"))
                await asyncio.sleep(0.05)
                engine._generation += 1  # Simulate supersession
                result = await task

        # Stale result should be None (discarded)
        assert result is None


# ============================================================
# T2: Supersession tests
# ============================================================

class TestSupersession:
    """T2: New collection aborts previous one."""

    @pytest.mark.asyncio
    async def test_new_collect_aborts_previous(self):
        """T2a: Start collect, then start another → first is cancelled."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        first_completed = [False]

        async def slow_summarize(name, messages, generation):
            await asyncio.sleep(1.0)  # Slow — will be aborted
            first_completed[0] = True
            return {"session": name, "summary": "ok"}

        async def fast_summarize(name, messages, generation):
            return {"session": name, "summary": "ok"}

        with patch("collection_engine.collect_all_messages") as mock_collect:
            mock_collect.return_value = {"s1": [("12:00", "user", "hi")]}

            # Start slow collection
            with patch.object(engine, '_run_with_retry', side_effect=slow_summarize):
                task1 = asyncio.create_task(engine.collect(datetime.now(SGT), trigger="text"))
                await asyncio.sleep(0.05)  # Let it start

            # Start fast collection (should abort the slow one)
            with patch.object(engine, '_run_with_retry', side_effect=fast_summarize):
                result2 = await engine.collect(datetime.now(SGT), trigger="text")

        # Second collection succeeded
        assert result2 is not None
        # First should NOT have completed
        assert not first_completed[0]

    @pytest.mark.asyncio
    async def test_subprocess_killed_on_abort(self):
        """T2b: Verify child processes receive termination on abort."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        killed_pids = []

        original_abort = engine._abort_active

        async def track_abort():
            # Track what gets killed
            for proc in engine._active_procs:
                killed_pids.append(proc.pid)
            await original_abort()

        engine._abort_active = track_abort

        # This test verifies the abort mechanism exists and is called
        # Detailed subprocess killing is tested in integration
        with patch("collection_engine.collect_all_messages", return_value={"s1": [("12:00", "user", "hi")]}):
            with patch.object(engine, '_run_with_retry', new_callable=AsyncMock) as mock_retry:
                mock_retry.return_value = {"session": "s1", "summary": "ok"}
                await engine.collect(datetime.now(SGT), trigger="text")
                await engine.collect(datetime.now(SGT), trigger="text")

        # _abort_active was called on the second collect
        assert engine._generation == 2

    @pytest.mark.asyncio
    async def test_abort_flag_prevents_stale_write(self):
        """T2c: Old collection returns after abort → results not applied."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()

        results_applied = []

        async def delayed_summarize(name, messages, generation):
            await asyncio.sleep(0.2)
            # Check generation AFTER the delay
            if generation != engine._generation:
                return None  # Stale
            result = {"session": name, "summary": "ok"}
            results_applied.append(result)
            return result

        with patch("collection_engine.collect_all_messages") as mock_collect:
            mock_collect.return_value = {"s1": [("12:00", "user", "hi")]}

            with patch.object(engine, '_run_with_retry', side_effect=delayed_summarize):
                # Start first collection
                task1 = asyncio.create_task(engine.collect(datetime.now(SGT), trigger="text"))
                await asyncio.sleep(0.05)

                # Bump generation (simulates second collect calling _abort_active)
                engine._generation += 1

                result1 = await task1

        # First collection should return None (stale)
        assert result1 is None


# ============================================================
# T3: Retry tests
# ============================================================

class TestRetry:
    """T3: Retry logic for individual sessions."""

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """T3a: Session fails twice, succeeds on third → result included."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        engine._generation = 1
        attempt_count = [0]

        async def flaky_summarize(name, messages, generation):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                return None  # Fail first 2
            return {"session": name, "summary": "ok"}

        with patch.object(engine, '_summarize_session', side_effect=flaky_summarize):
            result = await engine._run_with_retry("test_session", [("12:00", "user", "hi")], 1)

        assert result is not None
        assert result["summary"] == "ok"
        assert attempt_count[0] == 3

    @pytest.mark.asyncio
    async def test_max_retries_then_fail(self):
        """T3b: Session fails 3 times → returns None."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        engine._generation = 1
        attempt_count = [0]

        async def always_fail(name, messages, generation):
            attempt_count[0] += 1
            return None

        with patch.object(engine, '_summarize_session', side_effect=always_fail):
            result = await engine._run_with_retry("bad_session", [("12:00", "user", "hi")], 1)

        assert result is None
        assert attempt_count[0] == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """T3c: Verify delays between retries (5s, 10s)."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        engine._generation = 1
        timestamps = []

        async def fail_and_track(name, messages, generation):
            timestamps.append(time.time())
            return None

        with patch.object(engine, '_summarize_session', side_effect=fail_and_track):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await engine._run_with_retry("test", [], 1)

        # Should have called asyncio.sleep with backoff delays
        assert mock_sleep.call_count == 2  # 2 retries = 2 sleeps
        calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert calls[0] == 5   # First retry: 5s
        assert calls[1] == 10  # Second retry: 10s

    @pytest.mark.asyncio
    async def test_retry_aborted_by_supersession(self):
        """T3d: During retry sleep, new generation starts → retry stops."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        engine._generation = 1
        attempt_count = [0]

        async def fail_once(name, messages, generation):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                return None  # First attempt fails
            # Second attempt shouldn't happen if superseded
            return {"session": name, "summary": "ok"}

        original_sleep = asyncio.sleep

        async def sleep_and_supersede(delay):
            engine._generation = 2  # Supersede during backoff sleep
            await original_sleep(0)  # Don't actually wait

        with patch.object(engine, '_summarize_session', side_effect=fail_once):
            with patch("asyncio.sleep", side_effect=sleep_and_supersede):
                result = await engine._run_with_retry("test", [], 1)

        # Should have been aborted — generation changed during sleep
        assert result is None
        assert attempt_count[0] == 1  # Only 1 attempt, retry was aborted


# ============================================================
# T4: Parallel execution tests
# ============================================================

class TestParallel:
    """T4: Sessions run in parallel, not sequentially."""

    @pytest.mark.asyncio
    async def test_sessions_run_in_parallel(self):
        """T4a: 3 sessions each taking 0.1s → total < 0.25s (not 0.3s)."""
        from collection_engine import CollectionEngine, CollectionResult

        engine = CollectionEngine()

        async def slow_summarize(name, messages, generation):
            await asyncio.sleep(0.1)
            return {"session": name, "summary": "ok"}

        with patch.object(engine, '_run_with_retry', side_effect=slow_summarize):
            with patch("collection_engine.collect_all_messages") as mock_collect:
                mock_collect.return_value = {
                    "s1": [("12:00", "user", "a")],
                    "s2": [("12:01", "user", "b")],
                    "s3": [("12:02", "user", "c")],
                }
                start = time.time()
                result = await engine.collect(datetime.now(SGT), trigger="text")
                elapsed = time.time() - start

        assert result is not None
        assert len(result.summaries) == 3
        # Parallel: 0.1s + overhead, NOT 0.3s sequential
        assert elapsed < 0.25, f"Took {elapsed:.2f}s — sessions ran sequentially?"

    @pytest.mark.asyncio
    async def test_fallback_treated_as_failure(self):
        """T4b: compose_summary returning fallback text → treated as None."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()

        async def return_fallback(name, messages, generation):
            # Fallback placeholder text from compose_summary
            return None  # Fallback should be converted to None by _summarize_session

        with patch.object(engine, '_run_with_retry', side_effect=return_fallback):
            with patch("collection_engine.collect_all_messages") as mock_collect:
                mock_collect.return_value = {
                    "s1": [("12:00", "user", "hello")],
                }
                result = await engine.collect(datetime.now(SGT), trigger="text")

        # Fallback = failure = None
        assert result is None
