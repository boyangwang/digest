"""
Tests for llm.py — LLM integration via Doudou (OpenClaw agent).

Key regressions tested:
- Summaries go through Doudou, NOT bare OpenAI API (P1 incident)
- Conversations saved to Obsidian vault path, not /tmp/ (design decision)
- File-based handoff: conversations written to file, Doudou reads via tool
- Graceful fallback when Doudou is unavailable
- compose_summary prompt includes file path, not raw text
- compose_nudge works independently
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import llm


# ============================================================
# _save_conversations_to_file
# ============================================================

class TestSaveConversationsToFile:

    def test_saves_to_obsidian_vault_path(self, tmp_path):
        """CRITICAL: Files saved to Obsidian vault, not /tmp/."""
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")):
            filepath = llm._save_conversations_to_file("Hello world")
        assert filepath.startswith(str(tmp_path))
        assert os.path.exists(filepath)

    def test_file_contains_full_text(self, tmp_path):
        text = "**09:00** **Boyang:**\nHello\n\n**09:01** **Doudou:**\nHi there"
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")):
            filepath = llm._save_conversations_to_file(text)
        with open(filepath) as f:
            content = f.read()
        assert content == text

    def test_naming_convention(self, tmp_path):
        """Files named conv-YYYYMMDD-HHMMSS.md."""
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")):
            filepath = llm._save_conversations_to_file("Text")
        name = os.path.basename(filepath)
        assert name.startswith("conv-")
        assert name.endswith(".md")

    def test_creates_directory(self, tmp_path):
        """Should create transcripts directory if it doesn't exist."""
        target = str(tmp_path / "new" / "transcripts")
        with patch.object(llm, "CONV_DUMP_DIR", target):
            filepath = llm._save_conversations_to_file("Text")
        assert os.path.exists(filepath)

    def test_unicode_content(self, tmp_path):
        text = "中文对话 🌙 Japanese: 日本語"
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")):
            filepath = llm._save_conversations_to_file(text)
        with open(filepath, encoding="utf-8") as f:
            assert "中文对话" in f.read()

    def test_large_content(self, tmp_path):
        """Should handle large conversation dumps without truncation."""
        text = "A" * 100000  # 100KB — no truncation
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")):
            filepath = llm._save_conversations_to_file(text)
        with open(filepath) as f:
            assert len(f.read()) == 100000

    def test_vault_path_not_tmp(self):
        """REGRESSION: CONV_DUMP_DIR must be in Obsidian vault, not /tmp/."""
        assert "/tmp" not in llm.CONV_DUMP_DIR
        assert "NotesVault" in llm.CONV_DUMP_DIR or "Obsidian" in llm.CONV_DUMP_DIR or "Doudou-Digest" in llm.CONV_DUMP_DIR


# ============================================================
# compose_summary
# ============================================================

class TestComposeSummary:

    def test_empty_input_returns_placeholder(self):
        result = llm.compose_summary("")
        assert "No conversations" in result

    def test_whitespace_only_returns_placeholder(self):
        result = llm.compose_summary("   \n\n  ")
        assert "No conversations" in result

    def test_calls_doudou_with_file_path(self, tmp_path):
        """Summary should reference a file path, not embed raw conversation text."""
        mock_result = "A productive day of engineering work.\n\n今天工程工作很有成效。"

        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")), \
             patch.object(llm, "_ask_doudou", return_value=mock_result) as mock_ask:
            result = llm.compose_summary("**09:00** **Boyang:**\nHello")

        assert result == mock_result
        # Verify the prompt contains a file path, not raw conversation text
        call_args = mock_ask.call_args[0][0]
        assert "/transcripts/conv-" in call_args
        assert "Read the conversation transcript at:" in call_args
        # The actual conversation text should NOT be in the prompt
        assert "**09:00** **Boyang:**" not in call_args

    def test_saves_file_before_calling_doudou(self, tmp_path):
        """File must exist before Doudou is called (so it can read it)."""
        saved_files = []

        def mock_ask(prompt, timeout=120):
            # Check that the file exists when Doudou is called
            # Extract path from prompt
            for line in prompt.split("\n"):
                if "transcripts/conv-" in line:
                    path = line.split("at: ")[-1].strip()
                    saved_files.append(os.path.exists(path))
            return "Summary"

        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")), \
             patch.object(llm, "_ask_doudou", side_effect=mock_ask):
            llm.compose_summary("Some text")

        assert saved_files and saved_files[0] is True

    def test_fallback_when_doudou_unavailable(self, tmp_path):
        """When Doudou fails, should return a fallback message."""
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")), \
             patch.object(llm, "_ask_doudou", return_value=None):
            result = llm.compose_summary("**Boyang:** Hello\n**Doudou:** Hi")

        assert "recorded" in result or "pending" in result

    def test_no_bare_openai_api(self):
        """REGRESSION: Must use Doudou (OpenClaw), not bare OpenAI API.
        
        Incident: originally used bare gpt-4o-mini for summaries instead of
        routing through Doudou who has full conversation context.
        """
        import inspect
        source = inspect.getsource(llm)
        # Should NOT contain direct OpenAI API calls
        assert "openai" not in source.lower() or "openai" in source.lower().split("not")[0] if "not" in source.lower() else "openai" not in source.lower()
        assert "gpt-4o" not in source
        assert "gpt-3.5" not in source
        assert "api.openai.com" not in source

    def test_prompt_includes_digest_instructions(self, tmp_path):
        """Prompt should tell Doudou to write bilingual, reflective summary."""
        with patch.object(llm, "CONV_DUMP_DIR", str(tmp_path / "transcripts")), \
             patch.object(llm, "_ask_doudou", return_value="Summary") as mock_ask:
            llm.compose_summary("Some conversations")

        prompt = mock_ask.call_args[0][0]
        assert "bilingual" in prompt.lower()
        assert "journal" in prompt.lower() or "summary" in prompt.lower()
        assert "DIGEST_SUMMARY_REQUEST" in prompt


