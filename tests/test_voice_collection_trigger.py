"""
Failing tests: Voice messages should trigger collection, same as text messages.

BUG: handle_voice saves audio + transcribes + appends recap, but does NOT
trigger _engine.collect() to fetch new OpenClaw conversations.
handle_text DOES trigger collection after appending recap.

This means voice messages don't update Doudou's Summary with new conversations,
while text messages do. There's no reason for this asymmetry.

Evidence from production (2026-03-05):
  17:39 — Voice message received, saved, transcribed. No collection triggered.
  17:55 — Text message received, recorded. Collection Gen 3 triggered (0 msgs).

These tests MUST FAIL until the bug is fixed.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SGT


def _make_voice_update(user_id=411364623):
    """Build a mock Telegram Update with a voice message from Boyang."""

    async def _fake_download(path):
        """Create a dummy audio file so the handler doesn't crash on stat()."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x00" * 5000)

    mock_file = AsyncMock()
    mock_file.download_to_drive = AsyncMock(side_effect=_fake_download)

    mock_voice = MagicMock()
    mock_voice.file_id = "test_voice_file_id"
    mock_voice.duration = 14

    mock_message = AsyncMock()
    mock_message.voice = mock_voice
    mock_message.audio = None
    mock_message.reply_text = AsyncMock()
    mock_message.from_user = MagicMock()
    mock_message.from_user.id = user_id
    mock_message.from_user.username = "b0yan913"
    mock_message.from_user.first_name = "Boyang"

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.bot = AsyncMock()
    mock_context.bot.get_file = AsyncMock(return_value=mock_file)

    return mock_update, mock_context


def _make_text_update(text="Hello", user_id=411364623):
    """Build a mock Telegram Update with a text message from Boyang."""
    mock_message = AsyncMock()
    mock_message.text = text
    mock_message.reply_text = AsyncMock()
    mock_message.from_user = MagicMock()
    mock_message.from_user.id = user_id
    mock_message.from_user.username = "b0yan913"
    mock_message.from_user.first_name = "Boyang"

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_message.from_user

    mock_context = MagicMock()
    return mock_update, mock_context


@pytest.fixture
def active_digest(digest_dir):
    """Create an active digest so handlers proceed past the has_active_file check."""
    import recorder
    recorder._active_file = None
    with patch.object(recorder, "DIGEST_DIR", digest_dir):
        now = datetime.now(SGT)
        recorder.create_digest(
            coverage_from=now - timedelta(hours=2),
            coverage_to=now,
            session_summaries=[{"session": "CLAW 008", "messages": 10, "summary": "Test session."}],
        )
    yield
    recorder._active_file = None


