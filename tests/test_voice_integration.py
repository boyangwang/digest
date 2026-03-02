"""
Integration test: full voice handler chain against REAL APIs.

This test:
1. Creates a real speech audio file (OpenAI TTS)
2. Calls stt.transcribe() against real ElevenLabs Scribe v2
3. Calls recorder.append_voice_recap() with real data
4. Simulates handle_voice with mocked Telegram objects
5. Verifies: audio saved, transcribed, recorded in digest, reply sent

Requires: ELEVENLABS_API_KEY and OPENAI_API_KEY environment variables.
Skip with: pytest -m "not integration" (or ELEVENLABS_API_KEY unset)
"""

import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip if no API keys
ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
SKIP_REASON = "ELEVENLABS_API_KEY not set"


@pytest.fixture
def tmp_vault(tmp_path):
    """Temporary vault for integration tests."""
    digest_dir = tmp_path / "Doudou-Digest"
    digest_dir.mkdir()
    attachments_dir = digest_dir / "attachments"
    attachments_dir.mkdir()
    return tmp_path, digest_dir, attachments_dir


@pytest.fixture
def real_audio():
    """Generate a real speech audio file using OpenAI TTS."""
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        pytest.skip("OPENAI_API_KEY not set")

    audio_path = Path(tempfile.mktemp(suffix=".ogg"))
    result = subprocess.run(
        [
            "curl", "-s",
            "https://api.openai.com/v1/audio/speech",
            "-H", "Authorization: Bearer %s" % openai_key,
            "-H", "Content-Type: application/json",
            "-d", '{"model":"tts-1","input":"Hello, this is a test of the voice message feature. 这是语音消息功能的测试。","voice":"echo","response_format":"opus"}',
            "-o", str(audio_path),
        ],
        capture_output=True,
    )
    assert audio_path.exists() and audio_path.stat().st_size > 1000, "TTS failed"
    yield audio_path
    audio_path.unlink(missing_ok=True)


# ============================================================
# Real STT Integration
# ============================================================

class TestRealSTT:
    """Test stt.transcribe() against real ElevenLabs API."""

    @pytest.mark.skipif(not ELEVENLABS_KEY, reason=SKIP_REASON)
    def test_real_transcription(self, real_audio):
        """Transcribe real speech audio → text contains expected words."""
        from stt import transcribe
        result = transcribe(str(real_audio))
        assert result is not None, "Transcription returned None"
        assert len(result) > 10, "Transcription too short: %r" % result
        # Should contain some of the input words
        lower = result.lower()
        assert ("voice" in lower or "test" in lower or "语音" in result or "测试" in result), \
            "Transcription doesn't match input: %r" % result


# ============================================================
# Full Handler Integration
# ============================================================

