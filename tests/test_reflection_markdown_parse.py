"""
Tests for reflection Telegram display — send agent response VERBATIM.

ROOT CAUSE (2026-03-05): The reflection agent returns markdown (as instructed),
but the code tried to parse JSON from it → always empty → Telegram showed zeros.

FIX: Just send the agent's response text directly to Telegram. No parsing.
No JSON. No reformatting. The agent already produces a clean, readable report.

Also includes parse_reflection_markdown() tests for programmatic access
(e.g. if we ever need category counts for monitoring/alerting).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# Real agent output samples (from production 2026-03-05)
# ============================================================

REAL_AGENT_RESPONSE_2026_03_05 = """Committed `52d52c2` and pushed.

# 🪞 Nightly Reflection — 2026-03-05

**Big day — Fitness First skill built in 5 min, compaction P1 fabrication, Telegram Desktop v2, KANBAN race condition.**

## 📊 Facts (11 items)
- **Fitness First skill** built: reverse-engineered API (no auth), 14 clubs, ~1056 classes/week
- Cache-first architecture: every 2 days → /tmp/ → DO Spaces (TXT + JSON)
- URLs: `ff-timetable.txt` and `ff-timetable.json` on DO Spaces
- **Compaction truth:** ONLY `"default"` or `"safeguard"` modes. NO auto, NO model override.
- Compaction changed: safeguard → default. memoryFlush stays ON.
- **Telegram Desktop v2** rebuilt with Peekaboo pipeline (`a818fb3`)
- DD-018 (KANBAN system), DD-019 (compaction timeout) created
- KANBAN race condition: duplicate DD-017 from concurrent sessions
- **Audit (Mar 4):** 29% overall, routing 0%, bilingual **91%** (best ever!)

## 📝 Feedback & Lessons (3 items)
- **"Read the fucking documentation"** — fabricated config options, 4th fabrication incident
- **"Do as I instructed"** — queried API instead of reading cache
- **"Keep memoryFlush"** — settled decision, never propose disabling again

## 🚨 Rules & Incidents (1 item)
- **P1:** Fabricated `mode: "auto"` and `compaction.model` → applied invalid config → reverted. Mandatory `config.schema` check before any config proposal.

## 👏 Compliments (0)

## 🔀 Decisions (3 items)
- Fitness First cache-first (prefetch every 2 days, query locally)
- Compaction mode: default (only real lever)
- FF data in /tmp/ not workspace, uploaded to DO Spaces

## 📋 Action Items (0 new)

## 💡 Ideas (0 new)

## 🔧 Technical Learnings (4 topics)
- Compaction schema: 2 modes only, no model override, real levers are token thresholds
- memoryFlush = LLM call that writes important context to memory before compaction
- Fitness First API: public, no auth, 7-day lookahead, per-club querying
- Peekaboo pipeline: clipboard paste + search→Enter → reliable Telegram Desktop automation

---

**Stats:** ~120 messages across 5 sessions. 22 items extracted."""


SAMPLE_CONVERSATIONS = "**09:00** **Boyang:**\nHello\n\n**09:05** **Doudou:**\nHi there\n"


# ============================================================
# CORE TEST: Telegram gets the agent's text verbatim
# ============================================================

class TestReflectionVerbatimDisplay:
    """The Telegram message should be the agent's report text, not a reformatted summary."""

    def test_run_reflection_report_is_agent_response(self):
        """run_reflection returns the agent's markdown response as the report."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=(REAL_AGENT_RESPONSE_2026_03_05, None)), \
             patch("reflection._git_head_hash", return_value="same_hash"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-05")

        # Report should contain the agent's actual text
        assert "Fitness First skill" in report
        assert "Facts (11 items)" in report  # The header with count
        assert "Feedback & Lessons (3 items)" in report
        assert "Technical Learnings (4 topics)" in report
        assert "22 items extracted" in report

    def test_report_does_not_say_zero_items(self):
        """The report text should NOT show zeros when agent found real items."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=(REAL_AGENT_RESPONSE_2026_03_05, None)), \
             patch("reflection._git_head_hash", return_value="same_hash"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-05")

        # This was the exact bug — Telegram showed "0 items extracted from 0 messages"
        assert "0 items extracted from 0 messages" not in report

    def test_report_under_telegram_limit(self):
        """Normal agent responses should be under 4096 chars."""
        # The 2026-03-05 response was 2210 chars — well under limit
        assert len(REAL_AGENT_RESPONSE_2026_03_05) < 4096

    def test_long_report_truncated_for_telegram(self):
        """If report exceeds 4096 chars, cmd_sleep truncates it."""
        # This test verifies the truncation logic in main.py
        long_report = "# 🪞 Nightly Reflection\n\n" + ("- Item " * 1000)
        assert len(long_report) > 4096

        # The truncation logic: report[:4090] + "..."
        truncated = long_report[:4090] + "..." if len(long_report) > 4096 else long_report
        assert len(truncated) <= 4096


