"""
Collector — Reads OpenClaw session transcripts and extracts messages.

Pure code. No LLM. Reads filesystem only (never writes).
Ported from the proven daily-digest.py with cleanup.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from config import (
    SGT, SESSION_DIR, SESSIONS_JSON, GROUP_NAMES,
    MAX_ASSISTANT_LENGTH,
)


def get_all_session_transcripts() -> list[tuple[str, Path]]:
    """Get all active session transcript files with display names."""
    if not SESSIONS_JSON.exists():
        return []

    try:
        with open(SESSIONS_JSON) as f:
            data = json.load(f)

        sessions = []
        for key, entry in data.items():
            sid = entry.get("sessionId", "")
            label = entry.get("label", "")
            # Skip cron sessions and sub-agent runs
            if ":cron:" in key or ":run:" in key:
                continue
            if not sid:
                continue
            transcript = SESSION_DIR / f"{sid}.jsonl"
            if not transcript.exists():
                continue

            # Determine display name
            if "dm:" in key:
                display = "DM with Boyang"
            elif "group:" in key:
                chat_id = key.split("group:")[-1]
                display = GROUP_NAMES.get(chat_id, label or f"Group {chat_id}")
            elif "direct:" in key:
                display = "Direct with Boyang"
            elif key == "agent:main:main":
                display = "Webchat"
            else:
                display = label or key
            sessions.append((display, transcript))
        return sessions
    except Exception:
        return []


def extract_user_text(raw_text: str) -> str:
    """Extract Boyang's actual message from metadata-wrapped user message.
    
    OpenClaw wraps Telegram messages with conversation info, sender metadata, etc.
    This strips all of that and returns the actual user text.
    """
    lines = raw_text.strip().split("\n")
    actual = []
    skip_block = False

    for line in lines:
        # Skip metadata header blocks
        if line.startswith("Conversation info") or line.startswith("Sender (") or line.startswith("System:"):
            skip_block = True
            continue
        if line.startswith("Replied message ("):
            skip_block = True
            continue
        if skip_block:
            if line.strip() == "```":
                skip_block = False
            elif line.strip() in ("```json", "}", "{") or line.strip().startswith('"'):
                continue
            else:
                if not line.startswith(" ") and not line.startswith("{"):
                    skip_block = False
                    actual.append(line)
            continue

        # Skip empty lines between metadata blocks at the start
        if not actual and not line.strip():
            continue

        actual.append(line)

    return "\n".join(actual).strip()


def extract_assistant_text(content) -> str:
    """Extract text from assistant message content (skip tool calls)."""
    if isinstance(content, str):
        return content.strip()
    elif isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                text = p.get("text", "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


# Noise patterns to skip
NOISE_EXACT = {"heartbeat_ok", "no_reply", "announce_skip", "ack"}
NOISE_PREFIXES = (
    "[System Message]", "[Nightly Nudger", "[DAILY_DIGEST]",
    "[DIGEST_SUMMARY_REQUEST]", "[NUDGE_REQUEST]", "[TEST]",
)
# Patterns that indicate tool/CLI output (not human-readable conversation)
TOOL_OUTPUT_PATTERNS = [
    "Session store:",
    "Sessions listed:",
    "Kind Key Age Model",
    "agent:main:teleg",
    "agent:main:cron",
    "reasoning:on usage:full system id:",
    "launchctl list",
    "Exit: ",
    "=== ",
    "Successfully replaced text in",
    "Successfully wrote",
    "Command still running (session",
    "Process still running",
    "Termination requested for session",
    "HTTP Request: POST https://api.telegram.org",
]


def _is_tool_output(text):
    """Check if assistant text looks like tool/CLI output rather than conversation."""
    # If more than 30% of lines match tool output patterns, skip it
    lines = text.strip().split("\n")
    if not lines:
        return False
    tool_lines = 0
    for line in lines:
        stripped = line.strip()
        if any(p in stripped for p in TOOL_OUTPUT_PATTERNS):
            tool_lines += 1
    # Skip if majority is tool output
    if len(lines) > 3 and tool_lines / len(lines) > 0.3:
        return True
    # Skip if first line is a tool pattern (short responses)
    if lines and any(lines[0].strip().startswith(p) for p in TOOL_OUTPUT_PATTERNS):
        return True
    return False


def extract_messages(transcript_path, since_ts):
    """Extract user and assistant messages from a transcript since a timestamp."""
    messages = []

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "message":
                    continue

                msg = entry.get("message", {})
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                # Parse timestamp
                ts_str = entry.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    dt_sgt = dt.astimezone(SGT)
                except (ValueError, TypeError):
                    continue

                if dt_sgt < since_ts:
                    continue

                # Extract text
                content = msg.get("content", "")
                if role == "user":
                    if isinstance(content, str):
                        text = extract_user_text(content)
                    elif isinstance(content, list):
                        raw_parts = []
                        for p in content:
                            if isinstance(p, dict) and p.get("type") == "text":
                                raw_parts.append(p.get("text", ""))
                        text = extract_user_text("\n".join(raw_parts))
                    else:
                        text = ""
                else:
                    text = extract_assistant_text(content)

                if not text:
                    continue

                # Skip noise
                text_lower = text.strip().lower()
                if text_lower in NOISE_EXACT:
                    continue
                if any(text.startswith(p) for p in NOISE_PREFIXES):
                    continue
                # Skip compaction flushes
                if "Pre-compaction memory flush" in text:
                    continue
                # Skip tool/CLI output (not human-readable)
                if role == "assistant" and _is_tool_output(text):
                    continue

                # Truncate assistant (never Boyang)
                if role == "assistant" and len(text) > MAX_ASSISTANT_LENGTH:
                    text = text[:MAX_ASSISTANT_LENGTH] + "\n\n_[... truncated at 4000 chars]_"

                messages.append({
                    "time": dt_sgt,
                    "time_str": dt_sgt.strftime("%H:%M"),
                    "role": role,
                    "text": text,
                })
    except Exception:
        pass

    return messages


def collect_all_messages(since_ts: datetime) -> tuple[list[dict], list[dict]]:
    """Collect all messages across all sessions since the given timestamp.
    
    Returns (previous_night_msgs, today_msgs) where previous_night = before midnight today.
    Each message dict has: time, time_str, role, text, session.
    """
    from datetime import datetime as dt
    now = datetime.now(SGT)
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    sessions = get_all_session_transcripts()
    all_previous_night = []
    all_today = []

    for display_name, transcript_path in sessions:
        messages = extract_messages(transcript_path, since_ts)
        for msg in messages:
            msg["session"] = display_name
            if msg["time"] < midnight_today:
                all_previous_night.append(msg)
            else:
                all_today.append(msg)

    # Sort chronologically
    all_previous_night.sort(key=lambda m: m["time"])
    all_today.sort(key=lambda m: m["time"])

    return all_previous_night, all_today


def format_messages(messages: list[dict]) -> str:
    """Format messages into readable markdown."""
    if not messages:
        return "_No conversations recorded._\n"

    lines = []
    for msg in messages:
        speaker = "**Boyang:**" if msg["role"] == "user" else "**Doudou:**"
        lines.append(f"**{msg['time_str']}** {speaker}\n{msg['text']}\n")

    return "\n".join(lines)


def group_by_session(messages: list[dict]) -> dict[str, list[dict]]:
    """Group messages by session name."""
    groups = {}
    for msg in messages:
        sess = msg["session"]
        if sess not in groups:
            groups[sess] = []
        groups[sess].append(msg)
    return groups
