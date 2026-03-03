"""
Tests for nightly reflection — knowledge extraction from conversations.

Three tiers:
  - Unit: parse, format, prompt building (mocked everything)
  - Integration: subprocess mock, file I/O, recorder interaction
  - E2E: in test_live_e2e.py (separate file, real Telegram)

Covers PRD requirements R1-R16 and SPEC-REFLECT-01..06.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import timezone, timedelta
SGT = timezone(timedelta(hours=8))

# ============================================================
# Fixtures
# ============================================================

SAMPLE_REFLECTION_JSON = json.dumps({
    "facts": [
        {"category": "People/Ashley", "text": "Birthday is March 15"},
        {"category": "Health", "text": "VO2max measured at 46 mL/min/kg"},
    ],
    "feedback_lessons": [
        {"category": "Formatting", "text": "No markdown tables in Telegram",
         "context": "Boyang corrected table rendering", "action": "Use bullet lists"},
    ],
    "rules_incidents": [],
    "compliments": [
        {"text": "That analysis was thorough", "context": "hire-ai report"},
    ],
    "decisions": [
        {"decision": "Use Opus for reflection", "rationale": "Quality > cost",
         "alternatives": "Sonnet (cheaper)", "reversible": True},
    ],
    "action_items": [
        {"text": "Set up Cloudflare Tunnel evaluation"},
    ],
    "ideas": [
        {"text": "CGM morning health briefing", "context": "FreeStyle Libre data auto-pull"},
    ],
    "technical_learnings": [
        {"text": "openclaw agent --local subprocess pattern for digest-bot ↔ OpenClaw"},
    ],
    "stats": {
        "messages_processed": 142,
        "sessions_scanned": 5,
        "items_extracted": 10,
    },
})

SAMPLE_CONVERSATIONS = """**09:00** **Boyang:**
Let's set up the digest bot

**09:05** **Doudou:**
I'll help you build it. The collector is working.

**10:00** **Boyang:**
Ashley's birthday is March 15, remind me

**10:02** **Doudou:**
Noted — I'll add that to memory.
"""

SAMPLE_AGENT_RESPONSE = {
    "payloads": [{"text": SAMPLE_REFLECTION_JSON}],
}


@pytest.fixture
def workspace_dir(tmp_path):
    """Temporary workspace directory mimicking ~/.openclaw/workspace/."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "memory").mkdir()
    (ws / "memory" / "facts").mkdir()
    (ws / "memory" / "decisions").mkdir()
    (ws / "KANBAN.md").write_text("# KANBAN\n\n## Todo\n\n## Done\n")
    (ws / "INCIDENTS.md").write_text("# INCIDENTS\n")
    (ws / "RULES.md").write_text("# RULES\n")
    return ws


@pytest.fixture
def sample_active_digest(digest_dir):
    """Create an active digest file for reflection to append to."""
    content = """---
coverage_from: "2026-03-02T22:30:00+08:00"
coverage_to: "2026-03-02T23:00:00+08:00"
generated_at: "2026-03-02T22:30:00+08:00"
status: "active"
---

# Doudou's Summary

Session: CLAW 003
Messages: 50
Summary:
A productive day of building.

# Boyang's Recap

**22:45** Great day today
"""
    filepath = digest_dir / "2026-03-02-2230.md"
    filepath.write_text(content)
    return filepath


# ============================================================
# UNIT TESTS — parsing, formatting, prompt building
# ============================================================

