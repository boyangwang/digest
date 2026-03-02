#!/usr/bin/env python3
"""
Historical Backfill — Generate digest + reflection for past days.

Iterates from Feb 7 → Mar 1 (or specified range), creating digest documents
with Doudou summaries and Opus reflection for each 22:30-cycle day.

Usage:
    # Dry run (shows what would be created, no actual generation)
    python scripts/backfill.py --dry-run

    # Backfill a single day (good for testing)
    python scripts/backfill.py --start 2026-02-08 --end 2026-02-08

    # Full backfill (Feb 7 → Mar 1)
    python scripts/backfill.py

    # Skip reflection (summaries only, cheaper)
    python scripts/backfill.py --no-reflection

Author: Doudou 🦮
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SGT, DIGEST_DIR, DIGEST_HOUR, DIGEST_MINUTE
from collector import collect_all_messages, format_messages, group_by_session
from llm import compose_summary
from reflection import run_reflection

# ============================================================
# Constants
# ============================================================

# Earliest available transcript message
EARLIEST_MESSAGE = datetime(2026, 2, 7, 13, 44, 31, tzinfo=SGT)

# Default range
DEFAULT_START = "2026-02-07"
DEFAULT_END = "2026-03-01"

# Pause between days (rate limiting for API calls)
PAUSE_BETWEEN_DAYS = 10  # seconds


def log(msg):
    ts = datetime.now(SGT).strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] %s" % (ts, msg))


# ============================================================
# Cycle boundary calculation
# ============================================================

def get_cycle_boundary(date_str: str) -> datetime:
    """Get 22:30 SGT for a given date string (YYYY-MM-DD)."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, second=0,
                     microsecond=0, tzinfo=SGT)


def get_backfill_days(start_str: str, end_str: str) -> list[dict]:
    """Calculate all backfill days with coverage boundaries.

    Returns list of dicts: {date_str, coverage_from, coverage_to, filename}
    """
    days = []
    start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

    current = start_date
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        coverage_to = get_cycle_boundary(date_str)

        if current == start_date and start_str == DEFAULT_START:
            # First day: coverage from earliest message
            coverage_from = EARLIEST_MESSAGE
        else:
            # Normal: coverage from previous day's 22:30
            prev_day = current - timedelta(days=1)
            coverage_from = get_cycle_boundary(prev_day.strftime("%Y-%m-%d"))

        days.append({
            "date_str": date_str,
            "coverage_from": coverage_from,
            "coverage_to": coverage_to,
            "filename": "%s-2230.md" % date_str,
        })

        current += timedelta(days=1)

    return days


# ============================================================
# Document generation
# ============================================================

def build_backfill_document(day: dict, session_summaries: list, now: datetime,
                            reflection_report: str | None = None) -> str:
    """Build a complete backfill document with YAML frontmatter."""
    fm_lines = [
        '---',
        'generated_at: "%s"' % now.isoformat(),
        'coverage_from: "%s"' % day["coverage_from"].isoformat(),
        'coverage_to: "%s"' % day["coverage_to"].isoformat(),
        'status: "backfill"',
        'backfill: true',
        'backfill_at: "%s"' % now.isoformat(),
    ]
    if reflection_report:
        fm_lines.append('reflection_at: "%s"' % now.isoformat())
        fm_lines.append('reflection_model: "opus"')
    fm_lines.append('---')

    body_parts = ["\n\n# Doudou's Summary\n"]

    for entry in session_summaries:
        body_parts.append(
            "\nSession: %s\nMessages: %d\nSummary:\n%s\n" % (
                entry["session"], entry["messages"], entry["summary"]))

    body_parts.append("\n# Boyang's Recap\n")
    body_parts.append("\n_No recap — historical backfill. Recap system started March 2026._\n")

    if reflection_report:
        body_parts.append("\n" + reflection_report + "\n")

    return "\n".join(fm_lines) + "".join(body_parts)


