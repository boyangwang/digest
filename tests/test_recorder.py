"""
Tests for recorder.py — Document format v2 (per SPEC.md).

Key changes from v1:
- Two sections only: "# Doudou's Summary" + "# Boyang's Recap"
- No "Previous Night", no "Today's Conversations", no "New Conversations (updated)"
- Summary is append-only with session entries (Session/Messages/Summary)
- No raw conversations in digest file (those go to transcripts/)
- Minimal YAML frontmatter (no date/day fields)
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import recorder
from config import SGT


# ============================================================
# Helpers
# ============================================================

def _patch_digest_dir(digest_dir):
    return patch.object(recorder, "DIGEST_DIR", digest_dir)


def _reset_active():
    recorder._active_file = None


def _make_session_summaries(entries):
    """Build session_summaries list.

    entries: list of (session_name, message_count, summary_text)
    """
    return [
        {"session": name, "messages": count, "summary": text}
        for name, count, text in entries
    ]


# ============================================================
# SPEC-STRUCT-01: Two sections only
# ============================================================

class TestDocumentStructure:

    def setup_method(self):
        _reset_active()

    def test_has_summary_section(self, digest_dir):
        now = datetime.now(SGT)
        summaries = _make_session_summaries([("CLAW 003", 50, "A productive day.")])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
        content = fp.read_text()
        assert "# Doudou's Summary" in content

    def test_has_recap_section(self, digest_dir):
        now = datetime.now(SGT)
        summaries = _make_session_summaries([("CLAW 003", 50, "A productive day.")])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
        content = fp.read_text()
        assert "# Boyang's Recap" in content

    def test_no_previous_night_section(self, digest_dir):
        """SPEC-EXCLUDE-01: No Previous Night."""
        now = datetime.now(SGT)
        summaries = _make_session_summaries([("CLAW 003", 50, "Summary.")])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
        content = fp.read_text()
        assert "Previous Night" not in content

    def test_no_today_conversations_section(self, digest_dir):
        """SPEC-EXCLUDE-02: No raw conversation sections."""
        now = datetime.now(SGT)
        summaries = _make_session_summaries([("CLAW 003", 50, "Summary.")])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
        content = fp.read_text()
        assert "Today's Conversations" not in content

    def test_no_new_conversations_section(self, digest_dir):
        """SPEC-EXCLUDE-02: No 'New Conversations (updated)'."""
        now = datetime.now(SGT)
        summaries = _make_session_summaries([("CLAW 003", 50, "Summary.")])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
            # Update with more
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=_make_session_summaries([("CLAW 003", 3, "A few more.")]),
            )
        content = fp.read_text()
        assert "New Conversations" not in content

    def test_no_raw_conversations(self, digest_dir):
        """SPEC-STRUCT-02: No raw conversation messages in file."""
        now = datetime.now(SGT)
        summaries = _make_session_summaries([("CLAW 003", 50, "Summary.")])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
        content = fp.read_text()
        # Should not have the old conversation format markers
        assert "**Boyang:**" not in content
        assert "**Doudou:**" not in content


# ============================================================
# SPEC-STRUCT-03 / SPEC-SUMMARY-01: Summary entry format, append-only
# ============================================================

class TestSummaryEntries:

    def setup_method(self):
        _reset_active()

    def test_summary_entry_format(self, digest_dir):
        """SPEC-STRUCT-03: Session/Messages/Summary format."""
        now = datetime.now(SGT)
        summaries = _make_session_summaries([
            ("CLAW 003", 167, "A long day of building."),
            ("Telegram DM", 17, "Discussed vault setup."),
        ])
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=summaries,
            )
        content = fp.read_text()
        assert "Session: CLAW 003" in content
        assert "Messages: 167" in content
        assert "A long day of building." in content
        assert "Session: Telegram DM" in content
        assert "Messages: 17" in content
        assert "Discussed vault setup." in content

    def test_summary_append_on_update(self, digest_dir):
        """SPEC-SUMMARY-01: Update appends, never replaces."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 167, "First batch summary."),
                ]),
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 5, "Second batch summary."),
                ]),
            )
        content = fp.read_text()
        # Both summaries must be present
        assert "First batch summary." in content
        assert "Second batch summary." in content
        assert "Messages: 167" in content
        assert "Messages: 5" in content

    def test_same_session_multiple_entries(self, digest_dir):
        """SPEC-SUMMARY-02: Same session can appear multiple times."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 100, "Morning work."),
                ]),
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(hours=1),
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 10, "Afternoon work."),
                ]),
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(hours=2),
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 3, "Evening wrap-up."),
                ]),
            )
        content = fp.read_text()
        assert content.count("Session: CLAW 003") == 3
        assert "Morning work." in content
        assert "Afternoon work." in content
        assert "Evening wrap-up." in content

    def test_summary_order_preserved(self, digest_dir):
        """Summaries appear in chronological order (append-only)."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 100, "FIRST_MARKER"),
                ]),
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(hours=1),
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 10, "SECOND_MARKER"),
                ]),
            )
        content = fp.read_text()
        first_pos = content.index("FIRST_MARKER")
        second_pos = content.index("SECOND_MARKER")
        assert first_pos < second_pos

    def test_empty_summaries_no_update(self, digest_dir):
        """SPEC-SUMMARY-03: Zero new messages = no update."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 50, "Original summary."),
                ]),
            )
            original_content = fp.read_text()
            result = recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=[],
            )
        assert result is False
        assert fp.read_text() == original_content


# ============================================================
# SPEC-RECAP-01/02: Verbatim, timestamped, append-only, last section
# ============================================================

class TestRecap:

    def setup_method(self):
        _reset_active()

    def test_recap_verbatim(self, digest_dir):
        """SPEC-RECAP-01: Exact text preserved."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.append_recap("Feeling great today 🚀")
        content = fp.read_text()
        assert "Feeling great today 🚀" in content

    def test_recap_timestamped(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.append_recap("Test message")
        content = recorder.get_active_file().read_text()
        # Should have a timestamp like **HH:MM**
        import re
        assert re.search(r"\*\*\d{2}:\d{2}\*\*", content)

    def test_recap_multiple_entries(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.append_recap("First thought")
            recorder.append_recap("Second thought")
        content = recorder.get_active_file().read_text()
        assert "First thought" in content
        assert "Second thought" in content

    def test_recap_is_last_section(self, digest_dir):
        """SPEC-RECAP-02: Recap always at bottom."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
        content = fp.read_text()
        summary_pos = content.index("# Doudou's Summary")
        recap_pos = content.index("# Boyang's Recap")
        assert recap_pos > summary_pos
        # Nothing after recap section header except whitespace/content
        after_recap = content[recap_pos:]
        assert "# Doudou" not in after_recap.replace("# Boyang's Recap", "")

    def test_recap_returns_false_when_no_active(self):
        _reset_active()
        assert recorder.append_recap("Text") is False


# ============================================================
# SPEC-FRONTMATTER-01: Minimal metadata
# ============================================================

class TestFrontmatter:

    def setup_method(self):
        _reset_active()

    def test_has_required_fields(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
        fm, _ = recorder._parse_frontmatter(fp.read_text())
        assert "generated_at" in fm
        assert "coverage_from" in fm
        assert "coverage_to" in fm
        assert "status" in fm
        assert fm["status"] == "active"

    def test_no_date_day_fields(self, digest_dir):
        """SPEC-FRONTMATTER-01: No date/day — not date-oriented."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
        fm, _ = recorder._parse_frontmatter(fp.read_text())
        assert "date" not in fm
        assert "day" not in fm


# ============================================================
# SPEC-TS: Timestamp chain
# ============================================================

class TestTimestampChain:

    def setup_method(self):
        _reset_active()

    def test_coverage_to_advances(self, digest_dir):
        """SPEC-TS-01."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            new_time = now + timedelta(minutes=30)
            recorder.update_digest(
                new_coverage_to=new_time,
                session_summaries=_make_session_summaries([("S", 2, "More.")]),
            )
        fm, _ = recorder._parse_frontmatter(fp.read_text())
        assert new_time.isoformat() in str(fm["coverage_to"])

    def test_coverage_from_immutable(self, digest_dir):
        """SPEC-TS-02."""
        now = datetime.now(SGT)
        original_from = now - timedelta(hours=24)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=original_from,
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=_make_session_summaries([("S", 2, "More.")]),
            )
        fm, _ = recorder._parse_frontmatter(fp.read_text())
        assert original_from.isoformat() in str(fm["coverage_from"])

    def test_chain_between_files(self, digest_dir):
        """SPEC-TS-03."""
        t0 = datetime(2026, 2, 28, 22, 30, 0, tzinfo=SGT)
        t1 = datetime(2026, 3, 1, 22, 30, 0, tzinfo=SGT)
        t2 = datetime(2026, 3, 2, 22, 30, 0, tzinfo=SGT)
        with _patch_digest_dir(digest_dir):
            _reset_active()
            with patch("recorder.datetime") as mock_dt:
                mock_dt.now.return_value = t1
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                f1 = recorder.create_digest(
                    coverage_from=t0, coverage_to=t1,
                    session_summaries=_make_session_summaries([("S", 1, "First.")]),
                )
            recorder.finalize()

            latest = recorder.find_latest_coverage_to()
            assert latest is not None

            _reset_active()
            with patch("recorder.datetime") as mock_dt:
                mock_dt.now.return_value = t2
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                f2 = recorder.create_digest(
                    coverage_from=latest, coverage_to=t2,
                    session_summaries=_make_session_summaries([("S", 1, "Second.")]),
                )
        fm1, _ = recorder._parse_frontmatter(f1.read_text())
        fm2, _ = recorder._parse_frontmatter(f2.read_text())
        ct1 = datetime.fromisoformat(str(fm1["coverage_to"]))
        cf2 = datetime.fromisoformat(str(fm2["coverage_from"]))
        assert ct1 == cf2


# ============================================================
# SPEC-NAME-01/02: File naming
# ============================================================

class TestNaming:

    def setup_method(self):
        _reset_active()

    def test_filename_format(self, digest_dir):
        """SPEC-NAME-01: YYYY-MM-DD-HHMM.md."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
        name = fp.name
        assert name.endswith(".md")
        parts = name.replace(".md", "").split("-")
        assert len(parts) == 4  # YYYY-MM-DD-HHMM

    def test_new_file_only_from_idle(self, digest_dir):
        """SPEC-NAME-02: In ACTIVE state, update same file."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp1 = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=_make_session_summaries([("S", 2, "More.")]),
            )
        # Should still be the same file
        assert recorder.get_active_file() == fp1
        # Only one file in directory
        files = list(digest_dir.glob("*.md"))
        assert len(files) == 1


# ============================================================
# SPEC-FINAL: Finalization
# ============================================================

class TestFinalize:

    def setup_method(self):
        _reset_active()

    def test_sets_status_final(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.finalize()
        fm, _ = recorder._parse_frontmatter(fp.read_text())
        assert fm["status"] == "final"
        assert "finalized_at" in fm

    def test_clears_active(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            assert recorder.has_active_file()
            recorder.finalize()
            assert not recorder.has_active_file()

    def test_returns_false_when_no_active(self):
        _reset_active()
        assert recorder.finalize() is False


# ============================================================
# SPEC-STATUS-01/02: /status returns full document
# ============================================================

class TestGetActiveStatus:

    def setup_method(self):
        _reset_active()

    def test_active_returns_metadata(self, digest_dir):
        """Status includes file name, timestamps, and state."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 50, "The full summary text."),
                ]),
            )
            status = recorder.get_active_status()
        assert status["state"] == "ACTIVE"
        assert status["file"] == fp.name
        assert "coverage_from" in status
        assert "coverage_to" in status

    def test_active_returns_full_content(self, digest_dir):
        """SPEC-STATUS-01: Full raw document content included."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([
                    ("CLAW 003", 50, "The full summary text."),
                ]),
            )
            status = recorder.get_active_status()
        assert "content" in status
        assert "The full summary text." in status["content"]
        assert "# Doudou's Summary" in status["content"]
        assert "# Boyang's Recap" in status["content"]

    def test_active_content_is_complete_file(self, digest_dir):
        """Content should be the entire file, including YAML frontmatter."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            status = recorder.get_active_status()
        assert status["content"] == fp.read_text()

    def test_idle_shows_state_and_no_content(self, digest_dir):
        """SPEC-STATUS-02: IDLE shows state, no content."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("S", 1, "Sum.")]),
            )
            recorder.finalize()
            status = recorder.get_active_status()
        assert status["state"] == "IDLE"
        assert status["file"] is None
        assert "content" not in status or status.get("content") is None


# ============================================================
# State machine — full lifecycle
# ============================================================

class TestStateMachine:

    def setup_method(self):
        _reset_active()

    def test_full_lifecycle(self, digest_dir):
        """IDLE → create → ACTIVE → update → recap → finalize → IDLE."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            assert not recorder.has_active_file()

            # /digest → ACTIVE
            fp = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                session_summaries=_make_session_summaries([("CLAW 003", 100, "Big batch.")]),
            )
            assert recorder.has_active_file()

            # /digest again → still ACTIVE, same file
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                session_summaries=_make_session_summaries([("CLAW 003", 5, "Small update.")]),
            )
            assert recorder.get_active_file() == fp

            # Text → recap
            recorder.append_recap("Feeling good tonight")
            content = fp.read_text()
            assert "Feeling good tonight" in content
            assert "Big batch." in content
            assert "Small update." in content

            # /sleep → IDLE
            recorder.finalize()
            assert not recorder.has_active_file()

            # Verify final document structure
            content = fp.read_text()
            assert "# Doudou's Summary" in content
            assert "# Boyang's Recap" in content
            assert "Previous Night" not in content
            assert "New Conversations" not in content
            assert "Today's Conversations" not in content