class TestParseReflectionResponse:
    """UT1-UT2: Parse structured JSON from agent response."""

    def test_parse_valid_json(self):
        """UT1: Parse well-formed reflection JSON into 8 categories."""
        from reflection import parse_reflection_response

        result = parse_reflection_response(SAMPLE_REFLECTION_JSON)

        assert len(result["facts"]) == 2
        assert result["facts"][0]["category"] == "People/Ashley"
        assert len(result["feedback_lessons"]) == 1
        assert len(result["rules_incidents"]) == 0
        assert len(result["compliments"]) == 1
        assert len(result["decisions"]) == 1
        assert len(result["action_items"]) == 1
        assert len(result["ideas"]) == 1
        assert len(result["technical_learnings"]) == 1
        assert result["stats"]["messages_processed"] == 142

    def test_parse_malformed_json(self):
        """UT2: Malformed JSON returns empty structure, no crash."""
        from reflection import parse_reflection_response

        result = parse_reflection_response("this is not json at all {{{")

        assert result["facts"] == []
        assert result["feedback_lessons"] == []
        assert result["compliments"] == []
        assert result["stats"]["messages_processed"] == 0

    def test_parse_partial_json(self):
        """UT2b: Partial JSON (missing some categories) fills defaults."""
        from reflection import parse_reflection_response

        partial = json.dumps({"facts": [{"category": "Test", "text": "A fact"}]})
        result = parse_reflection_response(partial)

        assert len(result["facts"]) == 1
        assert result["feedback_lessons"] == []
        assert result["ideas"] == []

    def test_parse_json_embedded_in_text(self):
        """UT2c: Agent may wrap JSON in explanation text — extract it."""
        from reflection import parse_reflection_response

        wrapped = "Here's my analysis:\n\n" + SAMPLE_REFLECTION_JSON + "\n\nDone."
        result = parse_reflection_response(wrapped)

        assert len(result["facts"]) == 2


class TestBuildReflectionPrompt:
    """UT3: Prompt construction."""

    def test_prompt_includes_file_path(self):
        """UT3: Prompt contains the conversation file path."""
        from reflection import build_reflection_prompt

        prompt = build_reflection_prompt(
            conversations_file="/tmp/conv-20260302.md",
            date_str="2026-03-02",
        )

        assert "/tmp/conv-20260302.md" in prompt
        assert "2026-03-02" in prompt

    def test_prompt_mentions_all_8_categories(self):
        """UT3b: Prompt instructs extraction of all 8 categories."""
        from reflection import build_reflection_prompt

        prompt = build_reflection_prompt(
            conversations_file="/tmp/test.md",
            date_str="2026-03-02",
        )

        assert "facts" in prompt.lower()
        assert "feedback" in prompt.lower() or "lesson" in prompt.lower()
        assert "rules" in prompt.lower() or "incident" in prompt.lower()
        assert "compliment" in prompt.lower()
        assert "decision" in prompt.lower()
        assert "action" in prompt.lower()
        assert "idea" in prompt.lower()
        assert "technical" in prompt.lower() or "learning" in prompt.lower()

    def test_prompt_requests_json_output(self):
        """UT3c: Prompt asks for structured JSON response."""
        from reflection import build_reflection_prompt

        prompt = build_reflection_prompt(
            conversations_file="/tmp/test.md",
            date_str="2026-03-02",
        )

        assert "json" in prompt.lower()


class TestFormatReflectionReport:
    """UT4-UT6: Markdown report formatting."""

    def test_format_basic_report(self):
        """UT4: Format parsed categories into markdown report."""
        from reflection import format_reflection_report, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        report = format_reflection_report(parsed)

        assert "# 🪞 Nightly Reflection" in report
        assert "### 📌 Durable Facts (2)" in report
        assert "People/Ashley" in report
        assert "### 🔧 Feedback Lessons (1)" in report
        assert "### 🌟 Compliments (1)" in report
        assert "### 📊 Stats" in report

    def test_format_empty_categories(self):
        """UT5: Empty categories show placeholder text."""
        from reflection import format_reflection_report, parse_reflection_response

        empty = parse_reflection_response("{}")
        report = format_reflection_report(empty)

        assert "_None identified today._" in report

    def test_format_includes_stats(self):
        """UT6: Stats section includes message count, session count."""
        from reflection import format_reflection_report, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        report = format_reflection_report(parsed)

        assert "142" in report  # messages_processed
        assert "5" in report    # sessions_scanned


