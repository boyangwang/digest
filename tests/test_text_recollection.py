"""
Tests for text reply re-collection (Priority 1 bug).

Covers:
1. Re-collection MUST run after text reply (not silent no-op)
2. Re-collection MUST send a status message to Boyang
3. Coverage_to MUST advance after re-collection
4. If re-collection fails, MUST report failure (never silent)
5. Integration: full handle_text flow with real collector data
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SGT = timezone(timedelta(hours=8))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mock_sessions(tmp_path):
    """Create realistic session data that the collector will find."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    # sessions.json with one active session
    sessions_json = sessions_dir / "sessions.json"
    sessions_json.write_text(json.dumps({
        "agent:main:telegram:group:-5125187430": {
            "sessionId": "test-claw003",
            "label": "CLAW 003"
        }
    }))

    # JSONL transcript with 15 messages spread over 2 hours
    transcript = sessions_dir / "test-claw003.jsonl"
    base_time = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)  # 20:00 SGT
    lines = []
    for i in range(15):
        ts = base_time + timedelta(minutes=i * 8)
        role = "user" if i % 2 == 0 else "assistant"
        text = "Message %d from %s" % (i, role)
        lines.append(json.dumps({
            "type": "message",
            "timestamp": ts.isoformat(),
            "message": {"role": role, "content": text}
        }))
    transcript.write_text("\n".join(lines))

    return sessions_dir, sessions_json


@pytest.fixture
def active_digest(tmp_path):
    """Create an active digest file with known coverage_to."""
    digest_dir = tmp_path / "Doudou-Digest"
    digest_dir.mkdir()

    filepath = digest_dir / "2026-03-01-1930.md"
    filepath.write_text(
        '---\n'
        'generated_at: "2026-03-01T19:30:00+08:00"\n'
        'coverage_from: "2026-02-28T19:27:00+08:00"\n'
        'coverage_to: "2026-03-01T19:30:00+08:00"\n'
        'status: "active"\n'
        '---\n\n'
        "# Doudou's Summary\n\n"
        "Session: CLAW 003\nMessages: 10\nSummary:\nPrevious summary.\n\n"
        "# Boyang's Recap\n\n"
    )
    return digest_dir, filepath


# ============================================================
# Test: Re-collection MUST run and find messages
# ============================================================

class TestRecollectionRuns:
    """The re-collection code in handle_text must actually execute."""

    def test_collect_finds_messages_after_coverage_to(self, mock_sessions):
        """Given messages after coverage_to, collector MUST find them."""
        sessions_dir, sessions_json = mock_sessions

        import config
        orig_sd, orig_sj = config.SESSION_DIR, config.SESSIONS_JSON
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json

        try:
            from collector import collect_all_messages
            # coverage_to was 19:30 SGT. Messages start at 20:00 SGT.
            since_ts = datetime(2026, 3, 1, 19, 30, 0, tzinfo=SGT)
            prev_night, today = collect_all_messages(since_ts)
            total = len(prev_night) + len(today)
            assert total > 0, (
                "Collector found 0 messages after coverage_to. "
                "This is the root cause of the silent failure bug."
            )
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj

    def test_build_session_summaries_returns_nonzero(self, mock_sessions):
        """_build_session_summaries must return messages when they exist."""
        sessions_dir, sessions_json = mock_sessions

        import config
        orig_sd, orig_sj = config.SESSION_DIR, config.SESSIONS_JSON
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json

        try:
            from main import _build_session_summaries
            since_ts = datetime(2026, 3, 1, 19, 30, 0, tzinfo=SGT)

            with patch("main.compose_summary", return_value="Test summary"):
                summaries, total = _build_session_summaries(since_ts)
                assert total > 0, (
                    "_build_session_summaries returned 0. Messages exist but aren't found."
                )
                assert len(summaries) > 0, "No session summaries generated"
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj


# ============================================================
# Test: Status message MUST be sent after re-collection
# ============================================================

