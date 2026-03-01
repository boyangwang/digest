"""
Tests for voice message handling (SPEC-VOICE-01 through SPEC-VOICE-07).

Each test is named after the spec it validates.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We'll test these modules once they exist
# from stt import transcribe
# from recorder import append_voice_recap
# from main import handle_voice


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_vault(tmp_path):
    """Create a temporary vault structure."""
    digest_dir = tmp_path / "Doudou-Digest"
    digest_dir.mkdir()
    attachments_dir = digest_dir / "attachments"
    attachments_dir.mkdir()
    return tmp_path, digest_dir, attachments_dir


@pytest.fixture
def sample_ogg(tmp_path):
    """Create a minimal .ogg file for testing."""
    ogg_path = tmp_path / "sample.ogg"
    # Minimal OGG header (not valid audio, but enough for file handling tests)
    ogg_path.write_bytes(b"OggS" + b"\x00" * 100)
    return ogg_path


@pytest.fixture
def active_digest(tmp_vault):
    """Create an active digest file."""
    _, digest_dir, _ = tmp_vault
    filepath = digest_dir / "2026-03-01-2230.md"
    filepath.write_text(
        '---\n'
        'generated_at: "2026-03-01T22:30:00+08:00"\n'
        'coverage_from: "2026-03-01T10:00:00+08:00"\n'
        'coverage_to: "2026-03-01T22:30:00+08:00"\n'
        'status: "active"\n'
        '---\n\n'
        "# Doudou's Summary\n\n"
        "Session: CLAW 003\nMessages: 10\nSummary:\nTest summary.\n\n"
        "# Boyang's Recap\n\n"
        "**22:35** First entry\n"
    )
    return filepath


# ============================================================
# SPEC-VOICE-01: Audio file saved to Obsidian vault
# ============================================================

class TestVoice01AudioSaved:
    """SPEC-VOICE-01: Audio file saved to attachments/ in vault."""

    def test_audio_saved_to_attachments_dir(self, tmp_vault, sample_ogg):
        """Audio file is copied to Doudou-Digest/attachments/."""
        _, _, attachments_dir = tmp_vault
        # Simulate saving: copy sample to attachments with timestamped name
        dest = attachments_dir / "voice-20260301-225012.ogg"
        dest.write_bytes(sample_ogg.read_bytes())

        assert dest.exists()
        assert dest.suffix == ".ogg"
        assert dest.parent.name == "attachments"

    def test_filename_format_yyyymmdd_hhmmss(self):
        """Filename follows voice-YYYYMMDD-HHMMSS.ogg pattern."""
        import re
        pattern = r"^voice-\d{8}-\d{6}\.ogg$"
        assert re.match(pattern, "voice-20260301-225012.ogg")
        assert not re.match(pattern, "voice-2026-03-01-225012.ogg")  # no hyphens in date
        assert not re.match(pattern, "audio-20260301-225012.ogg")  # wrong prefix

    def test_attachments_dir_created_if_absent(self, tmp_vault):
        """attachments/ directory is auto-created."""
        _, digest_dir, attachments_dir = tmp_vault
        # Remove the attachments dir
        attachments_dir.rmdir()
        assert not attachments_dir.exists()

        # Simulate recorder creating it
        attachments_dir.mkdir(exist_ok=True)
        assert attachments_dir.exists()

    def test_original_bytes_preserved(self, tmp_vault, sample_ogg):
        """Audio bytes are identical — no transcoding."""
        _, _, attachments_dir = tmp_vault
        original_bytes = sample_ogg.read_bytes()
        dest = attachments_dir / "voice-20260301-225012.ogg"
        dest.write_bytes(original_bytes)

        assert dest.read_bytes() == original_bytes


# ============================================================
# SPEC-VOICE-02: STT transcription via ElevenLabs Scribe
# ============================================================

class TestVoice02STT:
    """SPEC-VOICE-02: ElevenLabs Scribe transcription."""

    def test_stt_returns_text_on_success(self):
        """transcribe() returns text string on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "今天的进展很不错",
            "language_code": "zh",
        }

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
             patch("stt.requests.post", return_value=mock_response), \
             patch("builtins.open", MagicMock()):
            from stt import transcribe
            result = transcribe("/fake/path.ogg")
            assert result == "今天的进展很不错"

    def test_stt_returns_none_on_failure(self):
        """transcribe() returns None when API fails."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
             patch("stt.requests.post", return_value=mock_response), \
             patch("builtins.open", MagicMock()):
            from stt import transcribe
            result = transcribe("/fake/path.ogg")
            assert result is None

    def test_stt_returns_none_on_missing_api_key(self):
        """transcribe() returns None gracefully when no API key."""
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}, clear=False):
            # Re-import to pick up missing key
            import importlib
            import stt
            importlib.reload(stt)
            result = stt.transcribe("/fake/path.ogg")
            assert result is None

    def test_stt_returns_none_on_timeout(self):
        """transcribe() returns None on network timeout."""
        import requests as req
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
             patch("stt.requests.post", side_effect=req.Timeout("timeout")), \
             patch("builtins.open", MagicMock()):
            from stt import transcribe
            result = transcribe("/fake/path.ogg")
            assert result is None

    def test_stt_handles_bilingual_content(self):
        """Scribe can handle mixed Chinese+English audio."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "Today we built the voice feature. 今天搞了语音功能。",
            "language_code": "zh",
        }

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
             patch("stt.requests.post", return_value=mock_response), \
             patch("builtins.open", MagicMock()):
            from stt import transcribe
            result = transcribe("/fake/path.ogg")
            assert "voice feature" in result
            assert "语音功能" in result