# ============================================================
# compose_nudge
# ============================================================

class TestComposeNudge:

    def test_returns_text_on_success(self):
        mock_nudge = "Time for bed! 🌙\n该睡了！"
        with patch.object(llm, "_ask_doudou", return_value=mock_nudge):
            result = llm.compose_nudge("It's 23:00 SGT.")
        assert result == mock_nudge

    def test_fallback_on_failure(self):
        with patch.object(llm, "_ask_doudou", return_value=None):
            result = llm.compose_nudge()
        assert "🌙" in result
        assert "/sleep" in result

    def test_nudge_timeout_shorter(self):
        """Nudge should use a shorter timeout than summary."""
        with patch.object(llm, "_ask_doudou", return_value="Nudge") as mock_ask:
            llm.compose_nudge()
        _, kwargs = mock_ask.call_args
        assert kwargs.get("timeout", 120) <= 60


# ============================================================
# _ask_doudou — CLI integration
# ============================================================

class TestAskDoudou:

    def test_uses_openclaw_agent_cli(self):
        """Must use 'openclaw agent --local', not 'openclaw sessions send'."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"payloads": [{"text": "Response"}]}),
                stderr="",
            )
            llm._ask_doudou("Test prompt")

        args = mock_run.call_args[0][0]
        assert "openclaw" in args
        assert "agent" in args
        assert "--local" in args
        assert "--json" in args
        # Should NOT use "sessions send" (broken, doesn't exist)
        assert "sessions" not in args

    def test_session_id_is_digest_bot(self):
        """Agent session should be 'digest-bot'."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"payloads": [{"text": "Response"}]}),
                stderr="",
            )
            llm._ask_doudou("Test")

        args = mock_run.call_args[0][0]
        session_idx = args.index("--session-id")
        assert args[session_idx + 1] == "digest-bot"

    def test_handles_timeout(self):
        with patch("subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("cmd", 120)):
            result = llm._ask_doudou("Test", timeout=1)
        assert result is None

    def test_handles_bad_json(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="NOT JSON", stderr="")
            result = llm._ask_doudou("Test")
        assert result is None

    def test_handles_nonzero_exit(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error occurred")
            result = llm._ask_doudou("Test")
        assert result is None

    def test_handles_empty_payloads(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"payloads": []}),
                stderr="",
            )
            result = llm._ask_doudou("Test")
        assert result is None

    def test_path_includes_homebrew(self):
        """PATH must include /opt/homebrew/bin for openclaw CLI."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"payloads": [{"text": "OK"}]}),
                stderr="",
            )
            llm._ask_doudou("Test")

        env = mock_run.call_args[1].get("env", {})
        assert "/opt/homebrew/bin" in env.get("PATH", "")


# ============================================================
# _fallback
# ============================================================

class TestFallback:

    def test_counts_speakers(self):
        text = "**Boyang:** hi\n**Doudou:** hello\n**Boyang:** bye"
        result = llm._fallback(text)
        assert "2" in result  # 2 from Boyang
        assert "1" in result  # 1 from Doudou

    def test_bilingual_fallback(self):
        result = llm._fallback("**Boyang:** test")
        # Should have both English and Chinese
        assert any(ord(c) > 0x4e00 for c in result)  # Has CJK characters