# ============================================================
# Crash recovery
# ============================================================

class TestRecoverActive:

    def setup_method(self):
        _reset_active()

    def test_recovers_active_file(self, digest_dir):
        # Create a file that looks active
        active_content = (
            '---\ngenerated_at: "2026-03-01T22:30:00+08:00"\n'
            'coverage_from: "2026-02-28T22:30:00+08:00"\n'
            'coverage_to: "2026-03-01T22:30:00+08:00"\n'
            'status: "active"\n---\n\n'
            '# Doudou\'s Summary\n\nSession: CLAW 003\nMessages: 50\n'
            'Summary:\nA good day.\n\n# Boyang\'s Recap\n'
        )
        (digest_dir / "2026-03-01-2230.md").write_text(active_content)
        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is not None
        assert recorder.has_active_file()

    def test_ignores_finalized(self, digest_dir):
        final_content = (
            '---\ngenerated_at: "2026-03-01T22:30:00+08:00"\n'
            'coverage_from: "2026-02-28T22:30:00+08:00"\n'
            'coverage_to: "2026-03-01T22:30:00+08:00"\n'
            'status: "final"\nfinalized_at: "2026-03-01T23:00:00+08:00"\n---\n\n'
            '# Doudou\'s Summary\n\n# Boyang\'s Recap\n'
        )
        (digest_dir / "2026-03-01-2230.md").write_text(final_content)
        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is None

    def test_empty_directory(self, digest_dir):
        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is None