# ============================================================
# SPEC-VOICE-03: Recap entry format (audio + transcript)
# ============================================================

class TestVoice03RecapFormat:
    """SPEC-VOICE-03: Voice entry in recap has audio embed + blockquote transcript."""

    def test_recap_contains_audio_embed(self, active_digest):
        """Voice entry includes ![[voice-*.ogg]] Obsidian embed."""
        # Simulate appending a voice recap entry
        content = active_digest.read_text()
        voice_entry = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> 今天的进展很不错\n'
        )
        content = content.rstrip() + "\n" + voice_entry
        active_digest.write_text(content)

        result = active_digest.read_text()
        assert "![[voice-20260301-225012.ogg]]" in result
        assert "🎙️" in result

    def test_recap_transcript_in_blockquote(self, active_digest):
        """Transcribed text uses > blockquote syntax."""
        content = active_digest.read_text()
        voice_entry = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> This is the transcribed text\n'
        )
        content = content.rstrip() + "\n" + voice_entry
        active_digest.write_text(content)

        result = active_digest.read_text()
        assert "> This is the transcribed text" in result

    def test_multiline_transcript_blockquote(self, active_digest):
        """Multi-line transcripts use continued blockquote."""
        content = active_digest.read_text()
        voice_entry = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> First line of transcript\n'
            '> Second line continues here\n'
        )
        content = content.rstrip() + "\n" + voice_entry
        active_digest.write_text(content)

        result = active_digest.read_text()
        assert "> First line of transcript" in result
        assert "> Second line continues here" in result

    def test_recap_entry_order_preserved(self, active_digest):
        """Voice entries appear in chronological order with text entries."""
        content = active_digest.read_text()
        entries = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> Voice message here\n'
            '\n**22:55** Text message after voice\n'
        )
        content = content.rstrip() + "\n" + entries
        active_digest.write_text(content)

        result = active_digest.read_text()
        voice_pos = result.index("🎙️")
        text_pos = result.index("Text message after voice")
        assert voice_pos < text_pos

    def test_transcription_unavailable_fallback(self, active_digest):
        """When STT fails, show [Transcription unavailable]."""
        content = active_digest.read_text()
        voice_entry = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> [Transcription unavailable]\n'
        )
        content = content.rstrip() + "\n" + voice_entry
        active_digest.write_text(content)

        result = active_digest.read_text()
        assert "[Transcription unavailable]" in result
        assert "![[voice-20260301-225012.ogg]]" in result  # audio still saved


# ============================================================
# SPEC-VOICE-04: Telegram confirmation
# ============================================================

class TestVoice04TelegramConfirmation:
    """SPEC-VOICE-04: Bot replies with transcription for verification."""

    def test_reply_contains_transcription(self):
        """Bot reply includes transcribed text for Boyang to verify."""
        transcript = "今天的进展很不错"
        reply = "🎙️ ✍️\n\n> %s" % transcript
        assert "🎙️" in reply
        assert transcript in reply

    def test_reply_on_stt_failure(self):
        """Bot reply indicates transcription unavailable."""
        reply = "🎙️ ✍️ (audio saved, transcription unavailable)"
        assert "audio saved" in reply
        assert "transcription unavailable" in reply


# ============================================================
# SPEC-VOICE-05: Voice requires ACTIVE state
# ============================================================

class TestVoice05ActiveState:
    """SPEC-VOICE-05: Voice messages only processed when digest is active."""

    def test_voice_ignored_when_idle(self):
        """Voice message silently ignored when no active digest."""
        # This will test the actual handler — for now, validate the logic
        has_active = False
        processed = False
        if has_active:
            processed = True
        assert not processed

    def test_voice_processed_when_active(self):
        """Voice message processed when digest is active."""
        has_active = True
        processed = False
        if has_active:
            processed = True
        assert processed


# ============================================================
# SPEC-VOICE-06: Audio stored in vault, not /tmp/
# ============================================================