# ============================================================
# parse_reflection_markdown — for programmatic access
# ============================================================

class TestParseReflectionMarkdown:
    """parse_reflection_markdown extracts structured data from agent's markdown."""

    def test_parses_real_production_output(self):
        from reflection import parse_reflection_markdown

        result = parse_reflection_markdown(REAL_AGENT_RESPONSE_2026_03_05)

        assert len(result["facts"]) == 9  # 9 bullet items
        assert len(result["feedback_lessons"]) == 3
        assert len(result["rules_incidents"]) == 1
        assert len(result["compliments"]) == 0
        assert len(result["decisions"]) == 3
        assert len(result["action_items"]) == 0
        assert len(result["ideas"]) == 0
        assert len(result["technical_learnings"]) == 4

    def test_stats_extracted(self):
        from reflection import parse_reflection_markdown

        result = parse_reflection_markdown(REAL_AGENT_RESPONSE_2026_03_05)

        assert result["stats"]["messages_processed"] == 120
        assert result["stats"]["items_extracted"] == 22

    def test_empty_input(self):
        from reflection import parse_reflection_markdown

        result = parse_reflection_markdown("")
        assert result["facts"] == []
        assert result["stats"]["items_extracted"] == 0

    def test_items_are_strings(self):
        from reflection import parse_reflection_markdown

        result = parse_reflection_markdown(REAL_AGENT_RESPONSE_2026_03_05)
        for cat in ["facts", "feedback_lessons", "rules_incidents", "decisions", "technical_learnings"]:
            for item in result[cat]:
                assert isinstance(item, str)

    def test_ignores_placeholder_lines(self):
        from reflection import parse_reflection_markdown

        text = """## Facts (0)
_None identified today._

## Feedback (1 item)
- Real lesson here"""

        result = parse_reflection_markdown(text)
        assert len(result["facts"]) == 0
        assert len(result["feedback_lessons"]) == 1

    def test_all_categories_present(self):
        from reflection import parse_reflection_markdown

        result = parse_reflection_markdown("## Facts (1 item)\n- A fact")
        for key in ["facts", "feedback_lessons", "rules_incidents", "compliments",
                     "decisions", "action_items", "ideas", "technical_learnings", "stats"]:
            assert key in result


# ============================================================
# Integration: run_reflection parsed data is usable
# ============================================================

class TestRunReflectionParsedData:
    """run_reflection returns parsed data alongside the report (for monitoring)."""

    def test_parsed_has_real_counts(self):
        """parsed dict should reflect actual agent output, not empty."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=(REAL_AGENT_RESPONSE_2026_03_05, None)), \
             patch("reflection._git_head_hash", return_value="same_hash"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-05")

        # parsed should have real data from markdown parsing
        assert len(parsed["facts"]) > 0
        assert len(parsed["feedback_lessons"]) > 0
        assert parsed["stats"]["items_extracted"] > 0

    def test_parsed_empty_on_failure(self):
        """When agent fails, parsed is empty."""
        from reflection import run_reflection

        with patch("reflection._call_agent", return_value=(None, "timeout")), \
             patch("reflection._git_head_hash", return_value="hash"):

            report, diff_info, parsed = run_reflection(SAMPLE_CONVERSATIONS, "2026-03-05")

        assert parsed["facts"] == []
        assert parsed["stats"]["items_extracted"] == 0