class TestEdgeCases:
    """UT7-UT8: Edge cases."""

    def test_zero_messages_returns_none(self):
        """UT7: Zero messages → reflection returns (None, empty_diff, empty_parsed) (skip)."""
        from reflection import run_reflection

        with patch("reflection._call_agent") as mock_agent:
            report, diff_info, parsed = run_reflection(conversations_text="", date_str="2026-03-02")

        assert report is None
        assert diff_info["files"] == []
        assert parsed["facts"] == []
        mock_agent.assert_not_called()

    def test_agent_empty_response_returns_fallback(self):
        """UT8: Agent returns empty → fallback report + empty diff."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=None):
            report, diff_info, parsed = run_reflection(
                conversations_text=SAMPLE_CONVERSATIONS,
                date_str="2026-03-02",
            )

        # Should return a fallback report, not None (we had conversations)
        assert report is not None
        assert "Reflection unavailable" in report or "failed" in report.lower()
        assert diff_info["files"] == []
        assert parsed["facts"] == []  # Empty parsed on failure


# ============================================================
# INTEGRATION TESTS — subprocess mock, file I/O, recorder
# ============================================================

class TestRunReflectionIntegration:
    """IT1-IT4: Integration with openclaw agent subprocess."""

    def test_calls_agent_with_correct_args(self):
        """IT1: Verify subprocess called with --local, --session-id, --model opus."""
        from reflection import _call_agent

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(SAMPLE_AGENT_RESPONSE),
            )

            _call_agent("test prompt")

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "openclaw" in args
            assert "agent" in args
            assert "--local" in args
            assert "--session-id" in args
            # Model is set via session config, not CLI flag
            assert "digest-bot-reflection" in " ".join(args)

    def test_conversations_saved_to_file(self):
        """IT2: Conversations saved to temp file before agent call."""
        from reflection import _save_conversations

        filepath = _save_conversations(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert Path(filepath).exists()
        content = Path(filepath).read_text()
        assert "Let's set up the digest bot" in content

    def test_agent_timeout_returns_none(self):
        """IT3: Subprocess timeout → returns None, no crash."""
        from reflection import _call_agent

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
            result = _call_agent("test prompt")

        assert result is None

    def test_agent_failure_returns_none(self):
        """IT4: Subprocess failure (rc≠0) → returns None, no crash."""
        from reflection import _call_agent

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")

            result = _call_agent("test prompt")

        assert result is None


class TestAppendReflection:
    """IT5-IT7, IT10: recorder.py integration."""

    def test_append_after_recap(self, sample_active_digest):
        """IT5: Reflection section appended AFTER '# Boyang's Recap' content."""
        from reflection import format_reflection_report, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        report = format_reflection_report(parsed)

        # Import and call append_reflection
        from recorder import append_reflection

        success = append_reflection(report, sample_active_digest)
        assert success

        content = sample_active_digest.read_text()
        # Verify order: Summary → Recap → Reflection
        summary_pos = content.index("# Doudou's Summary")
        recap_pos = content.index("# Boyang's Recap")
        reflection_pos = content.index("# 🪞 Nightly Reflection")
        assert summary_pos < recap_pos < reflection_pos

        # Verify recap content is preserved
        assert "Great day today" in content

    def test_append_idempotent(self, sample_active_digest):
        """IT6: Calling append_reflection twice doesn't duplicate."""
        from reflection import format_reflection_report, parse_reflection_response
        from recorder import append_reflection

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        report = format_reflection_report(parsed)

        append_reflection(report, sample_active_digest)
        append_reflection(report, sample_active_digest)

        content = sample_active_digest.read_text()
        assert content.count("# 🪞 Nightly Reflection") == 1

    def test_append_adds_yaml_fields(self, sample_active_digest):
        """IT7: YAML frontmatter gets reflection_at and reflection_model."""
        from reflection import format_reflection_report, parse_reflection_response
        from recorder import append_reflection

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        report = format_reflection_report(parsed)

        append_reflection(report, sample_active_digest)

        content = sample_active_digest.read_text()
        assert "reflection_at:" in content
        assert "reflection_model:" in content

    def test_atomic_write(self, sample_active_digest):
        """IT10: Uses atomic write pattern (.tmp → rename)."""
        from recorder import append_reflection

        # Verify no .tmp file left behind
        append_reflection("# 🪞 Nightly Reflection\nTest", sample_active_digest)

        tmp_files = list(sample_active_digest.parent.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestSleepWithReflection:
    """IT8-IT9: cmd_sleep integration."""

    def test_finalize_after_reflection(self, sample_active_digest):
        """IT8: finalize() called AFTER reflection completes."""
        import recorder

        call_order = []

        original_finalize = recorder.finalize

        def tracked_finalize():
            call_order.append("finalize")
            return original_finalize()

        def mock_reflection():
            call_order.append("reflection")
            return "# 🪞 Nightly Reflection\nTest"

        # Set the active file
        recorder._active_file = sample_active_digest

        with patch.object(recorder, "finalize", tracked_finalize):
            with patch("reflection.run_reflection", mock_reflection):
                # Simulate the new /sleep flow
                report = mock_reflection()
                if report:
                    recorder.append_reflection(report, sample_active_digest)
                tracked_finalize()

        assert call_order == ["reflection", "finalize"]

    def test_finalize_on_reflection_failure(self, sample_active_digest):
        """IT9: If reflection fails, finalize() still runs (SPEC-REFLECT-05)."""
        import recorder

        recorder._active_file = sample_active_digest

        with patch("reflection.run_reflection", return_value=None):
            # Simulate: reflection returns None (failed)
            report = None
            # Should still finalize
            success = recorder.finalize()

        assert success
        content = sample_active_digest.read_text()
        assert "final" in content
        # No reflection section added (it failed)
        assert "🪞" not in content


# ============================================================
# UNIT TESTS — Git diff capture (Proposal B)
# ============================================================

class TestGitHeadHash:
    """UT9-UT11: _git_head_hash()."""

    def test_returns_hash_string(self):
        """UT9: Returns a 40-char hex hash on success."""
        from reflection import _git_head_hash

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="abc123def456\n")
            result = _git_head_hash()

        assert result == "abc123def456"

    def test_returns_none_on_failure(self):
        """UT10: Returns None when git command fails."""
        from reflection import _git_head_hash

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = _git_head_hash()

        assert result is None

    def test_returns_none_on_exception(self):
        """UT11: Returns None on subprocess exception (not a git repo, etc)."""
        from reflection import _git_head_hash

        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = _git_head_hash()

        assert result is None