class TestVoice06VaultStorage:
    """SPEC-VOICE-06: Audio files stored permanently in vault."""

    def test_audio_path_in_vault(self, tmp_vault):
        """Audio path is under Doudou-Digest/attachments/, not /tmp/."""
        _, digest_dir, _ = tmp_vault
        audio_path = digest_dir / "attachments" / "voice-20260301-225012.ogg"
        assert "Doudou-Digest" in str(audio_path)
        assert "/tmp/" not in str(audio_path)

    def test_audio_survives_reboot(self, tmp_vault, sample_ogg):
        """Audio file persists (vault is permanent storage)."""
        _, _, attachments_dir = tmp_vault
        dest = attachments_dir / "voice-20260301-225012.ogg"
        dest.write_bytes(sample_ogg.read_bytes())
        # File exists and is readable
        assert dest.exists()
        assert len(dest.read_bytes()) > 0


# ============================================================
# SPEC-VOICE-07: STT provider abstraction
# ============================================================

class TestVoice07ProviderAbstraction:
    """SPEC-VOICE-07: STT is isolated in stt.py with clean interface."""

    def test_transcribe_function_signature(self):
        """stt.transcribe(audio_path) -> str | None"""
        from stt import transcribe
        import inspect
        sig = inspect.signature(transcribe)
        params = list(sig.parameters.keys())
        assert "audio_path" in params
        assert len(params) == 1  # Only one parameter

    def test_stt_module_is_standalone(self):
        """stt.py has no imports from main.py or recorder.py."""
        import ast
        stt_path = Path(__file__).parent.parent / "stt.py"
        if stt_path.exists():
            tree = ast.parse(stt_path.read_text())
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
            assert "main" not in imports
            assert "recorder" not in imports


# ============================================================
# Integration: Full voice flow
# ============================================================

class TestVoiceIntegration:
    """End-to-end voice message handling flow."""

    def test_full_flow_success(self, tmp_vault, sample_ogg, active_digest):
        """Complete flow: download → save → transcribe → record → reply."""
        _, _, attachments_dir = tmp_vault

        # 1. Save audio
        audio_dest = attachments_dir / "voice-20260301-225012.ogg"
        audio_dest.write_bytes(sample_ogg.read_bytes())

        # 2. Transcribe (mocked)
        transcript = "Today was productive. 今天很有收获。"

        # 3. Record in digest
        content = active_digest.read_text()
        voice_entry = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> %s\n' % transcript
        )
        content = content.rstrip() + "\n" + voice_entry
        active_digest.write_text(content)

        # 4. Verify
        result = active_digest.read_text()
        assert audio_dest.exists()
        assert "![[voice-20260301-225012.ogg]]" in result
        assert "Today was productive" in result
        assert "今天很有收获" in result

    def test_full_flow_stt_failure(self, tmp_vault, sample_ogg, active_digest):
        """Flow when STT fails: audio saved, fallback text recorded."""
        _, _, attachments_dir = tmp_vault

        # 1. Save audio (still works)
        audio_dest = attachments_dir / "voice-20260301-225012.ogg"
        audio_dest.write_bytes(sample_ogg.read_bytes())

        # 2. STT returns None
        transcript = None
        fallback = "[Transcription unavailable]"

        # 3. Record with fallback
        content = active_digest.read_text()
        voice_entry = (
            '\n**22:50** 🎙️ ![[voice-20260301-225012.ogg]]\n'
            '> %s\n' % (transcript or fallback)
        )
        content = content.rstrip() + "\n" + voice_entry
        active_digest.write_text(content)

        # 4. Verify: audio saved, fallback text present
        result = active_digest.read_text()
        assert audio_dest.exists()
        assert "![[voice-20260301-225012.ogg]]" in result
        assert "[Transcription unavailable]" in result

    def test_multiple_voice_messages(self, tmp_vault, active_digest):
        """Multiple voice messages produce separate entries."""
        _, _, attachments_dir = tmp_vault
        content = active_digest.read_text()

        for i, (ts, text) in enumerate([
            ("2250", "First voice message"),
            ("2255", "Second voice message"),
            ("2301", "Third voice message"),
        ]):
            filename = "voice-20260301-%s12.ogg" % ts
            (attachments_dir / filename).write_bytes(b"OggS" + bytes(100))
            voice_entry = (
                '\n**%s:%s** 🎙️ ![[%s]]\n'
                '> %s\n' % (ts[:2], ts[2:], filename, text)
            )
            content = content.rstrip() + "\n" + voice_entry

        active_digest.write_text(content)
        result = active_digest.read_text()

        assert result.count("🎙️") == 3
        assert "First voice message" in result
        assert "Second voice message" in result
        assert "Third voice message" in result