class TestRecollectionStatusMessage:
    """After text reply, bot MUST send a second message with collection status + summary."""

    @pytest.mark.asyncio
    async def test_text_reply_sends_collection_status(self, mock_sessions, active_digest):
        """handle_text must send a follow-up message with collection results."""
        sessions_dir, sessions_json = mock_sessions
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod

        orig_sd = config.SESSION_DIR
        orig_sj = config.SESSIONS_JSON
        orig_dd = config.DIGEST_DIR
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json
        config.DIGEST_DIR = digest_dir
        main_mod.ATTACHMENTS_DIR = digest_dir / "attachments"

        recorder._active_file = filepath

        try:
            mock_message = AsyncMock()
            mock_message.text = "Test recap message from Boyang"
            mock_message.reply_text = AsyncMock()

            mock_update = MagicMock()
            mock_update.message = mock_message

            mock_context = MagicMock()

            # Mock the bot for _send_to_boyang
            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            with patch("main.compose_summary", return_value="Test summary text"):
                await main_mod.handle_text(mock_update, mock_context)

            # Must have called reply_text at least twice:
            # 1. ✍️ acknowledgment
            # 2. Collection status with summary (via _send_to_boyang)
            all_calls = mock_message.reply_text.call_args_list
            bot_calls = mock_bot.send_message.call_args_list

            total_messages = len(all_calls) + len(bot_calls)
            assert total_messages >= 2, (
                "Expected at least 2 messages (✍️ + status+summary), got %d. "
                "reply_text calls: %d, send_message calls: %d"
                % (total_messages, len(all_calls), len(bot_calls))
            )
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None

    @pytest.mark.asyncio
    async def test_status_message_contains_count_and_timestamps(
        self, mock_sessions, active_digest
    ):
        """The collection status message must include message count and time range."""
        sessions_dir, sessions_json = mock_sessions
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod

        orig_sd = config.SESSION_DIR
        orig_sj = config.SESSIONS_JSON
        orig_dd = config.DIGEST_DIR
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json
        config.DIGEST_DIR = digest_dir

        recorder._active_file = filepath

        try:
            mock_message = AsyncMock()
            mock_message.text = "Another test recap"
            mock_message.reply_text = AsyncMock()

            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_context = MagicMock()

            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            with patch("main.compose_summary", return_value="Summary text"):
                await main_mod.handle_text(mock_update, mock_context)

            # Collect all text sent to Boyang
            all_texts = []
            for call in mock_message.reply_text.call_args_list:
                all_texts.append(str(call))
            for call in mock_bot.send_message.call_args_list:
                all_texts.append(str(call))

            combined = " ".join(all_texts)

            # Must mention message count
            assert any(c.isdigit() for c in combined), (
                "Status message must contain a number (message count). Got: %s"
                % combined[:300]
            )
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None

    @pytest.mark.asyncio
    async def test_status_message_includes_summary(self, mock_sessions, active_digest):
        """The follow-up message MUST include the LLM-composed summary, not just counts."""
        sessions_dir, sessions_json = mock_sessions
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod

        orig_sd = config.SESSION_DIR
        orig_sj = config.SESSIONS_JSON
        orig_dd = config.DIGEST_DIR
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json
        config.DIGEST_DIR = digest_dir

        recorder._active_file = filepath

        try:
            mock_message = AsyncMock()
            mock_message.text = "Took Asher for a walk"
            mock_message.reply_text = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_context = MagicMock()

            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            summary_text = "Evening conversation about the voice feature and ElevenLabs setup"
            with patch("main.compose_summary", return_value=summary_text):
                await main_mod.handle_text(mock_update, mock_context)

            # Collect all text sent via _send_to_boyang
            bot_texts = []
            for call in mock_bot.send_message.call_args_list:
                args = call[1] if call[1] else {}
                text = args.get("text", "")
                if not text and call[0]:
                    text = str(call[0])
                bot_texts.append(text)

            combined = " ".join(bot_texts)

            assert summary_text in combined, (
                "Follow-up message must include the LLM summary. "
                "Boyang wants to SEE the summary in Telegram, not just counts. "
                "Got: %s" % combined[:500]
            )
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None


# ============================================================
# Test: Coverage_to MUST advance
# ============================================================

class TestCoverageAdvances:
    """After text reply with messages found, coverage_to must move forward."""

    @pytest.mark.asyncio
    async def test_coverage_to_advances_after_text(self, mock_sessions, active_digest):
        """coverage_to in the digest file must advance past original value."""
        sessions_dir, sessions_json = mock_sessions
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod
        import yaml

        orig_sd = config.SESSION_DIR
        orig_sj = config.SESSIONS_JSON
        orig_dd = config.DIGEST_DIR
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json
        config.DIGEST_DIR = digest_dir

        recorder._active_file = filepath

        # Read original coverage_to
        original_content = filepath.read_text()
        original_fm = yaml.safe_load(original_content.split("---")[1])
        original_coverage = original_fm["coverage_to"]

        try:
            mock_message = AsyncMock()
            mock_message.text = "Recap text"
            mock_message.reply_text = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_context = MagicMock()

            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            with patch("main.compose_summary", return_value="Summary"):
                await main_mod.handle_text(mock_update, mock_context)

            # Read updated file
            updated_content = filepath.read_text()
            updated_fm = yaml.safe_load(updated_content.split("---")[1])
            updated_coverage = updated_fm["coverage_to"]

            assert updated_coverage != original_coverage, (
                "coverage_to did NOT advance. Before: %s, After: %s. "
                "This is the bug — re-collection silently failed."
                % (original_coverage, updated_coverage)
            )
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None


# ============================================================
# Test: Failure MUST be reported (never silent)
# ============================================================