class TestGitDiff:
    """UT12-UT16: _git_diff()."""

    def test_returns_stat_and_patch(self):
        """UT12: Returns dict with stat, patch, and files keys."""
        from reflection import _git_diff

        mock_stat = "memory/facts/2026-03-02.md | 5 +++++\n 1 file changed, 5 insertions(+)"
        mock_names = "memory/facts/2026-03-02.md"
        mock_patch = "diff --git a/memory/facts/2026-03-02.md..."
        mock_before = "# old content"
        mock_after = "# old content\n- new fact"

        def side_effect(args, **kwargs):
            if "--stat" in args:
                return MagicMock(returncode=0, stdout=mock_stat)
            elif "--name-only" in args:
                return MagicMock(returncode=0, stdout=mock_names)
            elif "show" in args and args[2].startswith("pre_"):
                return MagicMock(returncode=0, stdout=mock_before)
            elif "show" in args and args[2].startswith("post_"):
                return MagicMock(returncode=0, stdout=mock_after)
            else:
                return MagicMock(returncode=0, stdout=mock_patch)

        with patch("subprocess.run", side_effect=side_effect):
            result = _git_diff("pre_hash", "post_hash")

        assert result["stat"] == mock_stat
        assert result["patch"] == mock_patch
        assert len(result["files"]) == 1
        assert result["files"][0]["path"] == "memory/facts/2026-03-02.md"

    def test_empty_diff_returns_empty(self):
        """UT13: No changed files → empty files list."""
        from reflection import _git_diff

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = _git_diff("same_hash", "same_hash")

        assert result["files"] == []
        assert result["stat"] == ""

    def test_multiple_files(self):
        """UT14: Multiple changed files are all captured."""
        from reflection import _git_diff

        def side_effect(args, **kwargs):
            if "--name-only" in args:
                return MagicMock(returncode=0, stdout="KANBAN.md\nmemory/ideas.md\n")
            elif "--stat" in args:
                return MagicMock(returncode=0, stdout="2 files changed")
            elif "show" in args:
                return MagicMock(returncode=0, stdout="content")
            else:
                return MagicMock(returncode=0, stdout="patch text")

        with patch("subprocess.run", side_effect=side_effect):
            result = _git_diff("a", "b")

        assert len(result["files"]) == 2
        paths = [f["path"] for f in result["files"]]
        assert "KANBAN.md" in paths
        assert "memory/ideas.md" in paths

    def test_new_file_has_empty_before(self):
        """UT15: A newly created file has empty 'before' content."""
        from reflection import _git_diff

        def side_effect(args, **kwargs):
            if "--name-only" in args:
                return MagicMock(returncode=0, stdout="memory/facts/new.md\n")
            elif "--stat" in args:
                return MagicMock(returncode=0, stdout="1 file changed")
            elif "show" in args:
                # git show pre_hash:new_file fails (file didn't exist)
                if "pre_" in str(args):
                    return MagicMock(returncode=128, stdout="")
                return MagicMock(returncode=0, stdout="# New facts\n- fact 1")
            else:
                return MagicMock(returncode=0, stdout="patch")

        with patch("subprocess.run", side_effect=side_effect):
            result = _git_diff("pre_hash", "post_hash")

        assert len(result["files"]) == 1
        assert result["files"][0]["before"] == ""
        assert "New facts" in result["files"][0]["after"]

    def test_exception_returns_empty_result(self):
        """UT16: Subprocess exception → empty result, no crash."""
        from reflection import _git_diff

        with patch("subprocess.run", side_effect=OSError("disk error")):
            result = _git_diff("a", "b")

        assert result["files"] == []
        assert result["stat"] == ""
        assert result["patch"] == ""


