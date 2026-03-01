"""
Recorder — Atomic writes to Obsidian vault with proper YAML handling.

Document format v2 (per SPEC.md):
  Two sections only: "# Doudou's Summary" + "# Boyang's Recap"
  No raw conversations (stored in transcripts/)
  Summary is append-only with Session/Messages/Summary entries
  Minimal YAML frontmatter (no date/day fields)

State machine:
  IDLE → /digest → create file → ACTIVE
  ACTIVE → /digest → append summaries to same file → ACTIVE
  ACTIVE → text → append recap → ACTIVE
  ACTIVE → /sleep → finalize → IDLE

Naming: YYYY-MM-DD-HHMM.md (supports multiple files per day).
Timestamp chain: each file's coverage_to → next file's coverage_from.
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
    return _active_file


def has_active_file():
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
    """Write atomically: .tmp → os.rename."""
    tmp_path = filepath.with_suffix(".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.rename(str(tmp_path), str(filepath))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


# ============================================================
# Formatting helpers
# ============================================================

def _format_session_summaries(session_summaries):
    """Format a list of session summary dicts into markdown.

    Each entry: {"session": str, "messages": int, "summary": str}
    """
    parts = []
    for entry in session_summaries:
        parts.append(
            "Session: %s\nMessages: %d\nSummary:\n%s"
            % (entry["session"], entry["messages"], entry["summary"])
        )
    return "\n\n".join(parts)


# ============================================================
# Finding coverage chain
# ============================================================

def find_latest_coverage_to():
    """Find the most recent coverage_to across all digest files.

    Scans all files — the chain is date-independent.
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

def create_digest(coverage_from, coverage_to, session_summaries):
    """Create a new digest file. Returns the filepath.

    Called when /digest is issued in IDLE state.

    session_summaries: list of {"session": str, "messages": int, "summary": str}
    """
    global _active_file
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(SGT)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")
    filename = "%s-%s.md" % (date_str, time_str)

    summaries_text = _format_session_summaries(session_summaries)

    content = (
        '---\n'
        'generated_at: "%s"\n'
        'coverage_from: "%s"\n'
        'coverage_to: "%s"\n'
        'status: "active"\n'
        '---\n\n'
        '# Doudou\'s Summary\n\n'
        '%s\n\n'
        '# Boyang\'s Recap\n\n'
    ) % (
        coverage_to.isoformat(),
        coverage_from.isoformat(),
        coverage_to.isoformat(),
        summaries_text,
    )

    filepath = DIGEST_DIR / filename
    _atomic_write(filepath, content)
    _active_file = filepath
    return filepath


def update_digest(new_coverage_to, session_summaries):
    """Append new session summaries to the active digest. Extends coverage.

    Called when /digest is issued in ACTIVE state.
    Summary is APPEND-ONLY — previous entries are never modified.

    Returns True on success, False if no active file or empty summaries.
    """
    global _active_file
    if not has_active_file():
        return False

    if not session_summaries:
        return False

    try:
        content = _active_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(content)

        # Advance coverage_to
        fm["coverage_to"] = new_coverage_to.isoformat()

        # Format new summary entries
        new_text = _format_session_summaries(session_summaries)

        # Append before "# Boyang's Recap" (which is always last)
        recap_marker = "# Boyang's Recap"
        if recap_marker in body:
            before_recap, after_recap = body.split(recap_marker, 1)
            body = before_recap.rstrip() + "\n\n" + new_text + "\n\n" + recap_marker + after_recap
        else:
            # Safety: if marker missing, append at end
            body = body.rstrip() + "\n\n" + new_text + "\n"

        new_content = _serialize_frontmatter(fm, body)
        _atomic_write(_active_file, new_content)
        return True
    except Exception:
        return False


def append_recap(text):
    """Append Boyang's text verbatim to the active digest.

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
        _active_file = None
        return True
    except Exception:
        return False


def get_active_status():
    """Get status info for /status command.

    SPEC-STATUS-01: Returns metadata (state, file, timestamps)
    AND the full raw document content.
    """
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
            "content": content,
        }
    except Exception:
        return {"state": "ACTIVE", "file": _active_file.name}


def _is_v2_format(content):
    """Check if a digest file is in v2 format.

    v2 has "# Doudou's Summary" and no v1 artifacts like "Previous Night".
    """
    return "# Doudou's Summary" in content and "Previous Night" not in content


def recover_active_on_startup():
    """On bot startup, check if there's an unfinalized v2 digest to resume.

    Scans for files with status='active'. Only recovers v2-format files.
    v1-format active files are auto-finalized (they're incompatible).
    """
    global _active_file
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    latest_active = None
    latest_time = None

    for f in sorted(DIGEST_DIR.glob("*.md"), reverse=True):
        try:
            content = f.read_text()
            fm, body = _parse_frontmatter(content)
            if fm.get("status") in ("active", "draft"):
                if not _is_v2_format(content):
                    # Auto-finalize v1 files — incompatible with v2
                    fm["status"] = "final"
                    fm["finalized_at"] = datetime.now(SGT).isoformat()
                    fm["migration_note"] = "auto-finalized by v2 migration"
                    _atomic_write(f, _serialize_frontmatter(fm, body))
                    continue

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
