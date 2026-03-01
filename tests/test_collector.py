"""
Tests for collector.py — Message extraction, filtering, and formatting.

Key regressions tested:
- User metadata stripping (OpenClaw wraps messages with conversation info)
- Noise filtering (heartbeat_ok, no_reply, system messages)
- Tool output filtering (CLI output must not appear in digests)
- Boyang's messages NEVER truncated
- Assistant messages truncated at MAX_ASSISTANT_LENGTH
- Cron sessions skipped
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector import (
    extract_user_text,
    extract_assistant_text,
    _is_tool_output,
    extract_messages,
    format_messages,
    group_by_session,
    NOISE_EXACT,
    NOISE_PREFIXES,
    TOOL_OUTPUT_PATTERNS,
)
from config import SGT, MAX_ASSISTANT_LENGTH
from tests.conftest import make_message_entry, make_assistant_entry_list


# ============================================================
# extract_user_text — strips OpenClaw metadata wrappers
# ============================================================

class TestExtractUserText:
    """OpenClaw wraps Telegram messages with metadata. We must strip it."""

    def test_strips_conversation_info_block(self):
        raw = (
            'Conversation info (untrusted metadata):\n```json\n'
            '{"message_id": "123", "sender_id": "411364623"}\n```\n\n'
            'Sender (untrusted metadata):\n```json\n'
            '{"label": "Boyang", "name": "Boyang"}\n```\n\n'
            'Hello, how are you?'
        )
        result = extract_user_text(raw)
        assert result == "Hello, how are you?"

    def test_plain_text_unchanged(self):
        assert extract_user_text("Just a simple message") == "Just a simple message"

    def test_multiline_message_preserved(self):
        raw = (
            'Conversation info (untrusted metadata):\n```json\n'
            '{"message_id": "1"}\n```\n\n'
            'Sender (untrusted metadata):\n```json\n'
            '{"label": "Boyang"}\n```\n\n'
            'Line one\nLine two\nLine three'
        )
        result = extract_user_text(raw)
        assert "Line one" in result
        assert "Line two" in result
        assert "Line three" in result

    def test_empty_string(self):
        assert extract_user_text("") == ""

    def test_metadata_only_returns_empty(self):
        raw = (
            'Conversation info (untrusted metadata):\n```json\n'
            '{"message_id": "1"}\n```\n\n'
            'Sender (untrusted metadata):\n```json\n'
            '{"label": "Boyang"}\n```\n'
        )
        result = extract_user_text(raw)
        # Should be empty or whitespace-only after stripping
        assert result.strip() == "" or len(result) < 5

    def test_replied_message_context_stripped(self):
        raw = (
            'Conversation info (untrusted metadata):\n```json\n'
            '{"message_id": "1"}\n```\n\n'
            'Sender (untrusted metadata):\n```json\n'
            '{"label": "Boyang"}\n```\n\n'
            'Replied message (untrusted metadata):\n```json\n'
            '{"text": "some old msg"}\n```\n\n'
            'My actual reply'
        )
        result = extract_user_text(raw)
        assert "My actual reply" in result
        # Metadata should not leak
        assert "untrusted" not in result


# ============================================================
# extract_assistant_text — handles string and list content
# ============================================================

class TestExtractAssistantText:

    def test_string_content(self):
        assert extract_assistant_text("Hello world") == "Hello world"

    def test_list_content_extracts_text_blocks(self):
        content = [
            {"type": "tool_use", "id": "t1", "name": "exec", "input": {}},
            {"type": "text", "text": "Here's the result."},
            {"type": "tool_result", "tool_use_id": "t1", "content": "output"},
            {"type": "text", "text": "All done."},
        ]
        result = extract_assistant_text(content)
        assert "Here's the result." in result
        assert "All done." in result
        # Tool blocks should NOT appear
        assert "tool_use" not in result
        assert "exec" not in result

    def test_empty_list(self):
        assert extract_assistant_text([]) == ""

    def test_list_with_no_text_blocks(self):
        content = [{"type": "tool_use", "id": "t1", "name": "exec", "input": {}}]
        assert extract_assistant_text(content) == ""

    def test_none_content(self):
        assert extract_assistant_text(None) == ""

    def test_empty_string(self):
        assert extract_assistant_text("") == ""


# ============================================================
# Noise filtering
# ============================================================

class TestNoiseFiltering:
    """Messages matching noise patterns must be excluded from digests."""

    def test_noise_exact_entries(self):
        """All exact-match noise tokens are defined."""
        assert "heartbeat_ok" in NOISE_EXACT
        assert "no_reply" in NOISE_EXACT
        assert "announce_skip" in NOISE_EXACT
        assert "ack" in NOISE_EXACT

    def test_noise_prefixes_entries(self):
        """All prefix-match noise patterns are defined."""
        prefix_strs = [p for p in NOISE_PREFIXES]
        assert any("[System Message]" in p for p in prefix_strs)
        assert any("[Nightly Nudger" in p for p in prefix_strs)
        assert any("[DAILY_DIGEST]" in p for p in prefix_strs)
        assert any("[DIGEST_SUMMARY_REQUEST]" in p for p in prefix_strs)

    def test_heartbeat_ok_case_insensitive(self):
        """Noise check is case-insensitive (lowered before comparison)."""
        assert "heartbeat_ok" in NOISE_EXACT
        assert "HEARTBEAT_OK".lower() in NOISE_EXACT


# ============================================================
# Tool output detection
# ============================================================

class TestToolOutputDetection:
    """Tool/CLI output must not appear in digests.
    
    Regression: early versions showed raw session listing metadata
    and CLI output in digest messages.
    """

    def test_session_listing_is_tool_output(self):
        text = (
            "Session store:\n"
            "Sessions listed:\n"
            "Kind Key Age Model\n"
            "agent:main:telegram:group:-5125187430 12h claude-opus-4-6"
        )
        assert _is_tool_output(text)

    def test_launchctl_is_tool_output(self):
        text = "launchctl list com.digest-bot\nPID\tStatus\tLabel\n1234\t0\tcom.digest-bot"
        assert _is_tool_output(text)

    def test_file_edit_is_tool_output(self):
        text = "Successfully replaced text in /Users/claw/digest-bot/llm.py"
        assert _is_tool_output(text)

    def test_normal_conversation_is_not_tool_output(self):
        text = "The weather in Singapore is 28°C and sunny today."
        assert not _is_tool_output(text)

    def test_short_response_not_flagged(self):
        text = "Done!"
        assert not _is_tool_output(text)

    def test_mixed_content_threshold(self):
        """If >30% of lines are tool output, skip the whole message."""
        text = (
            "Here's what I found:\n"
            "Session store: loaded\n"
            "Exit: 0\n"
            "=== Status ===\n"
            "Everything looks good."
        )
        # 3 out of 5 lines (60%) are tool patterns
        assert _is_tool_output(text)

    def test_below_threshold_not_flagged(self):
        """If only a small fraction matches, keep it."""
        text = (
            "I checked the configuration and found that the settings are correct.\n"
            "The timezone is set to SGT (UTC+8).\n"
            "The digest runs at 22:30 every night.\n"
            "The nudge interval is 30 minutes.\n"
            "Everything is working as expected."
        )
        assert not _is_tool_output(text)

    def test_tool_patterns_comprehensive(self):
        """Verify all expected patterns are in the list."""
        patterns_to_check = [
            "Session store:",
            "Sessions listed:",
            "Exit: ",
            "=== ",
            "Successfully replaced text in",
            "Successfully wrote",
            "HTTP Request: POST https://api.telegram.org",
        ]
        for p in patterns_to_check:
            assert p in TOOL_OUTPUT_PATTERNS, f"Missing tool pattern: {p}"


# ============================================================
# extract_messages — JSONL parsing with timestamp filtering
# ============================================================

class TestExtractMessages:

    def test_extracts_messages_after_timestamp(self, transcript_dir):
        """Only messages after since_ts should be returned."""
        now = datetime.now(SGT)
        t1 = (now - timedelta(hours=3)).isoformat()
        t2 = (now - timedelta(hours=1)).isoformat()
        t3 = now.isoformat()

        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("\n".join([
            make_message_entry("user", "Old message", t1),
            make_message_entry("user", "Recent message", t2),
            make_message_entry("user", "Latest message", t3),
        ]) + "\n")

        since = now - timedelta(hours=2)
        msgs = extract_messages(filepath, since)
        texts = [m["text"] for m in msgs]
        assert "Old message" not in texts
        assert "Recent message" in texts
        assert "Latest message" in texts

    def test_filters_noise_messages(self, transcript_dir):
        """Noise messages (heartbeat_ok, no_reply) should be skipped."""
        now = datetime.now(SGT)
        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("\n".join([
            make_message_entry("assistant", "HEARTBEAT_OK", now.isoformat()),
            make_message_entry("assistant", "NO_REPLY", now.isoformat()),
            make_message_entry("user", "Real message", now.isoformat()),
        ]) + "\n")

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Real message"

    def test_filters_system_messages(self, transcript_dir):
        """System messages and digest triggers should be skipped."""
        now = datetime.now(SGT)
        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("\n".join([
            make_message_entry("user", "[System Message] Config updated", now.isoformat()),
            make_message_entry("user", "[DAILY_DIGEST] Generate summary", now.isoformat()),
            make_message_entry("user", "Actual question", now.isoformat()),
        ]) + "\n")

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Actual question"

    def test_truncates_assistant_not_boyang(self, transcript_dir):
        """CRITICAL: Boyang's messages are NEVER truncated. Assistant messages are."""
        now = datetime.now(SGT)
        long_text = "A" * 5000  # Exceeds MAX_ASSISTANT_LENGTH (4000)

        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("\n".join([
            make_message_entry("user", long_text, now.isoformat()),
            make_message_entry("assistant", long_text, now.isoformat()),
        ]) + "\n")

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        user_msg = next(m for m in msgs if m["role"] == "user")
        asst_msg = next(m for m in msgs if m["role"] == "assistant")

        # Boyang's message: FULL, never truncated
        assert len(user_msg["text"]) == 5000

        # Assistant: truncated at MAX_ASSISTANT_LENGTH
        assert len(asst_msg["text"]) <= MAX_ASSISTANT_LENGTH + 100  # +100 for truncation marker

    def test_filters_tool_output(self, transcript_dir):
        """Assistant messages that are tool/CLI output should be filtered."""
        now = datetime.now(SGT)
        tool_text = "Session store:\nSessions listed:\nKind Key Age Model\nagent:main:telegram 12h claude-opus-4-6"

        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("\n".join([
            make_message_entry("assistant", tool_text, now.isoformat()),
            make_message_entry("assistant", "Here's your answer.", now.isoformat()),
        ]) + "\n")

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        texts = [m["text"] for m in msgs]
        assert "Session store:" not in str(texts)
        assert "Here's your answer." in texts

    def test_skips_non_message_types(self, transcript_dir):
        """Only entries with type=message should be processed."""
        now = datetime.now(SGT)
        filepath = transcript_dir / "test.jsonl"
        entries = [
            json.dumps({"type": "system", "timestamp": now.isoformat(),
                         "message": {"role": "system", "content": "System prompt"}}),
            make_message_entry("user", "Real msg", now.isoformat()),
        ]
        filepath.write_text("\n".join(entries) + "\n")

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        assert len(msgs) == 1

    def test_handles_list_content_assistant(self, transcript_dir):
        """Assistant messages with list content (tool_use + text blocks)."""
        now = datetime.now(SGT)
        filepath = transcript_dir / "test.jsonl"
        filepath.write_text(
            make_assistant_entry_list("The result is 42.", now.isoformat()) + "\n"
        )

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        assert len(msgs) == 1
        assert "The result is 42." in msgs[0]["text"]

    def test_handles_malformed_jsonl(self, transcript_dir):
        """Malformed lines should be skipped, not crash."""
        now = datetime.now(SGT)
        filepath = transcript_dir / "test.jsonl"
        filepath.write_text(
            "NOT VALID JSON\n" +
            make_message_entry("user", "Valid message", now.isoformat()) + "\n"
        )

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Valid message"

    def test_empty_file(self, transcript_dir):
        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("")
        msgs = extract_messages(filepath, datetime.now(SGT) - timedelta(hours=1))
        assert msgs == []

    def test_nonexistent_file(self, transcript_dir):
        msgs = extract_messages(transcript_dir / "nonexistent.jsonl", datetime.now(SGT))
        assert msgs == []

    def test_compaction_flush_filtered(self, transcript_dir):
        """Pre-compaction memory flush messages should be filtered."""
        now = datetime.now(SGT)
        filepath = transcript_dir / "test.jsonl"
        filepath.write_text("\n".join([
            make_message_entry("assistant", "Pre-compaction memory flush: saving state", now.isoformat()),
            make_message_entry("user", "Real question", now.isoformat()),
        ]) + "\n")

        msgs = extract_messages(filepath, now - timedelta(hours=1))
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Real question"


