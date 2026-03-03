"""
Tests for derived session ID architecture — DIGEST-010.

Written BEFORE refactoring (TDD). Tests import `derive_session_id` which
doesn't exist yet as a standalone function (T4).

Test groups:
  T1: Sanitization (6 tests)
  T2: Uniqueness (3 tests)
  T3: Integration — session ID passed to subprocess (2 tests)
  T6: Parallel lock-freedom (1 test)

Total: 12 unit/integration tests.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, call

import pytest

SGT = timezone(timedelta(hours=8))


# ============================================================
# T1: Sanitization tests
# ============================================================

class TestSanitization:
    """T1: Session ID derivation from source session names."""

    def test_simple_name_sanitized(self):
        """T1a: 'CLAW 003' → 'digest-summary-claw-003'."""
        from collection_engine import derive_session_id
        assert derive_session_id("CLAW 003") == "digest-summary-claw-003"

    def test_spaces_to_hyphens(self):
        """T1b: 'Direct with Boyang' → 'digest-summary-direct-with-boyang'."""
        from collection_engine import derive_session_id
        assert derive_session_id("Direct with Boyang") == "digest-summary-direct-with-boyang"

    def test_special_chars_stripped(self):
        """T1c: Colons, dots, etc. become hyphens."""
        from collection_engine import derive_session_id
        result = derive_session_id("agent:main:subagent:5c16b1cc-6bdf")
        assert result == "digest-summary-agent-main-subagent-5c16b1cc-6bdf"

    def test_max_length_40(self):
        """T1d: Very long session name — safe_name portion truncated to 40 chars."""
        from collection_engine import derive_session_id
        long_name = "a" * 100
        result = derive_session_id(long_name)
        # "digest-summary-" is 15 chars, safe_name is max 40
        safe_part = result[len("digest-summary-"):]
        assert len(safe_part) <= 40

    def test_empty_name_handled(self):
        """T1e: Empty string doesn't crash, returns valid session ID."""
        from collection_engine import derive_session_id
        result = derive_session_id("")
        assert result.startswith("digest-summary")
        assert isinstance(result, str)

    def test_unicode_name(self):
        """T1f: Non-ASCII characters handled gracefully."""
        from collection_engine import derive_session_id
        result = derive_session_id("对话 with 日本語")
        assert isinstance(result, str)
        assert result.startswith("digest-summary-")
        # Should not contain unicode — only alphanumeric + hyphens
        import re
        safe_part = result[len("digest-summary-"):]
        assert re.match(r'^[a-z0-9-]*$', safe_part), f"Unsafe chars in: {safe_part}"


# ============================================================
# T2: Uniqueness tests
# ============================================================

class TestUniqueness:
    """T2: Different sessions → different IDs; same session → same ID."""

    def test_different_sessions_get_different_ids(self):
        """T2a: 3 different source names → 3 different session IDs."""
        from collection_engine import derive_session_id
        ids = {
            derive_session_id("CLAW 003"),
            derive_session_id("Direct with Boyang"),
            derive_session_id("agent:main:subagent:abc123"),
        }
        assert len(ids) == 3, "Expected 3 unique IDs, got %d" % len(ids)

    def test_same_session_gets_same_id(self):
        """T2b: Same source name called twice → identical session ID (deterministic)."""
        from collection_engine import derive_session_id
        id1 = derive_session_id("Direct with Boyang")
        id2 = derive_session_id("Direct with Boyang")
        assert id1 == id2

    def test_no_collision_similar_names(self):
        """T2c: Similar but different names produce different IDs."""
        from collection_engine import derive_session_id
        id1 = derive_session_id("CLAW 003")
        id2 = derive_session_id("CLAW 004")
        assert id1 != id2


# ============================================================
# T3: Integration — session ID passed to subprocess
# ============================================================

class TestSessionIdPassing:
    """T3: Verify session ID reaches the subprocess call."""

    @pytest.mark.asyncio
    async def test_session_id_passed_to_async_compose(self):
        """T3a: async_compose_summary receives and uses the session_id arg."""
        from llm import async_compose_summary

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(
                b'{"payloads":[{"text":"Summary text"}]}',
                b""
            ))
            mock_exec.return_value = mock_proc

            with patch("llm._save_conversations_to_file", return_value="/tmp/test.txt"):
                await async_compose_summary("test text", session_id="digest-summary-claw-003")

            # Verify --session-id arg was passed correctly
            call_args = mock_exec.call_args
            args_list = list(call_args[0]) if call_args[0] else list(call_args.args)
            sid_idx = args_list.index("--session-id")
            assert args_list[sid_idx + 1] == "digest-summary-claw-003"

    @pytest.mark.asyncio
    async def test_parallel_calls_use_different_session_ids(self):
        """T3b: 3 parallel _summarize_session calls → 3 different --session-id values."""
        from collection_engine import CollectionEngine

        engine = CollectionEngine()
        engine._generation = 1
        captured_sids = []

        original_compose = None

        async def capture_sid(text, session_id="digest-bot"):
            captured_sids.append(session_id)
            return "Summary for test"

        with patch("collection_engine.async_compose_summary", side_effect=capture_sid):
            with patch("collection_engine.format_messages", return_value="formatted"):
                tasks = [
                    engine._summarize_session("CLAW 003", [{"text": "a"}], 1),
                    engine._summarize_session("Direct with Boyang", [{"text": "b"}], 1),
                    engine._summarize_session("CLAW 008", [{"text": "c"}], 1),
                ]
                await asyncio.gather(*tasks)

        assert len(captured_sids) == 3
        assert len(set(captured_sids)) == 3, "Expected 3 unique session IDs, got: %s" % captured_sids
        assert all(sid.startswith("digest-summary-") for sid in captured_sids)
        assert "digest-bot" not in captured_sids  # Must NOT use the default


# ============================================================
# T6: Parallel lock-freedom
# ============================================================

class TestParallelLockFreedom:
    """T6: Full collect() with 3 sessions uses 3 distinct session IDs."""

    @pytest.mark.asyncio
    async def test_collect_uses_distinct_session_ids(self):
        """T6a: Spy on async_compose_summary during collect() — all IDs unique."""
        from collection_engine import CollectionEngine, CollectionResult

        engine = CollectionEngine()
        captured_sids = []

        async def spy_compose(text, session_id="digest-bot"):
            captured_sids.append(session_id)
            return "Summary text"

        with patch("collection_engine.async_compose_summary", side_effect=spy_compose):
            with patch("collection_engine.format_messages", return_value="formatted"):
                with patch("collection_engine.collect_all_messages") as mock_collect:
                    mock_collect.return_value = {
                        "CLAW 003": [{"text": "a"}],
                        "Direct with Boyang": [{"text": "b"}],
                        "CLAW 008": [{"text": "c"}],
                    }
                    result = await engine.collect(datetime.now(SGT), trigger="text")

        # All sessions should have been called with unique derived session IDs
        assert len(captured_sids) == 3
        assert len(set(captured_sids)) == 3
        assert all(sid.startswith("digest-summary-") for sid in captured_sids)
        assert "digest-bot" not in captured_sids

        # And the collection should have succeeded
        assert result is not None
        assert len(result.summaries) == 3