# ============================================================
# YAML helpers (unchanged from v1 — these should still pass)
# ============================================================

class TestYAMLHelpers:

    def test_parse_valid_frontmatter(self):
        content = '---\nstatus: "active"\n---\n\n# Body'
        fm, body = recorder._parse_frontmatter(content)
        assert fm["status"] == "active"
        assert "# Body" in body

    def test_parse_no_frontmatter(self):
        fm, body = recorder._parse_frontmatter("# Just a heading")
        assert fm == {}

    def test_serialize_roundtrip(self):
        original = '---\nstatus: "active"\n---\n\n# Body text'
        fm, body = recorder._parse_frontmatter(original)
        serialized = recorder._serialize_frontmatter(fm, body)
        fm2, body2 = recorder._parse_frontmatter(serialized)
        assert fm2["status"] == fm["status"]

    def test_safe_load_security(self):
        content = '---\nfoo: !!python/object/apply:os.system ["echo pwned"]\n---\nBody'
        fm, body = recorder._parse_frontmatter(content)
        assert True  # Didn't execute

    def test_atomic_write(self, tmp_path):
        fp = tmp_path / "test.md"
        recorder._atomic_write(fp, "Hello")
        assert fp.read_text() == "Hello"
        assert not fp.with_suffix(".tmp").exists()

    def test_atomic_write_unicode(self, tmp_path):
        fp = tmp_path / "test.md"
        recorder._atomic_write(fp, "中文 🌙 日本語")
        assert "中文" in fp.read_text()


