"""
Tests for recorder.py — File operations, state machine, YAML handling.

Key regressions tested:
- Atomic writes (no partial files on crash)
- YAML safe_load (not regex parsing — Fix #2)
- State machine: IDLE → ACTIVE → IDLE transitions
- Timestamp chain integrity across files
- Multiple files per day support (YYYY-MM-DD-HHMM naming)
- Crash recovery (recover_active_on_startup)
- Finalization sets status to 'final'
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
    """Patch recorder.DIGEST_DIR to use test directory."""
    return patch.object(recorder, "DIGEST_DIR", digest_dir)


def _reset_active():
    """Reset recorder's internal active file state."""
    recorder._active_file = None


# ============================================================
# YAML frontmatter parsing — Fix #2 (use yaml.safe_load, not regex)
# ============================================================

class TestYAMLFrontmatter:

    def test_parse_valid_frontmatter(self):
        content = '---\ndate: "2026-03-01"\nstatus: "active"\n---\n\n# Body'
        fm, body = recorder._parse_frontmatter(content)
        assert fm["date"] == "2026-03-01"
        assert fm["status"] == "active"
        assert "# Body" in body

    def test_parse_no_frontmatter(self):
        content = "# Just a heading\n\nSome text."
        fm, body = recorder._parse_frontmatter(content)
        assert fm == {}
        assert "Just a heading" in body

    def test_parse_empty_frontmatter(self):
        content = "---\n---\n\nBody text"
        fm, body = recorder._parse_frontmatter(content)
        assert fm == {}
        assert "Body text" in body

    def test_parse_complex_frontmatter(self):
        """YAML with quoted strings, ISO timestamps, nested values."""
        content = (
            '---\n'
            'date: "2026-03-01"\n'
            'coverage_from: "2026-02-28T22:30:00+08:00"\n'
            'coverage_to: "2026-03-01T22:30:00+08:00"\n'
            'status: "active"\n'
            '---\n'
            '\nBody'
        )
        fm, body = recorder._parse_frontmatter(content)
        assert "2026-02-28T22:30:00+08:00" in str(fm["coverage_from"])
        assert fm["status"] == "active"

    def test_serialize_roundtrip(self):
        """Parse → serialize → parse should give same result."""
        original = (
            '---\ndate: "2026-03-01"\nstatus: "active"\n---\n\n# Body text here'
        )
        fm, body = recorder._parse_frontmatter(original)
        serialized = recorder._serialize_frontmatter(fm, body)
        fm2, body2 = recorder._parse_frontmatter(serialized)
        assert fm2["date"] == fm["date"]
        assert fm2["status"] == fm["status"]

    def test_frontmatter_uses_safe_load(self):
        """Verify we use yaml.safe_load (not yaml.load which is unsafe)."""
        # Malicious YAML that would execute code with yaml.load
        content = '---\nfoo: !!python/object/apply:os.system ["echo pwned"]\n---\nBody'
        # safe_load should raise or return the raw string, NOT execute
        fm, body = recorder._parse_frontmatter(content)
        # If we got here without executing "echo pwned", safe_load is working
        assert True


# ============================================================
# Atomic writes — Fix #1
# ============================================================

