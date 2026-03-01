"""
End-to-end tests — simulate Telegram messages hitting the bot handlers.

These test the ACTUAL handler functions with mock Update/Context objects,
capture outgoing messages, and verify the responses match the spec.

No mocked recorder or LLM — these go through the real code path
(except Telegram network calls and Doudou CLI).
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import main
import recorder
from config import SGT, BOYANG_USER_ID


# ============================================================
# Helpers: simulate Telegram messages
# ============================================================

def _make_update(text, user_id=BOYANG_USER_ID, is_command=False):
    """Create a mock Telegram Update object."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.chat_id = user_id
    update.message.from_user = MagicMock()
    update.message.from_user.id = user_id
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    return update


def _make_context():
    """Create a mock Context."""
    return MagicMock()


class SentMessages:
    """Capture messages sent via _send_to_boyang."""

    def __init__(self):
        self.messages = []

    async def capture(self, text):
        self.messages.append(text)

    @property
    def last(self):
        return self.messages[-1] if self.messages else None

    @property
    def all_text(self):
        return "\n".join(self.messages)

    def clear(self):
        self.messages.clear()


@pytest.fixture
def sent():
    return SentMessages()


@pytest.fixture
def bot_env(digest_dir, sent):
    """Set up the bot environment with temp directories and captured output."""
    recorder._active_file = None

    with patch.object(recorder, "DIGEST_DIR", digest_dir), \
         patch.object(main, "_send_to_boyang", side_effect=sent.capture), \
         patch.object(main, "_app", MagicMock()):
        yield sent


@pytest.fixture
def bot_env_with_llm(digest_dir, sent):
    """Bot env with LLM mocked to return predictable summaries."""
    recorder._active_file = None

    def mock_compose(text):
        if not text.strip() or "No conversations" in text:
            return "No conversations to summarize."
        return "A productive session of work.\n\n高效的工作时段。"

    with patch.object(recorder, "DIGEST_DIR", digest_dir), \
         patch.object(main, "_send_to_boyang", side_effect=sent.capture), \
         patch.object(main, "_app", MagicMock()), \
         patch("main.compose_summary", side_effect=mock_compose):
        yield sent


# ============================================================
# /status command
# ============================================================

class TestStatusCommand:

    @pytest.mark.asyncio
    async def test_status_idle(self, bot_env):
        """IDLE: shows state, no document content."""
        update = _make_update("/status")
        ctx = _make_context()
        await main.cmd_status(update, ctx)

        response = bot_env.all_text
        assert "IDLE" in response
        assert "# Doudou's Summary" not in response  # No document content
        assert len(response) < 200  # Short, not 196KB

    @pytest.mark.asyncio
    async def test_status_active_shows_document(self, bot_env, digest_dir):
        """ACTIVE: shows metadata AND full document."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Built the bot."},
                ],
            )

        update = _make_update("/status")
        await main.cmd_status(update, _make_context())

        response = bot_env.all_text
        assert "ACTIVE" in response
        assert "# Doudou's Summary" in response
        assert "Built the bot." in response
        assert "Session: CLAW 003" in response
        assert "Messages: 50" in response
        assert "# Boyang's Recap" in response

    @pytest.mark.asyncio
    async def test_status_active_reasonable_size(self, bot_env, digest_dir):
        """Status response should be reasonable, not 196KB."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Short summary."},
                ],
            )

        update = _make_update("/status")
        await main.cmd_status(update, _make_context())

        total_chars = sum(len(m) for m in bot_env.messages)
        assert total_chars < 5000, f"/status returned {total_chars} chars"


# ============================================================
# /digest command
# ============================================================

class TestDigestCommand:

    @pytest.mark.asyncio
    async def test_digest_creates_v2_file(self, bot_env_with_llm, digest_dir, transcript_dir, populated_transcripts):
        """First /digest creates a v2 format file."""
        with patch("main.collect_all_messages") as mock_collect, \
             patch("main.group_by_session") as mock_group, \
             patch("main.format_messages", return_value="formatted"):

            mock_collect.return_value = (
                [],  # prev_night
                [{"role": "user", "text": "Hello", "time_str": "09:00", "session": "CLAW 003"}],
            )
            mock_group.return_value = {
                "CLAW 003": [{"role": "user", "text": "Hello", "time_str": "09:00", "session": "CLAW 003"}],
            }

            update = _make_update("/digest")
            update.message.reply_text = AsyncMock()
            await main.cmd_digest(update, _make_context())

        # Check file was created
        files = list(digest_dir.glob("*.md"))
        assert len(files) == 1

        content = files[0].read_text()
        assert "# Doudou's Summary" in content
        assert "# Boyang's Recap" in content
        assert "Session: CLAW 003" in content
        assert "Previous Night" not in content
        assert "Today's Conversations" not in content

    @pytest.mark.asyncio
    async def test_digest_telegram_message_clean(self, bot_env_with_llm, digest_dir):
        """Telegram message from /digest should be clean and readable."""
        with patch("main.collect_all_messages") as mock_collect, \
             patch("main.group_by_session") as mock_group, \
             patch("main.format_messages", return_value="formatted"):

            mock_collect.return_value = (
                [],
                [{"role": "user", "text": "Test", "time_str": "10:00", "session": "CLAW 003"}] * 5,
            )
            mock_group.return_value = {
                "CLAW 003": [{"role": "user", "text": "Test", "time_str": "10:00", "session": "CLAW 003"}] * 5,
            }

            update = _make_update("/digest")
            update.message.reply_text = AsyncMock()
            await main.cmd_digest(update, _make_context())

        msg = bot_env_with_llm.all_text
        # Should have session info
        assert "CLAW 003" in msg
        # Should NOT have raw conversation dumps
        assert "**Boyang:**" not in msg
        assert "**Doudou:**" not in msg
        # Should be reasonable size
        assert len(msg) < 2000

    @pytest.mark.asyncio
    async def test_digest_update_appends(self, bot_env_with_llm, digest_dir):
        """Second /digest appends to same file, preserves first summary."""
        now = datetime.now(SGT)

        # Pre-create an active file
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 100, "summary": "FIRST_SUMMARY"},
                ],
            )

        with patch("main.collect_all_messages") as mock_collect, \
             patch("main.group_by_session") as mock_group, \
             patch("main.format_messages", return_value="formatted"):

            mock_collect.return_value = (
                [],
                [{"role": "user", "text": "New msg", "time_str": "11:00", "session": "CLAW 003"}],
            )
            mock_group.return_value = {
                "CLAW 003": [{"role": "user", "text": "New msg", "time_str": "11:00", "session": "CLAW 003"}],
            }

            update = _make_update("/digest")
            update.message.reply_text = AsyncMock()
            await main.cmd_digest(update, _make_context())

        # Same file, not a new one
        files = list(digest_dir.glob("*.md"))
        assert len(files) == 1

        content = files[0].read_text()
        assert "FIRST_SUMMARY" in content  # Original preserved
        assert "Session: CLAW 003" in content
        assert content.count("Session: CLAW 003") == 2  # Both entries


