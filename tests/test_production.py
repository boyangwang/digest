"""
Production integration tests — test against REAL files and state.

These tests simulate what actually happens when the bot runs:
- Recover old v1 files on startup
- /status response size
- /digest creates correct v2 format
- Full lifecycle with real file I/O
- v1 → v2 migration handling

Run these BEFORE telling Boyang to test.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import recorder
from config import SGT


def _reset():
    recorder._active_file = None


# ============================================================
# v1 → v2 migration: old active files
# ============================================================

class TestCleanSlate:
    """Digest directory should only contain v2 files.
    v1 files have been moved to archive-v1/.
    """

    def setup_method(self):
        _reset()

    def test_empty_dir_starts_idle(self, digest_dir):
        """Empty directory → IDLE, no recovery."""
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is None
        assert not recorder.has_active_file()

    def test_v2_active_file_recovered(self, digest_dir):
        """A v2-format active file is recovered on startup."""
        v2_content = """---
generated_at: "2026-03-01T22:30:00+08:00"
coverage_from: "2026-02-28T22:30:00+08:00"
coverage_to: "2026-03-01T22:30:00+08:00"
status: "active"
---

# Doudou's Summary

Session: CLAW 003
Messages: 50
Summary:
A good day of work.

# Boyang's Recap

"""
        (digest_dir / "2026-03-01-2230.md").write_text(v2_content)

        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            result = recorder.recover_active_on_startup()

        assert result is not None
        assert recorder.has_active_file()

    def test_finalized_not_recovered(self, digest_dir):
        """Finalized files are not recovered."""
        final_content = """---
generated_at: "2026-03-01T22:30:00+08:00"
coverage_from: "2026-02-28T22:30:00+08:00"
coverage_to: "2026-03-01T22:30:00+08:00"
status: "final"
finalized_at: "2026-03-01T23:00:00+08:00"
---

# Doudou's Summary

Session: CLAW 003
Messages: 50
Summary:
Done.

# Boyang's Recap

"""
        (digest_dir / "2026-03-01-2230.md").write_text(final_content)

        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is None


# ============================================================
# /status response sanity
# ============================================================

class TestStatusResponse:
    """Status response must be reasonable for Telegram delivery."""

    def setup_method(self):
        _reset()

    def test_status_content_matches_file(self, digest_dir):
        """Content field must exactly match the file on disk."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Good day."},
                ],
            )
            status = recorder.get_active_status()

        assert status["content"] == fp.read_text()

    def test_status_idle_no_content(self, digest_dir):
        """When IDLE, no content field."""
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            status = recorder.get_active_status()
        assert status["state"] == "IDLE"
        assert "content" not in status or status.get("content") is None

    def test_new_digest_reasonable_size(self, digest_dir):
        """A new v2 digest should be small, not 196KB."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Built the digest bot."},
                    {"session": "Telegram DM", "messages": 10, "summary": "Quick chat."},
                ],
            )
        size = fp.stat().st_size
        # A v2 digest with 2 sessions should be well under 10KB
        assert size < 10000, f"Digest file is {size} bytes — too large for v2 format"

    def test_status_after_multiple_updates(self, digest_dir):
        """After several updates, file should still be reasonable."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 100, "summary": "Morning work. " * 50},
                ],
            )
            for i in range(5):
                recorder.update_digest(
                    new_coverage_to=now + timedelta(minutes=30 * (i + 1)),
                    session_summaries=[
                        {"session": "CLAW 003", "messages": 10, "summary": "Update %d. " % i * 20},
                    ],
                )
            status = recorder.get_active_status()

        content = status["content"]
        # After 6 batches of summaries, should still be under 50KB
        assert len(content) < 50000, f"Status content is {len(content)} chars after 5 updates"
        # All 6 summaries preserved (append-only)
        assert content.count("Session: CLAW 003") == 6


# ============================================================
# Document format verification
# ============================================================

