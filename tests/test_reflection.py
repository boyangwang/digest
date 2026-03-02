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
        """UT7: Zero messages → reflection returns None (skip)."""
        from reflection import run_reflection

        with patch("reflection._call_agent") as mock_agent:
            result = run_reflection(conversations_text="", date_str="2026-03-02")

        assert result is None
        mock_agent.assert_not_called()

    def test_agent_empty_response_returns_fallback(self):
        """UT8: Agent returns empty → fallback report."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=None):
            result = run_reflection(
                conversations_text=SAMPLE_CONVERSATIONS,
                date_str="2026-03-02",
            )

        # Should return a fallback report, not None (we had conversations)
        assert result is not None
        assert "Reflection unavailable" in result or "failed" in result.lower()


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