class TestVoiceTriggersCollection:
    """Voice messages must trigger collection, same as text messages."""

    @pytest.mark.asyncio
    async def test_text_triggers_collection(self, active_digest):
        """CONTROL: Verify text handler triggers collection (this should pass)."""
        import main

        mock_update, mock_context = _make_text_update("My evening thoughts")

        mock_result = MagicMock()
        mock_result.total = 0

        with patch.object(main, "_engine") as mock_engine, \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock):
            mock_engine.collect = AsyncMock(return_value=mock_result)
            await main.handle_text(mock_update, mock_context)

            # Text handler DOES trigger collection
            mock_engine.collect.assert_called_once()
            call_kwargs = mock_engine.collect.call_args
            assert call_kwargs is not None, "Collection was not triggered by text"

    @pytest.mark.asyncio
    async def test_voice_triggers_collection(self, active_digest):
        """BUG: Voice handler does NOT trigger collection. This test MUST FAIL."""
        import main

        mock_update, mock_context = _make_voice_update()

        mock_result = MagicMock()
        mock_result.total = 0

        with patch.object(main, "_engine") as mock_engine, \
             patch.object(main, "ATTACHMENTS_DIR", Path("/tmp/digest-bot-test-attach")), \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock), \
             patch("main.transcribe", return_value="Gym workout transcript"):
            mock_engine.collect = AsyncMock(return_value=mock_result)

            Path("/tmp/digest-bot-test-attach").mkdir(parents=True, exist_ok=True)
            await main.handle_voice(mock_update, mock_context)

            # Voice handler MUST trigger collection — same as text
            mock_engine.collect.assert_called_once()

    @pytest.mark.asyncio
    async def test_voice_collection_uses_coverage_to(self, active_digest):
        """BUG: Voice collection should use coverage_to as the since timestamp."""
        import main
        from recorder import get_active_status

        mock_update, mock_context = _make_voice_update()

        mock_result = MagicMock()
        mock_result.total = 5
        mock_result.coverage_to = datetime.now(SGT)
        mock_result.summaries = [{"session": "CLAW 008", "messages": 5, "summary": "New stuff"}]

        status = get_active_status()
        expected_since = datetime.fromisoformat(str(status["coverage_to"]))

        with patch.object(main, "_engine") as mock_engine, \
             patch.object(main, "ATTACHMENTS_DIR", Path("/tmp/digest-bot-test-attach")), \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock), \
             patch("main.transcribe", return_value="Gym workout"), \
             patch("main.update_digest") as mock_update_digest:
            mock_engine.collect = AsyncMock(return_value=mock_result)

            Path("/tmp/digest-bot-test-attach").mkdir(parents=True, exist_ok=True)
            await main.handle_voice(mock_update, mock_context)

            # Should collect from the last coverage_to
            mock_engine.collect.assert_called_once()
            call_args = mock_engine.collect.call_args[0]
            assert call_args[0] == expected_since, \
                "Expected since=%s, got %s" % (expected_since, call_args[0])

    @pytest.mark.asyncio
    async def test_voice_collection_updates_digest_on_new_messages(self, active_digest):
        """BUG: When voice triggers collection and finds messages, digest should update."""
        import main

        mock_update, mock_context = _make_voice_update()

        new_coverage = datetime.now(SGT) + timedelta(minutes=30)
        mock_result = MagicMock()
        mock_result.total = 12
        mock_result.coverage_to = new_coverage
        mock_result.summaries = [
            {"session": "CLAW 008", "messages": 12, "summary": "Discussed voice features."}
        ]

        with patch.object(main, "_engine") as mock_engine, \
             patch.object(main, "ATTACHMENTS_DIR", Path("/tmp/digest-bot-test-attach")), \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock) as mock_send, \
             patch("main.transcribe", return_value="Voice transcript"), \
             patch("main.update_digest") as mock_update_digest:
            mock_engine.collect = AsyncMock(return_value=mock_result)

            Path("/tmp/digest-bot-test-attach").mkdir(parents=True, exist_ok=True)
            await main.handle_voice(mock_update, mock_context)

            # Should update digest with new summaries
            mock_update_digest.assert_called_once()

            # Should send status message to Boyang about new messages
            assert mock_send.called, "Should notify Boyang about new messages"
            sent_text = mock_send.call_args[0][0]
            assert "12" in sent_text or "+12" in sent_text, \
                "Status message should mention the new message count"

    @pytest.mark.asyncio
    async def test_voice_collection_silent_on_zero_messages(self, active_digest):
        """BUG: When voice triggers collection and finds 0 messages, report it."""
        import main

        mock_update, mock_context = _make_voice_update()

        mock_result = MagicMock()
        mock_result.total = 0

        with patch.object(main, "_engine") as mock_engine, \
             patch.object(main, "ATTACHMENTS_DIR", Path("/tmp/digest-bot-test-attach")), \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock) as mock_send, \
             patch("main.transcribe", return_value="Voice transcript"):
            mock_engine.collect = AsyncMock(return_value=mock_result)

            Path("/tmp/digest-bot-test-attach").mkdir(parents=True, exist_ok=True)
            await main.handle_voice(mock_update, mock_context)

            # Should tell Boyang there are 0 new messages (same as text handler)
            assert mock_send.called, "Should send 0-message notification"
            sent_text = mock_send.call_args[0][0]
            assert "0" in sent_text, "Should mention 0 new messages"


class TestSymmetryTextVsVoice:
    """Text and voice handlers must have symmetric behavior for collection."""

    @pytest.mark.asyncio
    async def test_both_handlers_call_collect(self, active_digest):
        """Both handle_text and handle_voice must call _engine.collect()."""
        import main

        # --- Text ---
        text_update, text_ctx = _make_text_update("Text message")
        text_result = MagicMock()
        text_result.total = 0

        with patch.object(main, "_engine") as text_engine, \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock):
            text_engine.collect = AsyncMock(return_value=text_result)
            await main.handle_text(text_update, text_ctx)
            text_collected = text_engine.collect.called

        # --- Voice ---
        voice_update, voice_ctx = _make_voice_update()
        voice_result = MagicMock()
        voice_result.total = 0

        with patch.object(main, "_engine") as voice_engine, \
             patch.object(main, "ATTACHMENTS_DIR", Path("/tmp/digest-bot-test-attach")), \
             patch.object(main, "_send_to_boyang", new_callable=AsyncMock), \
             patch("main.transcribe", return_value="Transcript"):
            voice_engine.collect = AsyncMock(return_value=voice_result)
            Path("/tmp/digest-bot-test-attach").mkdir(parents=True, exist_ok=True)
            await main.handle_voice(voice_update, voice_ctx)
            voice_collected = voice_engine.collect.called

        # Both must trigger collection
        assert text_collected, "Text handler should trigger collection"
        assert voice_collected, "Voice handler should trigger collection (BUG: it doesn't)"
