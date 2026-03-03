"""
Reflection — Nightly knowledge extraction from conversations.

Follows the same architecture as llm.py:
  1. Save conversation data to file
  2. Call `openclaw agent --local` subprocess with reflection prompt
  3. Agent reads file, extracts 8 categories, writes to workspace
  4. Parse structured JSON response
  5. Format as markdown report for digest append

SPEC-REFLECT-01..06 compliance.
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from config import SGT

logger = logging.getLogger("digest-bot.reflection")

# Conversation dumps for reflection (same location as llm.py transcripts)
CONV_DUMP_DIR = "/Users/claw/Documents/NotesVault/Artificial-Colloquia/Doudou-Digest/transcripts"

# Reflection agent session ID (separate from digest-bot summary session)
REFLECTION_SESSION_ID = "digest-bot-reflection"

# Model — always Opus (PRD decision: quality > cost for persistent knowledge)
REFLECTION_MODEL = "anthropic/claude-opus-4-6"


# ============================================================
# Data preparation
# ============================================================

def _save_conversations(conversations_text: str, date_str: str) -> str:
    """Save conversation text to file for agent to read.

    Returns file path. Files persist in transcripts/ for debugging.
    """
    os.makedirs(CONV_DUMP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filepath = os.path.join(CONV_DUMP_DIR, "reflection-%s-%s.md" % (date_str, timestamp))
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(conversations_text)
    logger.info("Saved %d chars to %s" % (len(conversations_text), filepath))
    return filepath


# ============================================================
# Prompt building
# ============================================================

def build_reflection_prompt(conversations_file: str, date_str: str) -> str:
    """Build the reflection prompt for the Opus agent.

    The agent will:
    1. Read the conversation file
    2. Read existing workspace memory files (for deduplication)
    3. Extract 8 categories
    4. Write to workspace files directly
    5. Return structured JSON summary
    """
    return """[NIGHTLY_REFLECTION] Date: %s

Read the conversation transcript at: %s

Extract structured knowledge into these 8 categories. For each item, check existing
workspace files to avoid duplicates (use memory_search or read the target files).

## Categories

1. **facts** — Durable facts (people, places, companies, numbers, dates, relationships).
   Write to: memory/facts/%s.md
   Format: - **[Category/Entity]** Fact statement

2. **feedback_lessons** — Corrections Boyang gave, behavior/output preferences.
   Write to: memory/feedback-lessons.md (append with date header)
   Also: auto-apply to RULES.md if correction is clear and unambiguous.

3. **rules_incidents** — Formalized rules, near-misses, mistakes, patterns to prevent.
   Write to: INCIDENTS.md ONLY. Do NOT modify RULES.md — propose rule changes in the JSON output instead.

4. **compliments** — Positive feedback, things that went well, praise.
   Write to: memory/compliments.md (append with date header)

5. **decisions** — Choices made and WHY (rationale matters more than the decision).
   Write to: memory/decisions/%s.md

6. **action_items** — Promises, tasks planned, follow-ups. Check KANBAN.md for duplicates.
   Write to: KANBAN.md (append to Todo section)

7. **ideas** — Ideas mentioned in passing, creative suggestions, future possibilities.
   Write to: memory/ideas.md (append with date header)

8. **technical_learnings** — Tools, API quirks, debugging insights, architecture patterns.
   Write to: memory/%s.md (append) and TOOLS.md if critical.

## Instructions

- Read the conversation file first, then extract.
- For EACH category, check the target file for duplicates before adding.
- Write all extracted items to the workspace files directly.
- Do NOT modify RULES.md directly. Propose any rule changes in the JSON output.
- Run: git add -A && git commit -m "nightly-reflection: %s" && git push origin main
- Then return a JSON summary (and ONLY JSON, no other text) in this format:

