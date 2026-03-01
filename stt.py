"""
STT — Speech-to-Text via ElevenLabs Scribe v2.

Single-function module (SPEC-VOICE-07): easy to swap providers.
No imports from main.py, recorder.py, or any other bot module.

Usage:
    from stt import transcribe
    text = transcribe("/path/to/audio.ogg")  # str or None
"""

import logging
import os

import requests

logger = logging.getLogger("digest-bot.stt")

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_MODEL = "scribe_v2"


def transcribe(audio_path: str) -> str | None:
    """Transcribe audio file using ElevenLabs Scribe v2.

    Args:
        audio_path: Path to audio file (.ogg, .mp3, .wav, etc.)

    Returns:
        Transcribed text string, or None on any failure.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        logger.warning("No ELEVENLABS_API_KEY set — cannot transcribe")
        return None

    try:
        with open(audio_path, "rb") as f:
            response = requests.post(
                ELEVENLABS_API_URL,
                headers={"xi-api-key": api_key},
                files={"file": (os.path.basename(audio_path), f, "audio/ogg")},
                data={"model_id": ELEVENLABS_MODEL},
                timeout=60,
            )

        if response.status_code != 200:
            logger.warning(
                "ElevenLabs API error %d: %s"
                % (response.status_code, response.text[:300])
            )
            return None

        data = response.json()
        text = data.get("text", "").strip()
        if not text:
            logger.warning("ElevenLabs returned empty text")
            return None

        logger.info("Transcribed %d chars (lang=%s)" % (len(text), data.get("language_code", "?")))
        return text

    except requests.Timeout:
        logger.warning("ElevenLabs API timeout")
        return None
    except Exception as e:
        logger.warning("STT error: %s" % e)
        return None