class TestNoSilentFailure:
    """If re-collection fails, the bot MUST tell Boyang."""

    @pytest.mark.asyncio
    async def test_collection_failure_sends_error_message(self, active_digest):
        """When collector crashes, bot must report the failure."""
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod

        orig_dd = config.DIGEST_DIR
        config.DIGEST_DIR = digest_dir
        recorder._active_file = filepath

        try:
            mock_message = AsyncMock()
            mock_message.text = "Recap text"
            mock_message.reply_text = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_context = MagicMock()

            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            # Make collection crash
            with patch("main._build_session_summaries", side_effect=Exception("DB error")):
                await main_mod.handle_text(mock_update, mock_context)

            # Must have sent an error notification
            all_calls = mock_message.reply_text.call_args_list
            bot_calls = mock_bot.send_message.call_args_list
            all_texts = []
            for call in all_calls + bot_calls:
                all_texts.append(str(call))
            combined = " ".join(all_texts).lower()

            assert "fail" in combined or "error" in combined or "❌" in combined, (
                "Collection failed but no error was reported to Boyang. "
                "Silent failure is unacceptable. Messages: %s" % combined[:500]
            )
        finally:
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None

    @pytest.mark.asyncio
    async def test_zero_messages_reports_no_new(self, active_digest):
        """When 0 messages found, bot should still confirm collection ran."""
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod

        orig_dd = config.DIGEST_DIR
        config.DIGEST_DIR = digest_dir
        recorder._active_file = filepath

        try:
            mock_message = AsyncMock()
            mock_message.text = "Recap text"
            mock_message.reply_text = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_context = MagicMock()

            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            with patch("main._build_session_summaries", return_value=([], 0)):
                await main_mod.handle_text(mock_update, mock_context)

            # Even with 0 messages, must confirm collection ran
            all_calls = mock_message.reply_text.call_args_list
            bot_calls = mock_bot.send_message.call_args_list
            total = len(all_calls) + len(bot_calls)

            assert total >= 2, (
                "With 0 new messages, bot should still send ✍️ + '0 new messages'. "
                "Got only %d messages total." % total
            )
        finally:
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None


# ============================================================
# Integration: reproduce the exact production failure
# ============================================================

class TestReproduceProductionBug:
    """Reproduce the exact scenario from 2026-03-01 20:16."""

    @pytest.mark.asyncio
    async def test_handle_text_with_real_collector(self, mock_sessions, active_digest):
        """Full handle_text with real collector (mocked LLM only).

        This test reproduces the exact production scenario:
        1. Active digest exists (created at 19:30, coverage_to=19:30)
        2. Messages exist in JSONL after 19:30
        3. Boyang sends a text at 20:16
        4. Re-collection should find messages and advance coverage

        If this test PASSES, the bug is in compose_summary/subprocess.
        If this test FAILS, the bug is in the collection/handler logic.
        """
        sessions_dir, sessions_json = mock_sessions
        digest_dir, filepath = active_digest

        import config
        import recorder
        import main as main_mod
        import yaml

        orig_sd = config.SESSION_DIR
        orig_sj = config.SESSIONS_JSON
        orig_dd = config.DIGEST_DIR
        config.SESSION_DIR = sessions_dir
        config.SESSIONS_JSON = sessions_json
        config.DIGEST_DIR = digest_dir

        recorder._active_file = filepath

        try:
            mock_message = AsyncMock()
            mock_message.text = "Taking Asher for a walk"
            mock_message.reply_text = AsyncMock()
            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_context = MagicMock()

            mock_bot = AsyncMock()
            main_mod._app = MagicMock()
            main_mod._app.bot = mock_bot

            with patch("main.compose_summary", return_value="Evening walk summary"):
                await main_mod.handle_text(mock_update, mock_context)

            # Verify: recap was appended
            content = filepath.read_text()
            assert "Taking Asher for a walk" in content, "Recap not appended"

            # Verify: coverage_to advanced
            fm = yaml.safe_load(content.split("---")[1])
            assert fm["coverage_to"] != "2026-03-01T19:30:00+08:00", (
                "coverage_to stuck at 19:30 — re-collection did not run! "
                "This reproduces the production bug."
            )

            # Verify: new summary appended
            assert "Evening walk summary" in content, (
                "Summary not appended to digest file"
            )

            # Verify: status message sent
            all_calls = mock_message.reply_text.call_args_list
            bot_calls = mock_bot.send_message.call_args_list
            total = len(all_calls) + len(bot_calls)
            assert total >= 2, (
                "No collection status message sent. Got %d messages." % total
            )
        finally:
            config.SESSION_DIR = orig_sd
            config.SESSIONS_JSON = orig_sj
            config.DIGEST_DIR = orig_dd
            recorder._active_file = None
            main_mod._app = None