```json
{
  "facts": [{"category": "...", "text": "..."}],
  "feedback_lessons": [{"category": "...", "text": "...", "context": "...", "action": "..."}],
  "rules_incidents": [{"text": "...", "severity": "...", "prevention": "..."}],
  "compliments": [{"text": "...", "context": "..."}],
  "decisions": [{"decision": "...", "rationale": "...", "alternatives": "...", "reversible": true}],
  "action_items": [{"text": "..."}],
  "ideas": [{"text": "...", "context": "..."}],
  "technical_learnings": [{"text": "..."}],
  "stats": {"messages_processed": N, "sessions_scanned": N, "items_extracted": N}
}
```

Reply with ONLY the JSON. No preamble, no explanation, no markdown fences around the JSON.
""" % (date_str, conversations_file, date_str, date_str, date_str, date_str)


# ============================================================
# Agent interaction
# ============================================================

def _get_env():
    """Get environment with PATH for openclaw CLI."""
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    return env


def _call_agent(prompt: str, timeout: int = 1800) -> str | None:
    """Call the Opus agent for reflection. Returns response text or None."""
    try:
        result = subprocess.run(
            [
                "openclaw", "agent", "--local",
                "--session-id", REFLECTION_SESSION_ID,
                "--message", prompt,
                "--json",
                "--timeout", str(timeout),
            ],
            capture_output=True, text=True, timeout=timeout + 30,
            env=_get_env(),
        )

        if result.returncode != 0:
            logger.warning("Reflection agent failed (rc=%d): %s" % (
                result.returncode, result.stderr[:300]))
            return None

        data = json.loads(result.stdout)
        payloads = data.get("payloads", [])
        if payloads and payloads[0].get("text"):
            text = payloads[0]["text"]
            logger.info("Reflection agent responded: %d chars" % len(text))
            return text

        logger.warning("Reflection agent returned empty payloads")
        return None

    except subprocess.TimeoutExpired:
        logger.warning("Reflection agent timed out (%ds)" % timeout)
        return None
    except json.JSONDecodeError as e:
        logger.warning("Reflection response not JSON: %s" % e)
        return None
    except Exception as e:
        logger.warning("Reflection agent exception: %s" % e)
        return None


# ============================================================
# Response parsing
# ============================================================

_EMPTY_RESULT = {
    "facts": [],
    "feedback_lessons": [],
    "rules_incidents": [],
    "compliments": [],
    "decisions": [],
    "action_items": [],
    "ideas": [],
    "technical_learnings": [],
    "stats": {"messages_processed": 0, "sessions_scanned": 0, "items_extracted": 0},
}

_CATEGORIES = [
    "facts", "feedback_lessons", "rules_incidents", "compliments",
    "decisions", "action_items", "ideas", "technical_learnings",
]


def parse_reflection_response(text: str) -> dict:
    """Parse agent's JSON response into structured categories.

    Handles: valid JSON, partial JSON, JSON embedded in text, malformed input.
    Always returns a complete dict with all 8 categories (empty lists for missing).
    """
    if not text or not text.strip():
        return dict(_EMPTY_RESULT)

    # Try direct parse first
    parsed = _try_parse_json(text.strip())

    # If that fails, try to find JSON object in the text
    if parsed is None:
        # Look for { ... } pattern (greedy, outermost braces)
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            parsed = _try_parse_json(match.group())

    if parsed is None:
        logger.warning("Could not parse reflection response as JSON")
        return dict(_EMPTY_RESULT)

    # Normalize: ensure all categories exist with defaults
    result = dict(_EMPTY_RESULT)
    for cat in _CATEGORIES:
        if cat in parsed and isinstance(parsed[cat], list):
            result[cat] = parsed[cat]
    if "stats" in parsed and isinstance(parsed["stats"], dict):
        result["stats"] = {
            "messages_processed": parsed["stats"].get("messages_processed", 0),
            "sessions_scanned": parsed["stats"].get("sessions_scanned", 0),
            "items_extracted": parsed["stats"].get("items_extracted", 0),
        }

    return result


def _try_parse_json(text: str) -> dict | None:
    """Try to parse text as JSON. Returns dict or None."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# ============================================================