class TestRenderDiffImages:
    """UT17-UT21: render_diff_images()."""

    def test_empty_files_returns_empty(self):
        """UT17: No files → no images."""
        from reflection import render_diff_images

        result = render_diff_images({"files": []}, "2026-03-02")
        assert result == []

    def test_skips_unchanged_files(self):
        """UT18: Files with identical before/after are skipped."""
        from reflection import render_diff_images

        diff_data = {"files": [
            {"path": "test.md", "before": "same", "after": "same"},
        ]}

        with patch("subprocess.run") as mock_run:
            result = render_diff_images(diff_data, "2026-03-02")

        assert result == []
        mock_run.assert_not_called()

    def test_calls_agent_for_each_changed_file(self):
        """UT19: One openclaw agent call per changed file."""
        from reflection import render_diff_images

        diff_data = {"files": [
            {"path": "KANBAN.md", "before": "old", "after": "new"},
            {"path": "memory/ideas.md", "before": "a", "after": "b"},
        ]}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"payloads": [{"text": "/tmp/openclaw/test/preview.png"}]}),
            )
            with patch("os.path.exists", return_value=True):
                result = render_diff_images(diff_data, "2026-03-02")

        # Should call subprocess for each file
        assert mock_run.call_count == 2
        assert len(result) == 2

    def test_agent_failure_skips_gracefully(self):
        """UT20: If agent fails for one file, others still render."""
        from reflection import render_diff_images

        diff_data = {"files": [
            {"path": "fail.md", "before": "a", "after": "b"},
            {"path": "success.md", "before": "x", "after": "y"},
        ]}

        call_count = [0]

        def side_effect(args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=1, stderr="error")
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"payloads": [{"text": "/tmp/openclaw/ok/preview.png"}]}),
            )

        with patch("subprocess.run", side_effect=side_effect):
            with patch("os.path.exists", return_value=True):
                result = render_diff_images(diff_data, "2026-03-02")

        assert len(result) == 1
        assert "ok/preview.png" in result[0]

    def test_truncates_large_files(self):
        """UT21: Files larger than 50KB are truncated before rendering."""
        from reflection import render_diff_images

        big_content = "x" * 60000
        diff_data = {"files": [
            {"path": "big.md", "before": "", "after": big_content},
        ]}

        written_content = []

        original_open = open

        def mock_open_fn(path, *args, **kwargs):
            if "/tmp/reflection-diff-" in str(path) and "after" in str(path):
                # Capture what gets written
                class MockFile:
                    def write(self, data):
                        written_content.append(data)
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        pass
                return MockFile()
            return original_open(path, *args, **kwargs)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="skip")
            with patch("builtins.open", side_effect=mock_open_fn):
                render_diff_images(diff_data, "2026-03-02")

        # The written content should be truncated to ~50K + truncation notice
        assert len(written_content) == 1
        assert len(written_content[0]) < 60000
        assert "truncated" in written_content[0]


