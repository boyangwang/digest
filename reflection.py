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

# Workspace root for git diff capture
WORKSPACE_DIR = "/Users/claw/.openclaw/workspace"


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
- Then reply with a human-readable markdown summary of what you extracted.
  Start with "# 🪞 Nightly Reflection" heading.
  List each category with item count and top items.
  End with stats (messages processed, items extracted).
  This summary will be appended directly to the digest file — make it clean and readable.
""" % (date_str, conversations_file, date_str, date_str, date_str, date_str)


# ============================================================
# Agent interaction
# ============================================================

def _get_env():
    """Get environment with PATH for openclaw CLI."""
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    return env


def _call_agent(prompt: str, timeout: int = 1800) -> tuple[str | None, str | None]:
    """Call the Opus agent for reflection with automatic retry.

    Returns (response_text, error_reason) tuple:
      - On success: (text, None)
      - On failure: (None, reason) where reason is "timeout", "crash", or "empty"

    Retries up to 3 total attempts with exponential backoff (5s, 15s).
    """
    import time

    max_attempts = 3
    backoff_times = [0, 5, 15]  # No delay before first attempt, then 5s, 15s

    for attempt in range(1, max_attempts + 1):
        # Sleep before retry (not before first attempt)
        if attempt > 1:
            time.sleep(backoff_times[attempt - 1])

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
                reason = "crash"
                logger.warning("Reflection agent attempt %d/%d failed: rc=%d, %s" % (
                    attempt, max_attempts, result.returncode, result.stderr[:300]))
                if attempt < max_attempts:
                    continue
                return None, reason

            data = json.loads(result.stdout)
            payloads = data.get("payloads", [])
            if payloads and payloads[0].get("text"):
                text = payloads[0]["text"]
                logger.info("Reflection agent responded: %d chars" % len(text))
                return text, None

            # Empty payloads
            reason = "empty"
            logger.warning("Reflection agent attempt %d/%d failed: empty payloads" % (
                attempt, max_attempts))
            if attempt < max_attempts:
                continue
            return None, reason

        except subprocess.TimeoutExpired:
            reason = "timeout"
            logger.warning("Reflection agent attempt %d/%d failed: timeout (%ds)" % (
                attempt, max_attempts, timeout))
            if attempt < max_attempts:
                continue
            return None, reason
        except json.JSONDecodeError as e:
            reason = "crash"
            logger.warning("Reflection agent attempt %d/%d failed: not JSON: %s" % (
                attempt, max_attempts, e))
            if attempt < max_attempts:
                continue
            return None, reason
        except Exception as e:
            reason = "crash"
            logger.warning("Reflection agent attempt %d/%d failed: exception: %s" % (
                attempt, max_attempts, e))
            if attempt < max_attempts:
                continue
            return None, reason

    # Should never reach here, but just in case
    return None, "crash"


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


def format_reflection_telegram(parsed: dict, date_str: str) -> str:
    """Format parsed reflection into compact Telegram message.

    Max 4096 chars (Telegram limit). Shows category counts + top 3-5 items.
    Truncates gracefully if too long.
    """
    lines = ["🪞 Nightly Reflection — %s" % date_str, ""]

    # Category counts with emoji
    category_lines = [
        ("📌 Facts", parsed.get("facts", [])),
        ("🔧 Feedback", parsed.get("feedback_lessons", [])),
        ("⚠️ Incidents", parsed.get("rules_incidents", [])),
        ("🌟 Compliments", parsed.get("compliments", [])),
        ("🧭 Decisions", parsed.get("decisions", [])),
        ("📋 Action Items", parsed.get("action_items", [])),
        ("💡 Ideas", parsed.get("ideas", [])),
        ("🔬 Technical", parsed.get("technical_learnings", [])),
    ]

    for label, items in category_lines:
        lines.append("%s: %d items" % (label, len(items)))

    # Stats
    stats = parsed.get("stats", {})
    total_items = stats.get("items_extracted", 0)
    msg_count = stats.get("messages_processed", 0)
    lines.append("")
    lines.append("📊 %d items extracted from %d messages" % (total_items, msg_count))

    # Top items section (3-5 items from each non-empty category)
    lines.append("")
    lines.append("Top items:")
    max_items_per_category = 3
    shown_any = False

    for label, items in category_lines:
        if items:
            for item in items[:max_items_per_category]:
                text = _extract_item_text(item, label)
                if text:
                    lines.append("• [%s] %s" % (label.split()[1], text))  # Extract category name
                    shown_any = True

    if not shown_any:
        lines.append("• (No items extracted)")

    lines.append("")
    lines.append("Full report saved to Obsidian 📓")

    message = "\n".join(lines)

    # Truncate if exceeds Telegram limit
    if len(message) > 4096:
        message = message[:4090] + "..."

    return message


def _extract_item_text(item: dict | str, category_label: str) -> str:
    """Extract displayable text from an item dict/string. Max 120 chars."""
    if isinstance(item, str):
        return item[:120]
    if not isinstance(item, dict):
        return str(item)[:120]

    # Extract text based on category
    if "Facts" in category_label:
        cat = item.get("category", "")
        text = item.get("text", "")
        return "%s: %s" % (cat, text[:80]) if cat else text[:120]
    elif "Feedback" in category_label:
        return item.get("text", "")[:120]
    elif "Compliments" in category_label:
        return item.get("text", "")[:120]
    elif "Decisions" in category_label:
        return item.get("decision", "")[:120]
    elif "Action" in category_label:
        return item.get("text", "")[:120]
    elif "Ideas" in category_label:
        return item.get("text", "")[:120]
    elif "Technical" in category_label:
        return item.get("text", "")[:120]
    elif "Incidents" in category_label:
        return item.get("text", "")[:120]
    else:
        return str(item)[:120]


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
# Git diff capture & visual rendering
# ============================================================

def _git_head_hash() -> str | None:
    """Get current HEAD commit hash of the workspace repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=WORKSPACE_DIR,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _git_diff(pre_hash: str, post_hash: str) -> dict:
    """Capture git diff between two commits.

    Returns dict with:
      - files: list of {path, before, after} for each changed file
      - patch: unified patch text
      - stat: short stat summary
    """
    result = {"files": [], "patch": "", "stat": ""}

    try:
        # Get stat summary
        stat_result = subprocess.run(
            ["git", "diff", "--stat", pre_hash, post_hash],
            capture_output=True, text=True, cwd=WORKSPACE_DIR,
        )
        result["stat"] = stat_result.stdout.strip()

        # Get list of changed files
        names_result = subprocess.run(
            ["git", "diff", "--name-only", pre_hash, post_hash],
            capture_output=True, text=True, cwd=WORKSPACE_DIR,
        )
        changed_files = [f for f in names_result.stdout.strip().split("\n") if f]

        # Get full unified patch
        patch_result = subprocess.run(
            ["git", "diff", pre_hash, post_hash],
            capture_output=True, text=True, cwd=WORKSPACE_DIR,
        )
        result["patch"] = patch_result.stdout.strip()

        # Get before/after content for each changed file
        for filepath in changed_files:
            before = ""
            after = ""
            try:
                b = subprocess.run(
                    ["git", "show", "%s:%s" % (pre_hash, filepath)],
                    capture_output=True, text=True, cwd=WORKSPACE_DIR,
                )
                before = b.stdout if b.returncode == 0 else ""
            except Exception:
                pass
            try:
                a = subprocess.run(
                    ["git", "show", "%s:%s" % (post_hash, filepath)],
                    capture_output=True, text=True, cwd=WORKSPACE_DIR,
                )
                after = a.stdout if a.returncode == 0 else ""
            except Exception:
                pass
            result["files"].append({"path": filepath, "before": before, "after": after})

    except Exception as e:
        logger.warning("Git diff capture failed: %s" % e)

    return result