# Report formatting
# ============================================================

_CATEGORY_HEADERS = [
    ("facts", "📌 Durable Facts"),
    ("feedback_lessons", "🔧 Feedback Lessons"),
    ("rules_incidents", "⚠️ Rules & Incidents"),
    ("compliments", "🌟 Compliments"),
    ("decisions", "🧭 Decisions"),
    ("action_items", "📋 Action Items"),
    ("ideas", "💡 Ideas"),
    ("technical_learnings", "🔬 Technical Learnings"),
]


def format_reflection_report(parsed: dict) -> str:
    """Format parsed reflection data into markdown for digest append.

    Returns the full reflection section including the # heading.
    """
    lines = [
        "# 🪞 Nightly Reflection",
        "",
        "> Extracted by Doudou (Opus). All items stored in workspace.",
        "",
    ]

    for key, header in _CATEGORY_HEADERS:
        items = parsed.get(key, [])
        count = len(items)
        lines.append("### %s (%d)" % (header, count))

        if count == 0:
            lines.append("_None identified today._")
        else:
            for item in items:
                if isinstance(item, dict):
                    # Format depends on category
                    if key == "facts":
                        lines.append("- **[%s]** %s" % (
                            item.get("category", "General"), item.get("text", "")))
                    elif key == "feedback_lessons":
                        lines.append("- **[%s]** %s" % (
                            item.get("category", "General"), item.get("text", "")))
                        if item.get("action"):
                            lines.append("  - _Action:_ %s" % item["action"])
                    elif key == "compliments":
                        lines.append('- "%s" — %s' % (
                            item.get("text", ""), item.get("context", "")))
                    elif key == "decisions":
                        lines.append("- %s (rationale: %s)" % (
                            item.get("decision", ""), item.get("rationale", "")))
                    elif key == "action_items":
                        lines.append("- [ ] %s" % item.get("text", ""))
                    elif key == "ideas":
                        lines.append("- %s" % item.get("text", ""))
                    elif key == "technical_learnings":
                        lines.append("- %s" % item.get("text", ""))
                    elif key == "rules_incidents":
                        lines.append("- %s (severity: %s)" % (
                            item.get("text", ""), item.get("severity", "?")))
                    else:
                        lines.append("- %s" % str(item))
                else:
                    lines.append("- %s" % str(item))

        lines.append("")

    # Stats section
    stats = parsed.get("stats", {})
    lines.append("### 📊 Stats")
    lines.append("- Messages processed: %d" % stats.get("messages_processed", 0))
    lines.append("- Sessions scanned: %d" % stats.get("sessions_scanned", 0))
    lines.append("- Items extracted: %d" % stats.get("items_extracted", 0))
    lines.append("- Model: Opus")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# Main entry point
# ============================================================

def run_reflection(conversations_text: str, date_str: str) -> str | None:
    """Run the full nightly reflection pipeline.

    Returns formatted markdown report, or None if skipped/failed.

    SPEC-REFLECT-05: Never raises — always returns gracefully.
    """
    # UT7: Skip if no conversations
    if not conversations_text or not conversations_text.strip():
        logger.info("No conversations for reflection — skipping.")
        return None

    try:
        # Save conversations to file
        filepath = _save_conversations(conversations_text, date_str)

        # Build prompt
        prompt = build_reflection_prompt(filepath, date_str)

        # Call agent
        response = _call_agent(prompt)

        if not response:
            logger.warning("Reflection agent returned no response — fallback.")
            return "# 🪞 Nightly Reflection\n\n_Reflection unavailable — agent failed to respond._\n"

        # Parse response
        parsed = parse_reflection_response(response)

        # Format report
        report = format_reflection_report(parsed)
        logger.info("Reflection complete: %d items extracted" % (
            parsed["stats"].get("items_extracted", 0)))

        return report

    except Exception as e:
        logger.error("Reflection failed: %s" % e)
        return "# 🪞 Nightly Reflection\n\n_Reflection failed: %s_\n" % str(e)