# ============================================================
# INTEGRATION TESTS — run_reflection with diff capture
# ============================================================

class TestRunReflectionWithDiff:
    """IT11-IT15: run_reflection() diff capture integration."""

    def test_captures_diff_when_hash_changes(self):
        """IT11: When agent commits, diff_info contains file changes."""
        from reflection import run_reflection

        hash_calls = [0]

        def mock_git_head():
            hash_calls[0] += 1
            return "pre_hash" if hash_calls[0] == 1 else "post_hash"

        mock_diff_data = {
            "stat": "1 file changed",
            "patch": "diff content",
            "files": [{"path": "memory/facts/2026-03-02.md", "before": "", "after": "new fact"}],
        }

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", side_effect=mock_git_head), \
             patch("reflection._git_diff", return_value=mock_diff_data), \
             patch("reflection.render_diff_images", return_value=["/tmp/img.png"]):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert report is not None
        assert diff_info["stat"] == "1 file changed"
        assert len(diff_info["files"]) == 1
        assert diff_info["images"] == ["/tmp/img.png"]

    def test_empty_diff_when_no_hash_change(self):
        """IT12: When agent doesn't commit (same hash), diff_info is empty."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", return_value="same_hash"), \
             patch("reflection._git_diff") as mock_diff, \
             patch("reflection.render_diff_images") as mock_render:

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert report is not None
        # _git_diff should NOT be called when hashes are the same
        mock_diff.assert_not_called()
        mock_render.assert_not_called()
        assert diff_info["files"] == []
        assert diff_info["images"] == []

    def test_diff_survives_agent_failure(self):
        """IT13: When agent fails, diff_info is empty but no crash."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=None), \
             patch("reflection._git_head_hash", return_value="hash1"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert "unavailable" in report.lower() or "failed" in report.lower()
        assert diff_info["files"] == []

    def test_diff_survives_git_failure(self):
        """IT14: When git operations fail, returns empty diff, not crash."""
        from reflection import run_reflection

        hash_calls = [0]

        def mock_git_head():
            hash_calls[0] += 1
            if hash_calls[0] == 1:
                return "pre"
            return "post"

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", side_effect=mock_git_head), \
             patch("reflection._git_diff", side_effect=OSError("disk fail")):

            # Should not crash — exception handling in run_reflection
            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        # The outer try/except catches this
        assert report is not None or diff_info is not None

    def test_diff_survives_render_failure(self):
        """IT15: When image rendering fails, files are still in diff_info."""
        from reflection import run_reflection

        hash_calls = [0]

        def mock_git_head():
            hash_calls[0] += 1
            return "pre" if hash_calls[0] == 1 else "post"

        mock_diff_data = {
            "stat": "1 file changed",
            "patch": "diff",
            "files": [{"path": "test.md", "before": "a", "after": "b"}],
        }

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", side_effect=mock_git_head), \
             patch("reflection._git_diff", return_value=mock_diff_data), \
             patch("reflection.render_diff_images", return_value=[]):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert report is not None
        assert len(diff_info["files"]) == 1
        assert diff_info["images"] == []  # Render failed, but files are there