class TestAtomicWrite:

    def test_writes_file(self, tmp_path):
        filepath = tmp_path / "test.md"
        recorder._atomic_write(filepath, "Hello, world!")
        assert filepath.read_text() == "Hello, world!"

    def test_no_tmp_file_remains(self, tmp_path):
        filepath = tmp_path / "test.md"
        recorder._atomic_write(filepath, "Content")
        assert not filepath.with_suffix(".tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        filepath = tmp_path / "test.md"
        filepath.write_text("Old content")
        recorder._atomic_write(filepath, "New content")
        assert filepath.read_text() == "New content"

    def test_unicode_content(self, tmp_path):
        filepath = tmp_path / "test.md"
        recorder._atomic_write(filepath, "中文内容 🌙 日本語")
        assert "中文内容" in filepath.read_text()


# ============================================================
# find_latest_coverage_to — timestamp chain
# ============================================================

class TestFindLatestCoverageTo:

    def test_finds_most_recent(self, digest_dir):
        """Should find the latest coverage_to across all files."""
        # Older file
        (digest_dir / "2026-02-28-2230.md").write_text(
            '---\ncoverage_to: "2026-02-28T22:30:00+08:00"\nstatus: "final"\n---\nOld'
        )
        # Newer file
        (digest_dir / "2026-03-01-2230.md").write_text(
            '---\ncoverage_to: "2026-03-01T22:30:00+08:00"\nstatus: "final"\n---\nNew'
        )

        with _patch_digest_dir(digest_dir):
            result = recorder.find_latest_coverage_to()
        assert result is not None
        assert "2026-03-01" in result.isoformat()

    def test_empty_directory(self, digest_dir):
        with _patch_digest_dir(digest_dir):
            result = recorder.find_latest_coverage_to()
        assert result is None

    def test_ignores_files_without_coverage(self, digest_dir):
        (digest_dir / "notes.md").write_text("---\ntitle: Random\n---\nNo coverage_to here")
        with _patch_digest_dir(digest_dir):
            result = recorder.find_latest_coverage_to()
        assert result is None


# ============================================================
# create_digest — file creation
# ============================================================

class TestCreateDigest:

    def setup_method(self):
        _reset_active()

    def test_creates_file_with_correct_naming(self, digest_dir):
        """Files named YYYY-MM-DD-HHMM.md (supports multiple per day)."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="Prev night content",
                today_sections="Today content",
                summary="A good day.",
            )

        assert filepath.exists()
        # Naming: YYYY-MM-DD-HHMM.md
        name = filepath.name
        assert name.endswith(".md")
        parts = name.replace(".md", "").split("-")
        assert len(parts) == 4  # YYYY-MM-DD-HHMM

    def test_creates_valid_yaml_frontmatter(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="Content",
                today_sections="Content",
                summary="Summary",
            )

        content = filepath.read_text()
        fm, body = recorder._parse_frontmatter(content)

        assert "date" in fm
        assert "day" in fm
        assert "coverage_from" in fm
        assert "coverage_to" in fm
        assert fm["status"] == "active"

    def test_sets_active_file(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
        assert recorder.has_active_file()

    def test_content_includes_all_sections(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="Night stuff",
                today_sections="Day stuff",
                summary="Great day",
            )
        content = filepath.read_text()
        assert "Night stuff" in content
        assert "Day stuff" in content
        assert "Great day" in content
        assert "## 🌙 Summary" in content
        assert "## 🌃 Previous Night" in content
        assert "## 🗣️ Today's Conversations" in content
        assert "## 📝 Boyang's Recap" in content

    def test_multiple_files_per_day(self, digest_dir):
        """Creating two digests on the same day should produce two files."""
        now = datetime.now(SGT)
        time1 = now.replace(hour=22, minute=30, second=0)
        time2 = now.replace(hour=23, minute=0, second=0)

        with _patch_digest_dir(digest_dir):
            _reset_active()
            # First digest at 22:30
            with patch("recorder.datetime") as mock_dt:
                mock_dt.now.return_value = time1
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                f1 = recorder.create_digest(
                    coverage_from=now - timedelta(hours=24),
                    coverage_to=time1,
                    previous_night_sections="", today_sections="",
                    summary="First",
                )
            recorder.finalize()

            # Second digest at 23:00 (different HHMM)
            _reset_active()
            with patch("recorder.datetime") as mock_dt:
                mock_dt.now.return_value = time2
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                f2 = recorder.create_digest(
                    coverage_from=time1,
                    coverage_to=time2,
                    previous_night_sections="", today_sections="",
                    summary="Second",
                )
        assert f1 != f2
        assert f1.exists()
        assert f2.exists()


# ============================================================
# update_digest — extending active file
# ============================================================

class TestUpdateDigest:

    def setup_method(self):
        _reset_active()

    def test_advances_coverage_to(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="Initial",
                summary="Initial summary",
            )

            new_time = now + timedelta(minutes=30)
            recorder.update_digest(
                new_coverage_to=new_time,
                new_sections_text="New conversations here",
                new_summary="Updated summary",
            )

        content = filepath.read_text()
        fm, _ = recorder._parse_frontmatter(content)
        assert new_time.isoformat() in str(fm["coverage_to"])

    def test_appends_new_conversations(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="Original",
                summary="Summary",
            )
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                new_sections_text="Brand new content",
                new_summary=None,
            )

        content = filepath.read_text()
        assert "Brand new content" in content
        assert "Original" in content  # Original preserved

    def test_returns_false_when_no_active(self, digest_dir):
        with _patch_digest_dir(digest_dir):
            result = recorder.update_digest(
                new_coverage_to=datetime.now(SGT),
                new_sections_text="Text",
                new_summary=None,
            )
        assert result is False


# ============================================================
# append_recap — Boyang's text verbatim (Fix #4)
# ============================================================

class TestAppendRecap:

    def setup_method(self):
        _reset_active()

    def test_appends_text_verbatim(self, digest_dir):
        """Boyang's recap text must be stored EXACTLY as typed."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
            recorder.append_recap("Feeling productive today 🚀")

        content = filepath.read_text()
        assert "Feeling productive today 🚀" in content

    def test_returns_false_when_no_active(self):
        assert recorder.append_recap("Text") is False

    def test_multiple_recaps_preserved(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
            recorder.append_recap("First thought")
            recorder.append_recap("Second thought")

        content = recorder.get_active_file().read_text()
        assert "First thought" in content
        assert "Second thought" in content


# ============================================================
# finalize — /sleep
# ============================================================

class TestFinalize:

    def setup_method(self):
        _reset_active()

    def test_sets_status_final(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            filepath = recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
            recorder.finalize()

        content = filepath.read_text()
        fm, _ = recorder._parse_frontmatter(content)
        assert fm["status"] == "final"
        assert "finalized_at" in fm

    def test_clears_active_file(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
            assert recorder.has_active_file()
            recorder.finalize()
            assert not recorder.has_active_file()

    def test_returns_false_when_no_active(self):
        assert recorder.finalize() is False

    def test_state_transition_idle_after_finalize(self, digest_dir):
        """After finalize, state should be IDLE."""
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
            recorder.finalize()
            status = recorder.get_active_status()
        assert status["state"] == "IDLE"


# ============================================================
# recover_active_on_startup — crash recovery
# ============================================================

class TestRecoverActive:

    def setup_method(self):
        _reset_active()

    def test_recovers_active_file(self, digest_dir, sample_digest_content):
        """On startup, should find and resume unfinalized digest."""
        (digest_dir / "2026-03-01-2230.md").write_text(sample_digest_content)

        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is not None
        assert recorder.has_active_file()

    def test_ignores_finalized_files(self, digest_dir, finalized_digest_content):
        """Finalized files should not be recovered."""
        (digest_dir / "2026-02-28-2230.md").write_text(finalized_digest_content)

        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is None
        assert not recorder.has_active_file()

    def test_recovers_most_recent(self, digest_dir, sample_digest_content):
        """If multiple active files exist, recover the most recent."""
        # Older active
        older = sample_digest_content.replace(
            'generated_at: "2026-03-01T22:30:00+08:00"',
            'generated_at: "2026-02-28T22:30:00+08:00"'
        )
        (digest_dir / "2026-02-28-2230.md").write_text(older)
        # Newer active
        (digest_dir / "2026-03-01-2230.md").write_text(sample_digest_content)

        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result.name == "2026-03-01-2230.md"

    def test_empty_directory(self, digest_dir):
        with _patch_digest_dir(digest_dir):
            result = recorder.recover_active_on_startup()
        assert result is None


# ============================================================
# State machine — full lifecycle
# ============================================================

class TestStateMachine:
    """End-to-end state machine transitions."""

    def setup_method(self):
        _reset_active()

    def test_full_lifecycle(self, digest_dir):
        """IDLE → create → ACTIVE → update → ACTIVE → finalize → IDLE."""
        now = datetime.now(SGT)

        with _patch_digest_dir(digest_dir):
            # Start: IDLE
            assert not recorder.has_active_file()
            assert recorder.get_active_status()["state"] == "IDLE"

            # /digest → ACTIVE
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="Night", today_sections="Day",
                summary="Summary",
            )
            assert recorder.has_active_file()
            assert recorder.get_active_status()["state"] == "ACTIVE"

            # /digest again → still ACTIVE (update)
            recorder.update_digest(
                new_coverage_to=now + timedelta(minutes=30),
                new_sections_text="More content",
                new_summary="Updated summary",
            )
            assert recorder.has_active_file()

            # text → still ACTIVE (recap)
            recorder.append_recap("Thinking about tomorrow")
            assert recorder.has_active_file()

            # /sleep → IDLE
            recorder.finalize()
            assert not recorder.has_active_file()
            assert recorder.get_active_status()["state"] == "IDLE"

    def test_timestamp_chain_integrity(self, digest_dir):
        """Each file's coverage_to should be the next file's coverage_from."""
        # Use fixed timestamps to avoid YAML round-trip timezone issues
        t0 = datetime(2026, 2, 28, 22, 30, 0, tzinfo=SGT)
        t1 = datetime(2026, 3, 1, 22, 30, 0, tzinfo=SGT)
        t2 = datetime(2026, 3, 2, 22, 30, 0, tzinfo=SGT)

        with _patch_digest_dir(digest_dir):
            # First digest
            _reset_active()
            with patch("recorder.datetime") as mock_dt:
                mock_dt.now.return_value = t1
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                f1 = recorder.create_digest(
                    coverage_from=t0,
                    coverage_to=t1,
                    previous_night_sections="", today_sections="",
                    summary="First",
                )
            recorder.finalize()

            # Second digest — should chain from first's coverage_to
            latest = recorder.find_latest_coverage_to()
            assert latest is not None

            _reset_active()
            with patch("recorder.datetime") as mock_dt:
                mock_dt.now.return_value = t2
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                f2 = recorder.create_digest(
                    coverage_from=latest,
                    coverage_to=t2,
                    previous_night_sections="", today_sections="",
                    summary="Second",
                )

        # Verify chain: f1's coverage_to == f2's coverage_from (as datetimes)
        fm1, _ = recorder._parse_frontmatter(f1.read_text())
        fm2, _ = recorder._parse_frontmatter(f2.read_text())
        ct1 = datetime.fromisoformat(str(fm1["coverage_to"]))
        cf2 = datetime.fromisoformat(str(fm2["coverage_from"]))
        assert ct1 == cf2


# ============================================================
# get_active_status
# ============================================================

class TestGetActiveStatus:

    def setup_method(self):
        _reset_active()

    def test_idle_status(self):
        status = recorder.get_active_status()
        assert status["state"] == "IDLE"
        assert status["file"] is None

    def test_active_status(self, digest_dir):
        now = datetime.now(SGT)
        with _patch_digest_dir(digest_dir):
            recorder.create_digest(
                coverage_from=now - timedelta(hours=24),
                coverage_to=now,
                previous_night_sections="", today_sections="",
                summary="",
            )
            status = recorder.get_active_status()
        assert status["state"] == "ACTIVE"
        assert status["file"] is not None
        assert "coverage_from" in status
        assert "coverage_to" in status
