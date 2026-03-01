"""
Integration tests — Cross-module behavior and regression tests.

These test the system as a whole, verifying that the modules work
together correctly and that past incidents don't recur.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import collector
import recorder
import llm
from config import SGT, MAX_ASSISTANT_LENGTH
from tests.conftest import make_message_entry


# ============================================================
# P1 Regression: Token safety
# ============================================================

class TestTokenSafetyIntegration:
    """Cross-module check: the main bot token must never leak into any module."""

    MAIN_BOT_TOKEN_PREFIX = "8304524800"

    def test_no_main_token_in_config(self):
        import config
        assert self.MAIN_BOT_TOKEN_PREFIX not in config.BOT_TOKEN

    def test_no_main_token_in_source_files(self):
        """Scan all .py files for the main bot token."""
        project_dir = Path(__file__).parent.parent
        for py_file in project_dir.glob("*.py"):
            content = py_file.read_text()
            assert self.MAIN_BOT_TOKEN_PREFIX not in content, \
                f"Main bot token found in {py_file.name}!"


# ============================================================
# Regression: No bare OpenAI API usage
# ============================================================

class TestNoBareOpenAI:
    """The system must ONLY use Doudou (OpenClaw) for LLM calls.
    
    Incident: originally used bare gpt-4o-mini instead of Doudou.
    """

    def test_no_openai_import(self):
        """No module should import openai directly."""
        project_dir = Path(__file__).parent.parent
        for py_file in project_dir.glob("*.py"):
            content = py_file.read_text()
            lines = content.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert "import openai" not in stripped, \
                    f"Direct openai import in {py_file.name}:{i+1}"
                assert "from openai" not in stripped, \
                    f"Direct openai import in {py_file.name}:{i+1}"

    def test_no_openai_api_key_usage(self):
        """No module should reference OPENAI_API_KEY."""
        project_dir = Path(__file__).parent.parent
        for py_file in project_dir.glob("*.py"):
            content = py_file.read_text()
            assert "OPENAI_API_KEY" not in content, \
                f"OPENAI_API_KEY reference in {py_file.name}"


# ============================================================
# End-to-end: collect → summarize → record
# ============================================================

class TestEndToEnd:

    def setup_method(self):
        recorder._active_file = None

    def test_collect_summarize_record(self, tmp_path):
        """Full pipeline: collect messages → summarize → write to vault (v2 format)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        digest_dir = tmp_path / "digest"
        digest_dir.mkdir()

        now = datetime.now(SGT)
        today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)

        sessions = {
            "agent:main:telegram:group:-5125187430": {
                "sessionId": "sess-001",
                "label": "CLAW 003",
            },
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions))
        (sessions_dir / "sess-001.jsonl").write_text("\n".join([
            make_message_entry("user", "What's the plan?", today_9am.isoformat()),
            make_message_entry("assistant", "Let me check.", today_9am.replace(minute=1).isoformat()),
        ]) + "\n")

        # Collect
        with patch.object(collector, "SESSIONS_JSON", sessions_dir / "sessions.json"), \
             patch.object(collector, "SESSION_DIR", sessions_dir):
            prev_night, today_msgs = collector.collect_all_messages(
                now.replace(hour=0, minute=0, second=0, microsecond=0)
            )

        assert len(today_msgs) == 2

        # Summarize (mock Doudou)
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")), \
             patch.object(llm, "_ask_doudou", return_value="A productive planning session."):
            summary = llm.compose_summary("formatted conversations")

        # Record (v2 format — session summaries, no raw conversations)
        session_summaries = [
            {"session": "CLAW 003", "messages": 2, "summary": summary},
        ]
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now.replace(hour=0, minute=0),
                coverage_to=now,
                session_summaries=session_summaries,
            )

        content = filepath.read_text()
        assert "productive" in content
        assert "Session: CLAW 003" in content
        assert "Messages: 2" in content
        assert "# Doudou's Summary" in content
        assert "# Boyang's Recap" in content
        # v2: NO raw conversations in digest file
        assert "What's the plan?" not in content
        assert "Previous Night" not in content
        fm, _ = recorder._parse_frontmatter(content)
        assert fm["status"] == "active"

    def test_conversation_file_not_in_tmp(self, tmp_path):
        """Regression: conversation dump must go to vault, not /tmp/."""
        vault_transcripts = tmp_path / "vault" / "transcripts"

        with patch.object(llm, "CONV_DUMP_DIR", str(vault_transcripts)), \
             patch.object(llm, "_ask_doudou", return_value="Summary"):
            llm.compose_summary("Some text")

        # File should exist in vault location
        files = list(vault_transcripts.glob("conv-*.md"))
        assert len(files) == 1

    def test_boyang_text_never_truncated_e2e(self, tmp_path):
        """CRITICAL: Boyang's messages flow through the entire pipeline untruncated."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        now = datetime.now(SGT)
        long_message = "B" * 10000  # 10KB message from Boyang

        sessions = {
            "agent:main:telegram:dm:411364623": {
                "sessionId": "sess-dm",
                "label": "DM",
            },
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions))
        (sessions_dir / "sess-dm.jsonl").write_text(
            make_message_entry("user", long_message, now.isoformat()) + "\n"
        )

        with patch.object(collector, "SESSIONS_JSON", sessions_dir / "sessions.json"), \
             patch.object(collector, "SESSION_DIR", sessions_dir):
            _, today = collector.collect_all_messages(now - timedelta(hours=1))

        assert len(today) == 1
        assert len(today[0]["text"]) == 10000  # Full, untruncated

    def test_full_lifecycle_with_recap(self, tmp_path):
        """IDLE → /digest → text (recap) → /digest (update) → /sleep → IDLE (v2)."""
        digest_dir = tmp_path / "digest"
        digest_dir.mkdir()
        now = datetime.now(SGT)

        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            recorder._active_file = None

            # /digest (IDLE → ACTIVE)
            f1 = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 100, "summary": "Big batch."},
                ],
            )
            assert recorder.has_active_file()

            # Text reply (recap)
            recorder.append_recap("Feeling good tonight")
            content = f1.read_text()
            assert "Feeling good tonight" in content

            # /digest again (ACTIVE → ACTIVE with update, append-only)
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=[
                    {"session": "CLAW 003", "messages": 5, "summary": "Small update."},
                ],
            )
            content = f1.read_text()
            assert "Big batch." in content  # original preserved
            assert "Small update." in content  # new appended

            # /sleep (ACTIVE → IDLE)
            recorder.finalize()
            assert not recorder.has_active_file()
            fm, _ = recorder._parse_frontmatter(f1.read_text())
            assert fm["status"] == "final"


# ============================================================
# Regression: CLI command correctness
# ============================================================

class TestCLICommands:
    """Verify the correct CLI commands are used.
    
    Regression: originally used 'openclaw sessions send' which doesn't exist.
    """

    def test_uses_agent_not_sessions(self):
        """Must use 'openclaw agent', NOT 'openclaw sessions send'."""
        import inspect
        source = inspect.getsource(llm)
        # Should contain 'openclaw agent'
        assert "openclaw" in source and "agent" in source
        # Should NOT reference 'sessions send' as a CLI command
        # (the string 'sessions send' might appear in comments, so check the actual command list)
        assert '"sessions", "send"' not in source


# ============================================================
# Noise filtering integration
# ============================================================

class TestNoiseFilteringIntegration:

    def test_digest_trigger_messages_filtered(self, tmp_path):
        """Messages from the digest bot itself should be filtered."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        now = datetime.now(SGT)
        sessions = {
            "agent:main:telegram:dm:411364623": {
                "sessionId": "sess-dm",
                "label": "DM",
            },
        }
        (sessions_dir / "sessions.json").write_text(json.dumps(sessions))
        (sessions_dir / "sess-dm.jsonl").write_text("\n".join([
            make_message_entry("user", "[DIGEST_SUMMARY_REQUEST] Read file...", now.isoformat()),
            make_message_entry("user", "[NUDGE_REQUEST] Time to sleep", now.isoformat()),
            make_message_entry("user", "Actual message from Boyang", now.isoformat()),
        ]) + "\n")

        with patch.object(collector, "SESSIONS_JSON", sessions_dir / "sessions.json"), \
             patch.object(collector, "SESSION_DIR", sessions_dir):
            _, today = collector.collect_all_messages(now - timedelta(hours=1))

        assert len(today) == 1
        assert today[0]["text"] == "Actual message from Boyang"
