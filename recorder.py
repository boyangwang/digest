"""
Recorder — Atomic writes to Obsidian vault with proper YAML handling.

State machine:
  IDLE → /digest → create file → ACTIVE
  ACTIVE → /digest → update same file (extend coverage) → ACTIVE
  ACTIVE → text → append recap → ACTIVE
  ACTIVE → /sleep → finalize → IDLE
  IDLE → /digest → new file → ACTIVE

Naming: YYYY-MM-DD-HHMM.md (supports multiple files per day).
Timestamp chain: each file's coverage_to → next file's coverage_from.

All 5 fixes baked in:
  1. Atomic writes (.tmp → os.rename)
  2. YAML parsing (yaml.safe_load between --- delimiters)
  3. Metadata extraction (handled by collector)
  4. Recap = code only (append_recap / finalize)
  5. Self-contained (no external triggers)
"""

import os
from datetime import datetime
from pathlib import Path

import yaml

from config import SGT, DIGEST_DIR


# ============================================================
# Active file tracking (in-memory state)
# ============================================================

_active_file = None  # Path to the currently active digest file


def get_active_file():
    """Get the currently active (non-finalized) digest file path, or None."""
    return _active_file


def has_active_file():
    """Check if there's an active digest file."""
    return _active_file is not None and _active_file.exists()


# ============================================================
# YAML frontmatter helpers
# ============================================================

def _parse_frontmatter(content):
    """Parse YAML frontmatter from markdown. Returns (dict, body_str)."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2]


def _serialize_frontmatter(fm, body):
    """Serialize frontmatter dict + body back to markdown."""
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
    return "---\n%s\n---\n%s" % (fm_str, body)


def _atomic_write(filepath, content):
    """Write atomically: .tmp → os.rename. Fix #1."""
    tmp_path = filepath.with_suffix(".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.rename(str(tmp_path), str(filepath))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


# ============================================================
# Finding coverage chain
# ============================================================

def find_latest_coverage_to():
    """Find the most recent coverage_to across all digest files.
    
    Scans all files, not just today's — the chain is date-independent.
    Returns datetime or None.
    """
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    latest = None
    for f in DIGEST_DIR.glob("*.md"):
        try:
            content = f.read_text()
            fm, _ = _parse_frontmatter(content)
            ts_str = fm.get("coverage_to")
            if ts_str:
                dt = datetime.fromisoformat(str(ts_str))
                if latest is None or dt > latest:
                    latest = dt
        except Exception:
            continue
    return latest


# ============================================================
# Core operations
# ============================================================

def create_digest(
    coverage_from,
    coverage_to,
    previous_night_sections,
    today_sections,
    summary,
):
    """Create a new digest file. Returns the filepath.
    
    Called when /digest is issued in IDLE state.
    """
    global _active_file
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(SGT)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")
    day_name = now.strftime("%A")
    display_date = now.strftime("%B %-d, %Y")
    filename = "%s-%s.md" % (date_str, time_str)

    content = """---
date: "%s"
day: "%s"
generated_at: "%s"
coverage_from: "%s"
coverage_to: "%s"
status: "active"
---

# %s — %s

## 🌙 Summary

%s

## 🌃 Previous Night

%s

## 🗣️ Today's Conversations

%s

## 📝 Boyang's Recap

""" % (
        date_str, day_name,
        coverage_to.isoformat(),
        coverage_from.isoformat(),
        coverage_to.isoformat(),
        display_date, day_name,
        summary,
        previous_night_sections,
        today_sections,
    )

    filepath = DIGEST_DIR / filename
    _atomic_write(filepath, content)
    _active_file = filepath
    return filepath


def update_digest(
    new_coverage_to,
    new_sections_text,
    new_summary,
):
    """Update the active digest with new conversations. Extends coverage.
    
    Called when /digest is issued in ACTIVE state.
    Returns True on success.
    """
    global _active_file
    if not has_active_file():
        return False

    try:
        content = _active_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(content)

        # Advance coverage_to
        fm["coverage_to"] = new_coverage_to.isoformat()

        # Append new conversations before the recap section
        recap_marker = "## 📝 Boyang's Recap"
        if recap_marker in body:
            before_recap, after_recap = body.split(recap_marker, 1)
            # Add new conversations
            before_recap = before_recap.rstrip() + "\n\n## 🗣️ New Conversations (updated)\n\n" + new_sections_text + "\n\n"
            body = before_recap + recap_marker + after_recap
        else:
            body = body.rstrip() + "\n\n## 🗣️ New Conversations (updated)\n\n" + new_sections_text + "\n"

        # Update summary
        if new_summary:
            summary_marker = "## 🌙 Summary"
            if summary_marker in body:
                parts = body.split(summary_marker, 1)
                # Find the next ## heading after summary
                rest = parts[1]
                next_heading = rest.find("\n## ")
                if next_heading > 0:
                    body = parts[0] + summary_marker + "\n\n" + new_summary + "\n" + rest[next_heading:]

        new_content = _serialize_frontmatter(fm, body)
        _atomic_write(_active_file, new_content)
        return True
    except Exception:
        return False


def append_recap(text):
    """Append Boyang's text verbatim to the active digest. Fix #4.
    
    Returns True on success, False if no active file.
    """
    if not has_active_file():
        return False

    try:
        content = _active_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(content)

        now = datetime.now(SGT)
        time_str = now.strftime("%H:%M")
        entry = "\n**%s** %s\n" % (time_str, text)

        # Append at end (recap is last section)
        body = body.rstrip() + "\n" + entry

        new_content = _serialize_frontmatter(fm, body)
        _atomic_write(_active_file, new_content)
        return True
    except Exception:
        return False


def finalize():
    """Finalize the active digest (/sleep received).
    
    Sets status to 'final', records timestamp. Returns True on success.
    """
    global _active_file
    if not has_active_file():
        return False

    try:
        content = _active_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(content)

        fm["status"] = "final"
        fm["finalized_at"] = datetime.now(SGT).isoformat()

        new_content = _serialize_frontmatter(fm, body)
        _atomic_write(_active_file, new_content)
        _active_file = None  # Clear active — back to IDLE
        return True
    except Exception:
        return False


def get_active_status():
    """Get status info for /status command."""
    if not has_active_file():
        return {"state": "IDLE", "file": None}

    try:
        content = _active_file.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(content)
        return {
            "state": "ACTIVE",
            "file": _active_file.name,
            "coverage_from": fm.get("coverage_from", "?"),
            "coverage_to": fm.get("coverage_to", "?"),
            "status": fm.get("status", "?"),
        }
    except Exception:
        return {"state": "ACTIVE", "file": _active_file.name}


def recover_active_on_startup():
    """On bot startup, check if there's an unfinalized digest to resume.
    
    Scans for any file with status != 'final'. Resumes the most recent one.
    """
    global _active_file
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    latest_active = None
    latest_time = None

    for f in sorted(DIGEST_DIR.glob("*.md"), reverse=True):
        try:
            content = f.read_text()
            fm, _ = _parse_frontmatter(content)
            if fm.get("status") in ("active", "draft"):
                gen_str = fm.get("generated_at", "")
                if gen_str:
                    gen_time = datetime.fromisoformat(str(gen_str))
                    if latest_time is None or gen_time > latest_time:
                        latest_active = f
                        latest_time = gen_time
        except Exception:
            continue

    if latest_active:
        _active_file = latest_active
    return latest_active