class TestFullVoiceHandler:
    """Integration test: full handle_voice flow with real STT."""

    @pytest.mark.skipif(not ELEVENLABS_KEY, reason=SKIP_REASON)
    @pytest.mark.asyncio
    async def test_handle_voice_end_to_end(self, tmp_vault, real_audio):
        """Full flow: Telegram voice → download → save → transcribe → record → reply."""
        _, digest_dir, attachments_dir = tmp_vault

        # 1. Set up: patch config to use tmp vault, create active digest
        import config
        import main as main_mod
        original_digest_dir = config.DIGEST_DIR
        original_attachments_dir = config.ATTACHMENTS_DIR
        config.DIGEST_DIR = digest_dir
        config.ATTACHMENTS_DIR = attachments_dir
        main_mod.ATTACHMENTS_DIR = attachments_dir  # main.py imports locally

        try:
            import recorder
            recorder._active_file = None  # Reset state

            from recorder import create_digest, has_active_file, get_active_file
            from config import SGT

            now = datetime.now(SGT)
            create_digest(
                coverage_from=now - timedelta(hours=1),
                coverage_to=now,
                session_summaries=[{"session": "Test", "messages": 5, "summary": "Test."}],
            )
            assert has_active_file()

            # 2. Build mock Telegram Update
            mock_file = AsyncMock()
            mock_file.download_to_drive = AsyncMock(
                side_effect=lambda path: shutil.copy(str(real_audio), path)
            )

            mock_voice = MagicMock()
            mock_voice.file_id = "test_file_id"
            mock_voice.duration = 5

            mock_message = AsyncMock()
            mock_message.voice = mock_voice
            mock_message.audio = None
            mock_message.reply_text = AsyncMock()

            mock_update = MagicMock()
            mock_update.message = mock_message
            mock_message.from_user = MagicMock()
            mock_message.from_user.id = 411364623
            mock_message.from_user.username = "b0yan913"
            mock_message.from_user.first_name = "Boyang"

            mock_context = MagicMock()
            mock_context.bot = AsyncMock()
            mock_context.bot.get_file = AsyncMock(return_value=mock_file)

            # 3. Call handle_voice
            from main import handle_voice
            await handle_voice(mock_update, mock_context)

            # 4. Verify: audio file saved to attachments
            ogg_files = list(attachments_dir.glob("voice-*.ogg"))
            assert len(ogg_files) == 1, "Expected 1 audio file, found %d" % len(ogg_files)
            assert ogg_files[0].stat().st_size > 1000, "Audio file too small"

            # 5. Verify: reply sent with transcription
            mock_message.reply_text.assert_called_once()
            reply_text = mock_message.reply_text.call_args[0][0]
            assert "🎙️" in reply_text, "Reply missing 🎙️ emoji"
            assert "✍️" in reply_text, "Reply missing ✍️ emoji"
            # Should NOT say "unavailable" — real STT should work
            assert "unavailable" not in reply_text.lower(), \
                "STT failed on real audio: %r" % reply_text

            # 6. Verify: digest file contains voice entry
            content = get_active_file().read_text()
            assert "🎙️" in content, "Digest missing voice emoji"
            assert "![[voice-" in content, "Digest missing audio embed"
            assert ".ogg]]" in content, "Digest missing .ogg embed"
            assert "> " in content.split("![[")[1], "Digest missing blockquote transcript"

            print("\n=== INTEGRATION TEST PASSED ===")
            print("Audio file:", ogg_files[0].name)
            print("Reply:", reply_text[:200])
            print("Digest excerpt:", content.split("🎙️")[1][:200])

        finally:
            config.DIGEST_DIR = original_digest_dir
            config.ATTACHMENTS_DIR = original_attachments_dir
            main_mod.ATTACHMENTS_DIR = original_attachments_dir
            recorder._active_file = None


# ============================================================
# Deployed Instance Health Check
# ============================================================

class TestDeployedHealth:
    """Verify the deployed bot instance is healthy."""

    def test_bot_process_running(self):
        """Bot process is alive."""
        result = subprocess.run(
            ["pgrep", "-f", "digest-bot/main.py"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, "Bot process not running"

    def test_bot_logs_recent(self):
        """Bot has recent log activity (< 60s)."""
        log_path = Path("/tmp/digest-bot-stdout.log")
        if not log_path.exists():
            pytest.skip("Log file not found")
        import time
        age = time.time() - log_path.stat().st_mtime
        assert age < 60, "Bot log stale: %.0fs old" % age

    def test_elevenlabs_key_in_env(self):
        """ELEVENLABS_API_KEY is set in .env file."""
        env_path = Path.home() / "digest-bot" / ".env"
        content = env_path.read_text()
        assert "ELEVENLABS_API_KEY=" in content, ".env missing ELEVENLABS_API_KEY"
        # Key should not be empty
        for line in content.splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                val = line.split("=", 1)[1].strip()
                assert len(val) > 10, "ELEVENLABS_API_KEY looks empty"

    def test_attachments_dir_exists_or_creatable(self):
        """Attachments directory is accessible."""
        from config import ATTACHMENTS_DIR
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        assert ATTACHMENTS_DIR.exists()

    @pytest.mark.skipif(not ELEVENLABS_KEY, reason=SKIP_REASON)
    def test_elevenlabs_stt_api_live(self):
        """ElevenLabs STT API is reachable (our key only has STT access)."""
        import requests
        # Send a tiny invalid file — expect 422 (bad input), not 401 (auth fail)
        resp = requests.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": ELEVENLABS_KEY},
            files={"file": ("test.ogg", b"not real audio", "audio/ogg")},
            data={"model_id": "scribe_v2"},
            timeout=10,
        )
        # 422 = auth works, input rejected. 401 = auth broken.
        assert resp.status_code != 401, "ElevenLabs API key is invalid (401)"
        assert resp.status_code != 403, "ElevenLabs API key forbidden (403)"