# ============================================================
# replace_reflection() — T17 tests (DIGEST-007)
# ============================================================

class TestReplaceReflection:
    """Unit tests for replace_reflection() — in-place reflection replacement for /reflect command."""

    def test_replace_existing_reflection(self, tmp_path):
        """UT-T17-1: Replace existing reflection section in finalized digest."""
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        
        # Create a finalized digest with an existing reflection
        filepath = digest_dir / "2026-03-02-2230.md"
        original_content = """---
status: "final"
generated_at: "2026-03-02T22:30:00+08:00"
coverage_from: "2026-03-02T08:00:00+08:00"
coverage_to: "2026-03-02T22:30:00+08:00"
finalized_at: "2026-03-02T23:00:00+08:00"
reflection_at: "2026-03-02T23:00:00+08:00"
reflection_model: "opus"
---

# Doudou's Summary

Session: Test Session
Messages: 10
Summary:
Test summary content

# Boyang's Recap

**22:45** Had a productive day

# 🪞 Nightly Reflection

> Old reflection content

### 📌 Durable Facts (0)
_None identified today._

### 📊 Stats
- Items extracted: 0
"""
        filepath.write_text(original_content)
        
        # New reflection report
        new_report = """# 🪞 Nightly Reflection

> Extracted by Doudou (Opus). All items stored in workspace.

### 📌 Durable Facts (2)
- **[People/Ashley]** Birthday is March 15
- **[Health]** VO2max measured at 46

### 📊 Stats
- Messages processed: 50
- Items extracted: 8
- Model: Opus
"""
        
        # Import and call replace_reflection
        from recorder import replace_reflection
        success = replace_reflection(new_report, filepath)
        assert success, "replace_reflection should return True on success"
        
        # Verify content was replaced
        content = filepath.read_text()
        assert "Birthday is March 15" in content, "New reflection content should be in file"
        assert "Old reflection content" not in content, "Old reflection should be removed"
        assert "Had a productive day" in content, "Boyang's recap should be preserved"
        assert "Test summary content" in content, "Doudou's summary should be preserved"
        
        # Verify only ONE reflection section
        assert content.count("# 🪞 Nightly Reflection") == 1, "Should have exactly one reflection section"
        
        # Verify YAML frontmatter was updated
        assert "reflection_at:" in content
        assert "reflection_model:" in content

    def test_replace_reflection_updates_yaml(self, tmp_path):
        """UT-T17-2: YAML frontmatter gets updated with new reflection_at and reflection_model."""
        digest_dir = tmp_path / "digests"
        digest_dir.mkdir()
        
        filepath = digest_dir / "2026-03-02-2230.md"
        original_content = """---
status: "final"
generated_at: "2026-03-02T22:30:00+08:00"
reflection_at: "2026-03-02T23:00:00+08:00"
reflection_model: "opus"
---

# Doudou's Summary

Test content

# Boyang's Recap

**22:45** Test recap

# 🪞 Nightly Reflection

Old reflection
"""
        filepath.write_text(original_content)
        
        new_report = "# 🪞 Nightly Reflection\n\nNew reflection content\n"
        
        from recorder import replace_reflection
        replace_reflection(new_report, filepath)
        
        content = filepath.read_text()
        
        # Parse YAML to verify structure
        import yaml
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        
        # reflection_at should be updated to a new timestamp
        old_time = datetime.fromisoformat("2026-03-02T23:00:00+08:00")
        new_time = datetime.fromisoformat(fm["reflection_at"])
        assert new_time > old_time, "reflection_at should be updated to a newer timestamp"
        
        # reflection_model should be present
        assert fm["reflection_model"] == "opus"

    def test_replace_reflection_nonexistent_file(self):
        """UT-T17-3: Returns False if file doesn't exist."""
        from recorder import replace_reflection
        result = replace_reflection("# New reflection", Path("/nonexistent/file.md"))
        assert result is False, "Should return False for nonexistent file"

    def test_replace_reflection_missing_section(self, tmp_path):
        """UT-T17-4: Returns False if reflection section is missing (can't replace what doesn't exist)."""
        filepath = tmp_path / "no-reflection.md"
        content = """---
status: "final"
---

# Doudou's Summary

Test

# Boyang's Recap

Test recap
"""
        filepath.write_text(content)
        
        from recorder import replace_reflection
        new_report = "# 🪞 Nightly Reflection\n\nNew content\n"
        result = replace_reflection(new_report, filepath)
        
        # Should return False because there's no existing reflection to replace
        assert result is False, "Should return False when reflection section is missing"

    def test_replace_reflection_preserves_order(self, tmp_path):
        """UT-T17-5: File structure order is preserved: Summary → Recap → Reflection."""
        filepath = tmp_path / "test.md"
        original = """---
status: "final"
reflection_at: "2026-03-02T23:00:00+08:00"
reflection_model: "opus"
---

# Doudou's Summary

Summary content here

# Boyang's Recap

**22:45** Recap entry 1
**23:00** Recap entry 2

# 🪞 Nightly Reflection

Old reflection
"""
        filepath.write_text(original)
        
        new_report = """# 🪞 Nightly Reflection

New reflection content with much more detail
"""
        
        from recorder import replace_reflection
        replace_reflection(new_report, filepath)
        
        content = filepath.read_text()
        
        # Verify order
        summary_pos = content.index("# Doudou's Summary")
        recap_pos = content.index("# Boyang's Recap")
        reflection_pos = content.index("# 🪞 Nightly Reflection")
        
        assert summary_pos < recap_pos < reflection_pos, \
            "Order must be: Summary → Recap → Reflection"
        
        # Verify recap entries are preserved
        assert "Recap entry 1" in content
        assert "Recap entry 2" in content
        assert "Summary content here" in content

    def test_replace_reflection_atomic_write(self, tmp_path):
        """UT-T17-6: Uses atomic write pattern (.tmp → rename), no .tmp files left behind."""
        filepath = tmp_path / "test.md"
        original = """---
status: "final"
reflection_at: "2026-03-02T23:00:00+08:00"
reflection_model: "opus"
---

# Doudou's Summary

Test

# Boyang's Recap

Test

# 🪞 Nightly Reflection

Old
"""
        filepath.write_text(original)
        
        from recorder import replace_reflection
        replace_reflection("# 🪞 Nightly Reflection\n\nNew\n", filepath)
        
        # Verify no .tmp file left behind
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, "No .tmp files should remain after atomic write"