def render_diff_images(diff_data: dict, date_str: str) -> list[str]:
    """Render visual diff PNGs using openclaw agent --local with diffs tool.

    Sends one agent call per changed file. Returns list of PNG paths.
    Never raises — returns empty list on failure.
    """
    images = []
    if not diff_data.get("files"):
        return images

    for file_info in diff_data["files"]:
        filepath = file_info["path"]
        before = file_info["before"]
        after = file_info["after"]

        # Skip if no actual change
        if before == after:
            continue

        # Truncate very large files (50KB per side max)
        max_chars = 50000
        if len(before) > max_chars:
            before = before[:max_chars] + "\n... (truncated)"
        if len(after) > max_chars:
            after = after[:max_chars] + "\n... (truncated)"

        try:
            # Write before/after to temp files so agent can read them
            # (avoids shell escaping issues with large content in --message)
            before_path = "/tmp/reflection-diff-before-%s.txt" % filepath.replace("/", "_")
            after_path = "/tmp/reflection-diff-after-%s.txt" % filepath.replace("/", "_")
            with open(before_path, "w") as f:
                f.write(before)
            with open(after_path, "w") as f:
                f.write(after)

            msg = (
                'Read the file at %s as "before" text and the file at %s as "after" text. '
                'Then call the diffs tool with: before=<contents of before file>, '
                'after=<contents of after file>, path="%s", mode="image". '
                'Reply with ONLY the imagePath from the diffs tool result. Nothing else.'
                % (before_path, after_path, filepath)
            )

            # Use unique session ID per file to avoid lock conflicts
            safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', filepath)[:40]
            session_id = "diff-render-%s" % safe_name

            # Clear any stale lock before calling
            lock_path = "/Users/claw/.openclaw/agents/main/sessions/%s.jsonl.lock" % session_id
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass

            result = subprocess.run(
                [
                    "openclaw", "agent", "--local",
                    "--session-id", session_id,
                    "--message", msg,
                    "--json",
                    "--timeout", "120",
                ],
                capture_output=True, text=True, timeout=150,
                env=_get_env(),
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                text = data.get("payloads", [{}])[0].get("text", "")
                # Extract path from response
                for line in text.split("\n"):
                    line = line.strip().strip("`")
                    if "/tmp/openclaw" in line and ".png" in line:
                        if os.path.exists(line):
                            images.append(line)
                            logger.info("Rendered diff image: %s → %s" % (filepath, line))
                        break
            else:
                logger.warning("Diff render failed for %s (rc=%d): %s" % (
                    filepath, result.returncode, result.stderr[:200]))

        except Exception as e:
            logger.warning("Diff render exception for %s: %s" % (filepath, e))
        finally:
            # Clean up temp files
            for p in [before_path, after_path]:
                try:
                    os.unlink(p)
                except Exception:
                    pass

    return images


# ============================================================
# Main entry point
# ============================================================

def run_reflection(conversations_text: str, date_str: str) -> tuple[str | None, dict, dict]:
    """Run the full nightly reflection pipeline.

    Returns (report, diff_info, parsed) tuple:
      - report: formatted markdown string, or None if skipped/failed
      - diff_info: dict with keys: stat, patch, files, images (list of PNG paths)
      - parsed: parsed reflection dict with 8 categories + stats

    SPEC-REFLECT-05: Never raises — always returns gracefully.
    """
    empty_diff = {"stat": "", "patch": "", "files": [], "images": []}
    empty_parsed = dict(_EMPTY_RESULT)

    # UT7: Skip if no conversations
    if not conversations_text or not conversations_text.strip():
        logger.info("No conversations for reflection — skipping.")
        return None, empty_diff, empty_parsed

    try:
        # Save conversations to file
        filepath = _save_conversations(conversations_text, date_str)

        # Build prompt
        prompt = build_reflection_prompt(filepath, date_str)

        # Capture workspace state BEFORE agent runs
        pre_hash = _git_head_hash()
        logger.info("Pre-reflection git hash: %s" % pre_hash)

        # Call agent (now returns tuple with error reason)
        response, error_reason = _call_agent(prompt)

        if not response:
            # Build informative fallback message
            now = datetime.now(SGT)
            timestamp = now.strftime("%H:%M")
            # Count messages (rough estimate: lines starting with **)
            msg_count = len([line for line in conversations_text.split("\n") if line.strip().startswith("**")])
            
            reason_text = {
                "timeout": "agent timeout",
                "crash": "agent crashed",
                "empty": "empty response",
            }.get(error_reason, "agent failed")
            
            logger.warning("Reflection agent returned no response — fallback.")
            fallback = (
                "# 🪞 Nightly Reflection\n\n"
                "_Reflection unavailable (%s at %s, %d messages collected). "
                "Will retry on next /reflect._\n"
            ) % (reason_text, timestamp, msg_count)
            return (fallback, empty_diff, empty_parsed)

        # Use agent's raw response as the reflection report.
        # The agent already writes structured data to workspace files.
        # No JSON parsing needed — just prepend the section header if missing.
        if response.strip().startswith("# 🪞"):
            report = response.strip()
        else:
            report = "# 🪞 Nightly Reflection\n\n" + response.strip()

        # Try to parse JSON for Telegram summary (best-effort, not required)
        parsed = parse_reflection_response(response)
        logger.info("Reflection complete: %d chars report" % len(report))

        # Capture workspace state AFTER agent ran
        post_hash = _git_head_hash()
        logger.info("Post-reflection git hash: %s" % post_hash)

        diff_info = dict(empty_diff)
        if pre_hash and post_hash and pre_hash != post_hash:
            diff_info = _git_diff(pre_hash, post_hash)
            logger.info("Workspace changed: %d files modified" % len(diff_info.get("files", [])))

            # Render visual diffs
            images = render_diff_images(diff_info, date_str)
            diff_info["images"] = images
            logger.info("Rendered %d diff images" % len(images))
        else:
            logger.info("No workspace changes detected (hashes: %s → %s)" % (
                pre_hash, post_hash))

        return report, diff_info, parsed

    except Exception as e:
        logger.error("Reflection failed: %s" % e)
        return (
            "# 🪞 Nightly Reflection\n\n_Reflection failed: %s_\n" % str(e),
            empty_diff,
            empty_parsed,
        )