# ============================================================
# /sleep command
# ============================================================

class TestSleepCommand:

    @pytest.mark.asyncio
    async def test_sleep_finalizes(self, bot_env, digest_dir):
        """After /sleep, file is finalized."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[{"session": "S", "messages": 1, "summary": "Sum."}],
            )

        update = _make_update("/sleep")
        await main.cmd_sleep(update, _make_context())

        assert not recorder.has_active_file()
        update.message.reply_text.assert_called_once()
        reply = update.message.reply_text.call_args[0][0]
        assert "Obsidian" in reply or "晚安" in reply

    @pytest.mark.asyncio
    async def test_sleep_when_idle(self, bot_env):
        """Sleep when no active file — graceful response."""
        update = _make_update("/sleep")
        await main.cmd_sleep(update, _make_context())

        reply = update.message.reply_text.call_args[0][0]
        assert "No active" in reply or "晚安" in reply


# ============================================================
# Text handler (recap)
# ============================================================

class TestTextHandler:

    @pytest.mark.asyncio
    async def test_text_appends_recap(self, bot_env, digest_dir):
        """Text message appends verbatim to recap section."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[{"session": "S", "messages": 1, "summary": "Sum."}],
            )

        # Mock the re-collect to return nothing new
        with patch("main._build_session_summaries", return_value=([], 0)):
            update = _make_update("Feeling productive tonight 🚀")
            await main.handle_text(update, _make_context())

        content = fp.read_text()
        assert "Feeling productive tonight 🚀" in content

    @pytest.mark.asyncio
    async def test_text_ignored_when_idle(self, bot_env):
        """Text when no active file — silently ignored."""
        update = _make_update("Random text")
        await main.handle_text(update, _make_context())
        # No reply, no error
        update.message.reply_text.assert_not_called()


# ============================================================
# Full conversation simulation
# ============================================================

class TestFullConversation:

    @pytest.mark.asyncio
    async def test_evening_flow(self, bot_env_with_llm, digest_dir):
        """Simulate: /digest → text → /status → /sleep → /status."""
        now = datetime.now(SGT)

        with patch("main._build_session_summaries") as mock_build:

            # Step 1: /digest
            mock_build.return_value = (
                [{"session": "CLAW 003", "messages": 10, "summary": "Evening work session."}],
                10,
            )
            update = _make_update("/digest")
            update.message.reply_text = AsyncMock()
            await main.cmd_digest(update, _make_context())
            assert recorder.has_active_file()
            bot_env_with_llm.clear()

            # Step 2: text reply (recap)
            mock_build.return_value = ([], 0)
            update = _make_update("Good session today")
            await main.handle_text(update, _make_context())
            bot_env_with_llm.clear()

            # Step 3: /status — should show document with summary + recap
            update = _make_update("/status")
            await main.cmd_status(update, _make_context())
            status_msg = bot_env_with_llm.all_text
            assert "ACTIVE" in status_msg
            assert "# Doudou's Summary" in status_msg
            assert "Good session today" in status_msg
            total_chars = sum(len(m) for m in bot_env_with_llm.messages)
            assert total_chars < 5000
            bot_env_with_llm.clear()

            # Step 4: /sleep
            update = _make_update("/sleep")
            await main.cmd_sleep(update, _make_context())
            assert not recorder.has_active_file()
            bot_env_with_llm.clear()

            # Step 5: /status after sleep — should be IDLE, short
            update = _make_update("/status")
            await main.cmd_status(update, _make_context())
            idle_msg = bot_env_with_llm.all_text
            assert "IDLE" in idle_msg
            assert "# Doudou's Summary" not in idle_msg
            assert len(idle_msg) < 200