class TestDocumentFormat:
    """Verify the produced document matches SPEC.md exactly."""

    def setup_method(self):
        _reset()

    def test_no_v1_artifacts(self, digest_dir):
        """No v1 format artifacts in new documents."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Summary text."},
                ],
            )
        content = fp.read_text()
        # None of these v1 artifacts should appear
        assert "Previous Night" not in content
        assert "Today's Conversations" not in content
        assert "New Conversations (updated)" not in content
        assert "**Boyang:**" not in content
        assert "**Doudou:**" not in content
        assert "date:" not in content.split("---")[1]  # No date in frontmatter
        assert "day:" not in content.split("---")[1]  # No day in frontmatter

    def test_has_v2_structure(self, digest_dir):
        """Document has exactly the v2 structure."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Summary."},
                ],
            )
        content = fp.read_text()
        # Must have these
        assert "# Doudou's Summary" in content
        assert "# Boyang's Recap" in content
        assert "Session: CLAW 003" in content
        assert "Messages: 50" in content
        assert "Summary:" in content

    def test_update_preserves_structure(self, digest_dir):
        """After update, document still has clean v2 structure."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 100, "summary": "First."},
                ],
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=[
                    {"session": "CLAW 003", "messages": 5, "summary": "Second."},
                ],
            )
            recorder.append_recap("Good night thoughts")

        content = fp.read_text()
        # Structure intact
        assert content.count("# Doudou's Summary") == 1
        assert content.count("# Boyang's Recap") == 1
        # Both summaries present
        assert "First." in content
        assert "Second." in content
        # Recap present
        assert "Good night thoughts" in content
        # No v1 artifacts
        assert "Previous Night" not in content
        assert "New Conversations" not in content

    def test_finalized_document_clean(self, digest_dir):
        """After /sleep, document is clean and final."""
        now = datetime.now(SGT)
        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 50, "summary": "Summary."},
                ],
            )
            recorder.append_recap("Goodnight")
            recorder.finalize()

        content = fp.read_text()
        fm, _ = recorder._parse_frontmatter(content)
        assert fm["status"] == "final"
        assert "finalized_at" in fm
        assert "Goodnight" in content
        assert "Previous Night" not in content


# ============================================================
# Full lifecycle simulation
# ============================================================

class TestFullLifecycle:
    """Simulate real usage: create → update → recap → status → sleep."""

    def setup_method(self):
        _reset()

    def test_complete_evening_session(self, digest_dir):
        """Simulate a real evening: /digest, update, recap, /status, /sleep."""
        now = datetime.now(SGT)

        with patch.object(recorder, "DIGEST_DIR", digest_dir):
            # 22:30 — auto /digest
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=[
                    {"session": "CLAW 003", "messages": 80, "summary": "Worked on digest bot all day."},
                    {"session": "Telegram DM", "messages": 12, "summary": "Quick check-ins."},
                ],
            )

            # 23:00 — /digest again (new messages came in)
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=[
                    {"session": "CLAW 003", "messages": 5, "summary": "Final cleanup."},
                ],
            )

            # 23:15 — Boyang types some thoughts
            recorder.append_recap("Productive day. The bot is shaping up nicely.")

            # 23:20 — /status
            status = recorder.get_active_status()
            assert status["state"] == "ACTIVE"
            assert status["file"] == fp.name
            assert "content" in status
            content = status["content"]

            # Verify everything is in the document
            assert "Worked on digest bot all day." in content
            assert "Quick check-ins." in content
            assert "Final cleanup." in content
            assert "Productive day." in content
            assert content.count("Session: CLAW 003") == 2  # initial + update
            assert content.count("Session: Telegram DM") == 1

            # 23:30 — /sleep
            recorder.finalize()
            assert not recorder.has_active_file()

            # Verify final document
            final = fp.read_text()
            fm, _ = recorder._parse_frontmatter(final)
            assert fm["status"] == "final"

            # Next day — /status shows IDLE
            status = recorder.get_active_status()
            assert status["state"] == "IDLE"
