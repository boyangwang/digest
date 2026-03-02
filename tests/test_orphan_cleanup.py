"""Tests for T4/T5: Orphan empty digest file cleanup on startup.

Bug B: Bot restarts create empty 394-byte digest files with `status: active`
that never get content. recover_active_on_startup() should detect and clean
up stale empty active files (status: active, no summary content, older than 1h).
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# We need to mock DIGEST_DIR before importing recorder
_test_dir = None


def _make_digest_file(directory, filename, status="active", has_summary=False, age_hours=0):
    """Helper: create a digest file with given properties."""
    sgt = timezone(timedelta(hours=8))
    generated_at = datetime.now(sgt) - timedelta(hours=age_hours)
    
    fm = {
        "status": status,
        "generated_at": generated_at.isoformat(),
        "coverage_from": generated_at.isoformat(),
        "coverage_to": generated_at.isoformat(),
    }
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
    
    body = "\n# Doudou's Summary\n\n"
    if has_summary:
        body += "Session: CLAW 008 (TG Group)\nMessages: 5\nSummary:\nBoyang discussed longevity research.\n\n"
    body += "# Boyang's Recap\n\n"
    
    content = "---\n%s\n---\n%s" % (fm_str, body)
    filepath = directory / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


class TestOrphanDetection:
    """Test that orphan files are correctly identified."""

    def test_empty_active_old_file_cleaned(self, tmp_path):
        """Empty active file older than 1h should be marked stale."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            # Create an empty active file, 2 hours old
            orphan = _make_digest_file(tmp_path, "orphan-20260301-2230.md",
                                        status="active", has_summary=False, age_hours=2)
            
            recorder.recover_active_on_startup()
            
            # The orphan should now have status: stale
            content = orphan.read_text()
            fm, _ = recorder._parse_frontmatter(content)
            assert fm.get("status") == "stale", \
                f"Empty active file older than 1h should be marked stale, got {fm.get('status')}"

    def test_recent_empty_active_not_cleaned(self, tmp_path):
        """Empty active file younger than 1h should NOT be cleaned (could be in-progress)."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            # Create an empty active file, 30 min old
            recent = _make_digest_file(tmp_path, "recent-20260301-2300.md",
                                        status="active", has_summary=False, age_hours=0)
            
            recorder.recover_active_on_startup()
            
            content = recent.read_text()
            fm, _ = recorder._parse_frontmatter(content)
            # Should still be active (recovered as the active file, not marked stale)
            assert fm.get("status") in ("active", "draft"), \
                f"Recent empty active file should NOT be marked stale, got {fm.get('status')}"

    def test_active_with_content_not_cleaned(self, tmp_path):
        """Active file WITH summary content should NOT be cleaned up, even if old."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            # Create an active file with real summary content, 3 hours old
            real = _make_digest_file(tmp_path, "real-20260301-2230.md",
                                      status="active", has_summary=True, age_hours=3)
            
            recorder.recover_active_on_startup()
            
            content = real.read_text()
            fm, _ = recorder._parse_frontmatter(content)
            assert fm.get("status") == "active" or fm.get("status") == "draft", \
                f"Active file with content should NOT be cleaned, got {fm.get('status')}"


class TestFinalizedFilesUntouched:
    """Finalized files should never be modified."""

    def test_finalized_file_not_touched(self, tmp_path):
        """Files with status: final should never be modified."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            finalized = _make_digest_file(tmp_path, "final-20260301-2230.md",
                                           status="final", has_summary=True, age_hours=24)
            original_content = finalized.read_text()
            
            recorder.recover_active_on_startup()
            
            assert finalized.read_text() == original_content, \
                "Finalized file content should be unchanged"

    def test_stale_file_not_touched(self, tmp_path):
        """Files already marked stale should not be modified again."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            stale = _make_digest_file(tmp_path, "stale-20260301-2230.md",
                                       status="stale", has_summary=False, age_hours=5)
            original_content = stale.read_text()
            
            recorder.recover_active_on_startup()
            
            assert stale.read_text() == original_content, \
                "Already-stale file should not be modified"


class TestMultipleOrphans:
    """Test cleanup with multiple orphan files + one real active."""

    def test_cleans_orphans_preserves_real(self, tmp_path):
        """Multiple orphans get cleaned, real active file is preserved."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            # Create 3 orphans (empty, old)
            orphan1 = _make_digest_file(tmp_path, "orphan1-20260301-2000.md",
                                         status="active", has_summary=False, age_hours=5)
            orphan2 = _make_digest_file(tmp_path, "orphan2-20260301-2100.md",
                                         status="active", has_summary=False, age_hours=4)
            orphan3 = _make_digest_file(tmp_path, "orphan3-20260301-2130.md",
                                         status="active", has_summary=False, age_hours=3)
            
            # Create 1 real active file (most recent, has content)
            real = _make_digest_file(tmp_path, "real-20260301-2230.md",
                                      status="active", has_summary=True, age_hours=0)
            
            recovered = recorder.recover_active_on_startup()
            
            # Real file should be the recovered active
            assert recovered is not None
            assert recovered.name == "real-20260301-2230.md"
            
            # Orphans should be stale
            for orphan in [orphan1, orphan2, orphan3]:
                content = orphan.read_text()
                fm, _ = recorder._parse_frontmatter(content)
                assert fm.get("status") == "stale", \
                    f"{orphan.name} should be stale, got {fm.get('status')}"

    def test_no_files_no_crash(self, tmp_path):
        """Empty directory should not crash."""
        with patch("recorder.DIGEST_DIR", tmp_path), \
             patch("recorder._active_file", None):
            import recorder
            recorder._active_file = None
            
            result = recorder.recover_active_on_startup()
            assert result is None