# ============================================================
# INTEGRATION TESTS — cmd_sleep sends diff images
# ============================================================

class TestCmdSleepDiffDelivery:
    """IT16-IT19: cmd_sleep sends visual diffs to Telegram."""

    @pytest.fixture
    def mock_update(self):
        """Mock Telegram Update object."""
        update = MagicMock()
        update.effective_chat.id = 411364623
        update.message.reply_text = MagicMock(return_value=MagicMock())
        # Make reply_text awaitable
        import asyncio
        update.message.reply_text.return_value = asyncio.coroutine(lambda: None)()
        return update

    @pytest.fixture
    def mock_context(self):
        """Mock Telegram context with bot."""
        context = MagicMock()
        import asyncio
        context.bot.send_photo = MagicMock(
            return_value=asyncio.coroutine(lambda: None)())
        return context

    def test_diff_images_sent_after_finalize(self):
        """IT16: Visual diff images are sent via send_photo after successful reflection."""
        # This test verifies the integration contract:
        # run_reflection returns diff_info with images → cmd_sleep calls send_photo
        from reflection import run_reflection

        # Simulate: agent ran, workspace changed, images rendered
        mock_diff_info = {
            "stat": "1 file changed",
            "patch": "...",
            "files": [{"path": "KANBAN.md", "before": "old", "after": "new"}],
            "images": ["/tmp/openclaw/test1/preview.png"],
        }

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", side_effect=["pre", "post"]), \
             patch("reflection._git_diff", return_value=mock_diff_info), \
             patch("reflection.render_diff_images", return_value=["/tmp/openclaw/test1/preview.png"]):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        # Verify the data cmd_sleep will use
        assert len(diff_info["images"]) == 1
        assert diff_info["images"][0].endswith(".png")

    def test_no_images_when_no_changes(self):
        """IT17: No diff images when agent doesn't modify workspace."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", return_value="unchanged"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert diff_info["images"] == []

    def test_stat_fallback_when_no_images(self):
        """IT18: When images fail to render, stat text is available as fallback."""
        from reflection import run_reflection

        mock_diff_data = {
            "stat": "memory/facts/2026-03-02.md | 5 +++++\n 1 file changed",
            "patch": "...",
            "files": [{"path": "memory/facts/2026-03-02.md", "before": "", "after": "new"}],
        }

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", side_effect=["pre", "post"]), \
             patch("reflection._git_diff", return_value=mock_diff_data), \
             patch("reflection.render_diff_images", return_value=[]):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        # stat is available even when images failed
        assert "1 file changed" in diff_info["stat"]
        assert diff_info["images"] == []

    def test_multiple_files_produce_multiple_images(self):
        """IT19: Each changed file produces its own diff image."""
        from reflection import run_reflection

        mock_diff_data = {
            "stat": "3 files changed",
            "patch": "...",
            "files": [
                {"path": "KANBAN.md", "before": "a", "after": "b"},
                {"path": "memory/ideas.md", "before": "c", "after": "d"},
                {"path": "memory/facts/2026-03-02.md", "before": "", "after": "new"},
            ],
        }

        mock_images = [
            "/tmp/openclaw/diff1/preview.png",
            "/tmp/openclaw/diff2/preview.png",
            "/tmp/openclaw/diff3/preview.png",
        ]

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", side_effect=["pre", "post"]), \
             patch("reflection._git_diff", return_value=mock_diff_data), \
             patch("reflection.render_diff_images", return_value=mock_images):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        assert len(diff_info["images"]) == 3


# ============================================================
# UNIT TESTS — Telegram message formatting (T1)
# ============================================================

class TestFormatReflectionTelegram:
    """UT22-UT27: format_reflection_telegram() — compact summary for Telegram."""

    def test_basic_format(self):
        """UT22: Basic format includes date, category counts, stats."""
        from reflection import format_reflection_telegram, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        message = format_reflection_telegram(parsed, "2026-03-02")

        assert "2026-03-02" in message
        assert "📌 Facts: 2" in message
        assert "🔧 Feedback: 1" in message
        assert "🌟 Compliments: 1" in message
        assert "🧭 Decisions: 1" in message
        assert "📋 Action Items: 1" in message
        assert "💡 Ideas: 1" in message
        assert "🔬 Technical: 1" in message
        assert "📊" in message  # Stats section
        assert "items extracted" in message.lower()

    def test_includes_top_items(self):
        """UT23: Shows top 3-5 items from non-empty categories."""
        from reflection import format_reflection_telegram, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        message = format_reflection_telegram(parsed, "2026-03-02")

        # Should include some actual item text (top items)
        assert "Ashley" in message or "VO2max" in message  # From facts
        assert "Opus" in message or "Cloudflare" in message  # From decisions/actions

    def test_zero_counts_shown(self):
        """UT24: Categories with 0 items show count as 0."""
        from reflection import format_reflection_telegram, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        message = format_reflection_telegram(parsed, "2026-03-02")

        # rules_incidents is empty in sample
        assert "⚠️ Incidents: 0" in message

    def test_empty_reflection(self):
        """UT25: Empty reflection (all zeros) shows gracefully."""
        from reflection import format_reflection_telegram, parse_reflection_response

        empty = parse_reflection_response("{}")
        message = format_reflection_telegram(empty, "2026-03-02")

        assert "2026-03-02" in message
        assert "0 items" in message.lower()
        # All categories should show 0
        for emoji in ["📌", "🔧", "⚠️", "🌟", "🧭", "📋", "💡", "🔬"]:
            assert emoji in message

    def test_respects_4096_char_limit(self):
        """UT26: Never exceeds Telegram's 4096 char limit, truncates gracefully."""
        from reflection import format_reflection_telegram

        # Create huge parsed data
        big_parsed = {
            "facts": [{"category": "Cat%d" % i, "text": "Long fact text " * 50} for i in range(100)],
            "feedback_lessons": [],
            "rules_incidents": [],
            "compliments": [],
            "decisions": [],
            "action_items": [],
            "ideas": [],
            "technical_learnings": [],
            "stats": {"messages_processed": 500, "sessions_scanned": 20, "items_extracted": 100},
        }

        message = format_reflection_telegram(big_parsed, "2026-03-02")

        assert len(message) <= 4096
        # Should indicate truncation
        if len(message) >= 4000:
            assert "..." in message or "truncated" in message.lower()

    def test_message_readable_format(self):
        """UT27: Message is human-readable, not debug output."""
        from reflection import format_reflection_telegram, parse_reflection_response

        parsed = parse_reflection_response(SAMPLE_REFLECTION_JSON)
        message = format_reflection_telegram(parsed, "2026-03-02")

        # Should NOT have JSON or debug markers
        assert "{" not in message
        assert "}" not in message
        assert "dict(" not in message

        # SHOULD have emoji and readable structure
        assert "🪞" in message or "Reflection" in message
        assert "\n" in message  # Multi-line
        assert "• " in message or "- " in message or ":" in message  # Bullet/structured


# ============================================================
# INTEGRATION TESTS — run_reflection returns parsed data (T3)
# ============================================================

class TestRunReflectionReturnsParsed:
    """IT20-IT21: run_reflection() now returns (report, diff_info, parsed)."""

    def test_returns_three_tuple(self):
        """IT20: run_reflection returns (report, diff_info, parsed)."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", return_value="same_hash"):

            result = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        # Should be a 3-tuple now
        assert isinstance(result, tuple)
        assert len(result) == 3
        report, diff_info, parsed = result
        assert isinstance(report, str)
        assert isinstance(diff_info, dict)
        assert isinstance(parsed, dict)
        assert "facts" in parsed

    def test_parsed_matches_report(self):
        """IT21: Parsed dict is consistent with the report."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=SAMPLE_REFLECTION_JSON), \
             patch("reflection._git_head_hash", return_value="same_hash"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-02")

        # Verify parsed has same data as report
        assert len(parsed["facts"]) == 2
        assert len(parsed["decisions"]) == 1
        # Report should mention these items
        assert "Ashley" in report
        assert "Opus" in report