def process_day(day: dict, dry_run: bool = False, no_reflection: bool = False) -> bool:
    """Process a single backfill day. Returns True on success."""
    filepath = DIGEST_DIR / day["filename"]

    # R24: No overwrites
    if filepath.exists():
        log("SKIP %s — file already exists" % day["filename"])
        return True

    log("Processing %s (coverage: %s → %s)" % (
        day["date_str"],
        day["coverage_from"].strftime("%Y-%m-%d %H:%M"),
        day["coverage_to"].strftime("%Y-%m-%d %H:%M"),
    ))

    if dry_run:
        # Collect messages just to count them
        prev_night, today_msgs = collect_all_messages(day["coverage_from"])
        # Filter messages within cycle boundary
        all_msgs = [m for m in (prev_night + today_msgs) if m["time"] <= day["coverage_to"]]
        log("  DRY RUN: %d messages found" % len(all_msgs))
        return True

    # Collect messages within this cycle
    prev_night, today_msgs = collect_all_messages(day["coverage_from"])
    all_msgs = [m for m in (prev_night + today_msgs) if m["time"] <= day["coverage_to"]]
    total = len(all_msgs)

    if total == 0:
        log("  No messages for %s — creating empty document" % day["date_str"])
        now = datetime.now(SGT)
        content = build_backfill_document(day, [], now)
        filepath.write_text(content, encoding="utf-8")
        log("  Wrote %s (empty)" % day["filename"])
        return True

    log("  Collected %d messages" % total)

    # Group by session and compose summaries
    session_groups = group_by_session(all_msgs)
    session_summaries = []

    for sess_name, msgs in sorted(session_groups.items()):
        formatted = format_messages(msgs)
        summary = compose_summary(formatted)
        session_summaries.append({
            "session": sess_name,
            "messages": len(msgs),
            "summary": summary,
        })
        log("  Summarized %s (%d msgs)" % (sess_name, len(msgs)))

    # Reflection
    reflection_report = None
    if not no_reflection:
        log("  Running Opus reflection...")
        formatted_all = format_messages(all_msgs)
        reflection_report = run_reflection(formatted_all, day["date_str"])
        if reflection_report:
            log("  Reflection complete")
        else:
            log("  Reflection failed or empty — continuing without")

    # Build and write document
    now = datetime.now(SGT)
    content = build_backfill_document(day, session_summaries, now, reflection_report)
    filepath.write_text(content, encoding="utf-8")
    log("  Wrote %s (%d chars)" % (day["filename"], len(content)))

    return True


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Historical backfill for sleep digest")
    parser.add_argument("--start", default=DEFAULT_START, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="End date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")
    parser.add_argument("--no-reflection", action="store_true", help="Skip Opus reflection")
    args = parser.parse_args()

    log("=" * 60)
    log("Backfill: %s → %s" % (args.start, args.end))
    if args.dry_run:
        log("DRY RUN — no files will be created")
    if args.no_reflection:
        log("No reflection — summaries only")

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    days = get_backfill_days(args.start, args.end)
    log("Total days: %d" % len(days))

    success = 0
    failed = 0

    for i, day in enumerate(days):
        try:
            ok = process_day(day, dry_run=args.dry_run, no_reflection=args.no_reflection)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as e:
            log("  ERROR on %s: %s" % (day["date_str"], e))
            failed += 1

        # Rate limit between days (skip on dry run or last day)
        if not args.dry_run and i < len(days) - 1:
            log("  Pausing %ds..." % PAUSE_BETWEEN_DAYS)
            time.sleep(PAUSE_BETWEEN_DAYS)

    log("=" * 60)
    log("Done: %d success, %d failed, %d total" % (success, failed, len(days)))

    if not args.dry_run and success > 0:
        log("Remember to commit workspace changes:")
        log("  cd ~/.openclaw/workspace && git add -A && git commit -m 'nightly-reflection: backfill' && git push")


if __name__ == "__main__":
    main()
