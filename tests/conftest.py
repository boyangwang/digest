"""
Shared fixtures for digest-bot tests.

All tests use temporary directories — never touch the real Obsidian vault or
OpenClaw session files.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Set dummy token BEFORE config is imported by any test module.
# The prefix 8324650609 is the bot's public numeric ID (not a secret).
# The suffix is fake. Real token lives in .env (gitignored).
os.environ.setdefault("DIGEST_BOT_TOKEN", "8324650609:FAKE_TEST_TOKEN_NOT_REAL")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

SGT = timezone(timedelta(hours=8))


@pytest.fixture
def tmp_dir(tmp_path):
    """A temporary directory for test files."""
    return tmp_path


@pytest.fixture
def digest_dir(tmp_path):
    """Temporary digest output directory (replaces Obsidian vault path)."""
    d = tmp_path / "Doudou-Digest"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def transcript_dir(tmp_path):
    """Temporary directory with mock session transcripts."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    return sessions_dir


@pytest.fixture
def sample_sessions_json(transcript_dir):
    """Create a sessions.json with test sessions and matching JSONL files."""
    sessions = {
        "agent:main:telegram:group:-5125187430": {
            "sessionId": "sess-claw003",
            "label": "CLAW 003",
        },
        "agent:main:telegram:dm:411364623": {
            "sessionId": "sess-dm-boyang",
            "label": "DM Boyang",
        },
        "agent:main:main": {
            "sessionId": "sess-webchat",
            "label": "Webchat",
        },
        # Should be skipped (cron session)
        "agent:main:cron:daily": {
            "sessionId": "sess-cron",
            "label": "Daily Cron",
        },
    }

    sessions_json = transcript_dir / "sessions.json"
    sessions_json.write_text(json.dumps(sessions))
    return sessions_json


def make_message_entry(role, text, timestamp, msg_type="message"):
    """Create a JSONL entry matching OpenClaw transcript format."""
    if role == "user":
        # Wrap in metadata like OpenClaw does
        wrapped = (
            'Conversation info (untrusted metadata):\n```json\n'
            '{"message_id": "123", "sender_id": "411364623"}\n```\n\n'
            'Sender (untrusted metadata):\n```json\n'
            '{"label": "Boyang", "name": "Boyang"}\n```\n\n'
            + text
        )
        content = wrapped
    else:
        content = text

    return json.dumps({
        "type": msg_type,
        "timestamp": timestamp,
        "message": {
            "role": role,
            "content": content,
        },
    })


def make_assistant_entry_list(text, timestamp):
    """Create assistant entry with list-style content (tool_use + text blocks)."""
    return json.dumps({
        "type": "message",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool123", "name": "exec", "input": {"command": "ls"}},
                {"type": "text", "text": text},
            ],
        },
    })


@pytest.fixture
def populated_transcripts(transcript_dir, sample_sessions_json):
    """Create transcript files with realistic message data."""
    now = datetime.now(SGT)
    today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)
    today_10am = now.replace(hour=10, minute=0, second=0, microsecond=0)
    today_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
    yesterday_23pm = (now - timedelta(days=1)).replace(hour=23, minute=30, second=0, microsecond=0)

    # CLAW 003 — has messages today and last night
    claw003 = transcript_dir / "sess-claw003.jsonl"
    claw003.write_text("\n".join([
        make_message_entry("user", "Let's set up the digest bot", yesterday_23pm.isoformat()),
        make_message_entry("assistant", "I'll help you build it. Let me check the current state.", yesterday_23pm.replace(minute=31).isoformat()),
        make_message_entry("user", "How's the progress?", today_9am.isoformat()),
        make_message_entry("assistant", "Good progress. The collector is working and we have 49 messages.", today_9am.replace(minute=5).isoformat()),
    ]) + "\n")

    # DM — Boyang messages today
    dm = transcript_dir / "sess-dm-boyang.jsonl"
    dm.write_text("\n".join([
        make_message_entry("user", "Check my schedule for today", today_10am.isoformat()),
        make_message_entry("assistant", "You have a meeting at 2pm with the Stanford team.", today_10am.replace(minute=2).isoformat()),
    ]) + "\n")

    # Webchat — includes noise that should be filtered
    webchat = transcript_dir / "sess-webchat.jsonl"
    webchat.write_text("\n".join([
        make_message_entry("user", "heartbeat_ok", today_11am.isoformat()),  # noise — but this is user sending "heartbeat_ok"
        make_message_entry("assistant", "HEARTBEAT_OK", today_11am.replace(minute=1).isoformat()),  # noise
        make_message_entry("assistant", "NO_REPLY", today_11am.replace(minute=2).isoformat()),  # noise
        make_message_entry("user", "What's the weather?", today_11am.replace(minute=10).isoformat()),
        make_message_entry("assistant", "It's 28°C and sunny in Singapore.", today_11am.replace(minute=11).isoformat()),
    ]) + "\n")

    # Cron session file (should be skipped by session filter)
    cron = transcript_dir / "sess-cron.jsonl"
    cron.write_text(make_message_entry("assistant", "Cron job ran successfully", today_9am.isoformat()) + "\n")

    return transcript_dir


@pytest.fixture
def sample_digest_content():
    """A well-formed v2 digest file content for testing."""
    return """---
generated_at: "2026-03-01T22:30:00+08:00"
coverage_from: "2026-02-28T22:30:00+08:00"
coverage_to: "2026-03-01T22:30:00+08:00"
status: "active"
---

# Doudou's Summary

Session: CLAW 003
Messages: 50
Summary:
A productive day of building the sleep digest bot.

# Boyang's Recap

"""


@pytest.fixture
def finalized_digest_content():
    """A finalized v2 digest file content."""
    return """---
generated_at: "2026-02-28T22:30:00+08:00"
coverage_from: "2026-02-27T22:30:00+08:00"
coverage_to: "2026-02-28T22:30:00+08:00"
status: "final"
finalized_at: "2026-02-28T23:15:00+08:00"
---

# Doudou's Summary

Session: CLAW 003
Messages: 20
Summary:
A quiet evening.

# Boyang's Recap

Goodnight.
"""