# ============================================================
# format_messages — markdown output
# ============================================================

class TestFormatMessages:

    def test_formats_user_and_assistant(self):
        msgs = [
            {"time_str": "09:00", "role": "user", "text": "Hello"},
            {"time_str": "09:01", "role": "assistant", "text": "Hi there"},
        ]
        result = format_messages(msgs)
        assert "**Boyang:**" in result
        assert "**Doudou:**" in result
        assert "09:00" in result
        assert "Hello" in result
        assert "Hi there" in result

    def test_empty_messages(self):
        result = format_messages([])
        assert "No conversations" in result


# ============================================================
# group_by_session — grouping
# ============================================================

class TestGroupBySession:

    def test_groups_correctly(self):
        msgs = [
            {"session": "CLAW 003", "text": "a"},
            {"session": "DM", "text": "b"},
            {"session": "CLAW 003", "text": "c"},
        ]
        groups = group_by_session(msgs)
        assert len(groups) == 2
        assert len(groups["CLAW 003"]) == 2
        assert len(groups["DM"]) == 1

    def test_empty_input(self):
        assert group_by_session([]) == {}


# ============================================================
# get_all_session_transcripts — session discovery
# ============================================================

class TestGetAllSessionTranscripts:

    def test_skips_cron_sessions(self, populated_transcripts):
        """Cron sessions must be excluded from digests."""
        with patch("collector.SESSIONS_JSON", populated_transcripts / "sessions.json"), \
             patch("collector.SESSION_DIR", populated_transcripts):
            from collector import get_all_session_transcripts
            sessions = get_all_session_transcripts()
            names = [name for name, _ in sessions]
            assert not any("cron" in n.lower() for n in names)

    def test_dm_labeled_correctly(self, populated_transcripts):
        with patch("collector.SESSIONS_JSON", populated_transcripts / "sessions.json"), \
             patch("collector.SESSION_DIR", populated_transcripts):
            from collector import get_all_session_transcripts
            sessions = get_all_session_transcripts()
            names = [name for name, _ in sessions]
            assert "DM with Boyang" in names

    def test_webchat_labeled(self, populated_transcripts):
        with patch("collector.SESSIONS_JSON", populated_transcripts / "sessions.json"), \
             patch("collector.SESSION_DIR", populated_transcripts):
            from collector import get_all_session_transcripts
            sessions = get_all_session_transcripts()
            names = [name for name, _ in sessions]
            assert "Webchat" in names
