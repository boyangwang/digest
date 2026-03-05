#!/usr/bin/env python3
"""
Sleep Digest Bot — Standalone Telegram bot for nightly digests.

Document format v2 (per SPEC.md):
  Two sections: "# Doudou's Summary" (append-only) + "# Boyang's Recap"
  No raw conversations in digest file (stored in transcripts/)
  Session/Messages/Summary entries per session

State machine:
  IDLE  → /digest → collect, create file, start nudging → ACTIVE
  ACTIVE → /digest → collect NEW msgs, append summaries  → ACTIVE
  ACTIVE → text    → append verbatim recap               → ACTIVE
  ACTIVE → /sleep  → finalize, stop nudging              → IDLE
"""

import atexit
import fcntl
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Load .env file if present (for local development)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN, BOYANG_USER_ID, TEST_USER_ID, ALLOWED_USER_IDS,
    SGT, ATTACHMENTS_DIR, TEST_DIGEST_DIR,
)

if not BOT_TOKEN:
    print("FATAL: DIGEST_BOT_TOKEN not set. Set it in .env or environment.", file=sys.stderr)
    sys.exit(1)

from collector import collect_all_messages, format_messages, group_by_session
from recorder import (
    find_latest_coverage_to,
    create_digest,
    update_digest,
    append_recap,
    append_voice_recap,
    append_image_recap,
    append_file_recap,
    append_reflection,
    finalize,
    has_active_file,
    get_active_file,
    get_active_status,
    recover_active_on_startup,
)
from reflection import run_reflection
from scheduler import DigestScheduler
from llm import compose_summary, compose_nudge
from stt import transcribe
from collection_engine import CollectionEngine


# ============================================================
# User filtering + test mode
# ============================================================

def _is_allowed(user_id: int) -> bool:
    """Check if user is in the allowlist.
    References config module directly so tests can patch dynamically.
    """
    import config as _cfg
    return user_id in _cfg.ALLOWED_USER_IDS


def _is_test_user(user_id: int) -> bool:
    """Check if user is the test account (not Boyang).
    References config module directly so tests can patch dynamically.
    """
    import config as _cfg
    return _cfg.TEST_USER_ID != 0 and user_id == _cfg.TEST_USER_ID


def _check_user(update) -> tuple[bool, bool]:
    """Check user access. Returns (allowed, is_test).
    Logs all incoming messages with user_id for discovery/audit.
    """
    user = getattr(update.message, "from_user", None) if update.message else None
    if not user:
        user = getattr(update, "effective_user", None)
    if not user or not hasattr(user, "id") or not isinstance(user.id, int):
        logger.warning("Message with no identifiable user — ignoring")
        return False, False

    user_id = user.id
    username = getattr(user, "username", None) or "?"
    name = getattr(user, "first_name", None) or "?"

    if not _is_allowed(user_id):
        logger.info("Rejected user %d (@%s, %s)" % (user_id, username, name))
        return False, False

    is_test = _is_test_user(user_id)
    if is_test:
        logger.info("Test user %d (@%s)" % (user_id, username))
    return True, is_test


# ============================================================
# Test mode — isolated recorder with no LLM, no production impact
# ============================================================

class TestRecorder:
    """Lightweight recorder for test mode. No LLM, no coverage chain.
    Completely isolated from production state.
    """

    def __init__(self, test_dir):
        self.test_dir = test_dir
        self.active_file = None

    def has_active(self):
        return self.active_file is not None and self.active_file.exists()

    def create(self):
        self.test_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(SGT)
        filename = "test-%s.md" % now.strftime("%Y%m%d-%H%M%S")
        filepath = self.test_dir / filename
        content = (
            '---\n'
            'status: "active"\n'
            'generated_at: "%s"\n'
            'coverage_from: "%s"\n'
            'coverage_to: "%s"\n'
            '---\n\n'
            '# Doudou\'s Summary\n\n'
            'TEST SUMMARY PLACEHOLDER\n\n'
            '# Boyang\'s Recap\n\n'
        ) % (now.isoformat(), now.isoformat(), now.isoformat())
        filepath.write_text(content, encoding="utf-8")
        self.active_file = filepath
        return filepath

    def update(self, text="Additional test summary"):
        if not self.has_active():
            return False
        content = self.active_file.read_text(encoding="utf-8")
        now = datetime.now(SGT)
        # Insert before "# Boyang's Recap"
        marker = "# Boyang's Recap"
        if marker in content:
            before, after = content.split(marker, 1)
            content = before.rstrip() + "\n\nSession: Test Update\nMessages: 0\nSummary:\n%s\n\n" % text + marker + after
        content = content.replace(
            'coverage_to:', 'coverage_to: "%s"\n# OLD coverage_to:' % now.isoformat(), 1
        )
        # Simpler: just re-parse and update
        import yaml as _yaml
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = _yaml.safe_load(parts[1]) or {}
            fm["coverage_to"] = now.isoformat()
            fm_str = _yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
            content = "---\n%s\n---\n%s" % (fm_str, parts[2])
        self.active_file.write_text(content, encoding="utf-8")
        return True

    def append_recap(self, text):
        if not self.has_active():
            return False
        content = self.active_file.read_text(encoding="utf-8")
        now = datetime.now(SGT)
        entry = "\n**%s** %s\n" % (now.strftime("%H:%M"), text)
        content = content.rstrip() + "\n" + entry
        self.active_file.write_text(content, encoding="utf-8")
        return True

    def append_reflection(self):
        """Append a mock reflection section for test mode.

        No real agent call — just verifies the wiring works.
        Uses the same section heading as production (SPEC-REFLECT-02).
        
        Returns mock parsed dict for format_reflection_telegram().
        """
        # Mock parsed data for testing
        mock_parsed = {
            "facts": [{"category": "Test", "text": "Test fact 1"}],
            "feedback_lessons": [{"category": "Test", "text": "Test feedback", "action": "Test action"}],
            "rules_incidents": [],
            "compliments": [{"text": "Good test", "context": "E2E testing"}],
            "decisions": [{"decision": "Use test mode", "rationale": "For E2E verification"}],
            "action_items": [{"text": "Complete E2E tests"}],
            "ideas": [{"text": "Improve test coverage"}],
            "technical_learnings": [{"text": "Test recorder pattern works"}],
            "stats": {"messages_processed": 5, "sessions_scanned": 1, "items_extracted": 8},
        }
        
        if not self.has_active():
            return mock_parsed
        content = self.active_file.read_text(encoding="utf-8")
        # Idempotent — skip if already present
        if "🪞 Nightly Reflection" in content:
            return mock_parsed
        now = datetime.now(SGT)
        mock_report = (
            "\n# 🪞 Nightly Reflection\n\n"
            "> Test mode — no real extraction.\n\n"
            "### 📌 Durable Facts (1)\n"
            "- **[Test]** Test fact 1\n\n"
            "### 🔧 Feedback Lessons (1)\n"
            "- **[Test]** Test feedback\n\n"
            "### 📊 Stats\n"
            "- Messages processed: 5\n"
            "- Items extracted: 8\n"
            "- Model: test-mock\n"
            "- Timestamp: %s\n"
        ) % now.isoformat()
        # Update YAML
        import yaml as _yaml
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = _yaml.safe_load(parts[1]) or {}
            fm["reflection_at"] = now.isoformat()
            fm["reflection_model"] = "test-mock"
            fm_str = _yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
            content = "---\n%s\n---\n%s" % (fm_str, parts[2])
        content = content.rstrip() + mock_report
        self.active_file.write_text(content, encoding="utf-8")
        return mock_parsed

    def finalize(self):
        if not self.has_active():
            return False
        content = self.active_file.read_text(encoding="utf-8")
        now = datetime.now(SGT)
        # Handle both quoted and unquoted YAML status values
        import yaml as _yaml
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = _yaml.safe_load(parts[1]) or {}
            fm["status"] = "final"
            fm["finalized_at"] = now.isoformat()
            fm_str = _yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
            content = "---\n%s\n---\n%s" % (fm_str, parts[2])
        self.active_file.write_text(content, encoding="utf-8")
        result = self.active_file
        self.active_file = None
        return result

    def get_status(self):
        if not self.has_active():
            return {"state": "IDLE", "file": None}
        content = self.active_file.read_text(encoding="utf-8")
        import yaml as _yaml
        parts = content.split("---", 2)
        fm = {}
        if len(parts) >= 3:
            fm = _yaml.safe_load(parts[1]) or {}
        return {
            "state": "ACTIVE",
            "file": self.active_file.name,
            "coverage_from": fm.get("coverage_from", "?"),
            "coverage_to": fm.get("coverage_to", "?"),
            "content": content,
        }

    def cleanup(self):
        """Remove all test files."""
        if self.test_dir.exists():
            for f in self.test_dir.glob("test-*.md"):
                f.unlink()


_test_recorder = TestRecorder(TEST_DIGEST_DIR)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("/tmp/digest-bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("digest-bot")

# --- Global state ---
_scheduler = DigestScheduler()
_app = None
_lock_fd = None  # Module-level — must stay open for process lifetime
_engine = CollectionEngine()  # Collection engine for parallel, retriable, supersedable collection


# ============================================================
# T5: Singleton Guard — PID lock, SIGTERM handler, orphan cleanup
# ============================================================


def acquire_pid_lock(pidfile="/tmp/digest-bot.pid"):
    """Acquire exclusive PID lock via fcntl.flock. Exit if another instance is running.
    
    Uses flock (not PID-based check) because:
    - Auto-releases on ANY process death, including SIGKILL — OS closes the fd
    - No stale PID file problem — lock is held by fd, not by file content
    - No race condition between checking PID and starting
    - The PID is still written for informational/debugging purposes
    
    Returns the lock file descriptor (must stay open for process lifetime).
    """
    global _lock_fd
    # Open with "a" (append) to avoid truncating — preserves existing PID for error messages
    _lock_fd = open(pidfile, "a")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another instance holds the lock
        _lock_fd.close()  # Close our fd
        # Read its PID for the error message
        try:
            with open(pidfile) as f:
                old_pid = f.read().strip()
        except Exception:
            old_pid = "unknown"
        logger.fatal("Another instance running (PID %s). Exiting." % old_pid)
        sys.exit(1)
    
    # Lock acquired — truncate and write our PID
    _lock_fd.seek(0)
    _lock_fd.truncate()
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
    logger.info("PID lock acquired: %s (PID %d)" % (pidfile, os.getpid()))
    return _lock_fd


def _remove_pidfile(pidfile="/tmp/digest-bot.pid"):
    """Remove the PID file if it exists."""
    try:
        if os.path.exists(pidfile):
            os.remove(pidfile)
            logger.info("PID file removed: %s" % pidfile)
    except Exception as e:
        logger.warning("Failed to remove PID file: %s" % e)


def _handle_sigterm(signum, frame, pidfile="/tmp/digest-bot.pid"):
    """Handle SIGTERM — log and exit cleanly."""
    logger.info("Received SIGTERM — shutting down gracefully.")
    _remove_pidfile(pidfile=pidfile)
    sys.exit(0)


def identify_orphan_files(digest_dir):
    """Scan directory for .md files that are status: active, ≤ 400 bytes.
    
    Returns list of file paths (strings).
    These are likely orphan files from duplicate bot instances.
    """
    digest_path = Path(digest_dir)
    if not digest_path.exists():
        return []
    
    orphans = []
    for md_file in digest_path.glob("*.md"):
        try:
            size = md_file.stat().st_size
            if size > 400:
                # Skip files with real content
                continue
            
            content = md_file.read_text(encoding="utf-8")
            
            # Check if status is active (both quoted and unquoted YAML)
            if 'status: "active"' in content or 'status: active' in content:
                orphans.append(str(md_file))
        except Exception as e:
            logger.warning("Could not check file %s: %s" % (md_file, e))
            continue
    
    return orphans


def delete_orphan_files(file_list):
    """Delete files in list, returns count deleted."""
    deleted = 0
    for filepath in file_list:
        try:
            os.remove(filepath)
            logger.info("Deleted orphan: %s" % filepath)
            deleted += 1
        except Exception as e:
            logger.warning("Could not delete %s: %s" % (filepath, e))
    return deleted


async def _send_to_boyang(text):
    """Send a message to Boyang. Splits if > 4096 chars."""
    if not (_app and _app.bot):
        return
    while text:
        chunk = text[:4000]
        text = text[4000:]
        try:
            await _app.bot.send_message(
                chat_id=BOYANG_USER_ID, text=chunk, parse_mode="Markdown",
            )
        except Exception:
            try:
                await _app.bot.send_message(chat_id=BOYANG_USER_ID, text=chunk)
            except Exception as e:
                logger.error("Send failed: %s" % e)


def _build_telegram_message(session_summaries, total, is_update=False):
    """Build a clean Telegram message from session summaries."""
    now = datetime.now(SGT)
    display_date = now.strftime("%B %-d, %Y")

    if is_update:
        msg = "📝 *Updated* (+%d new messages)\n\n" % total
    else:
        msg = "🌙 *%s*\n\n" % display_date

    for entry in session_summaries:
        msg += "📌 *%s* (%d msgs)\n%s\n\n" % (
            entry["session"], entry["messages"], entry["summary"],
        )

    msg += "---\n/sleep 结束 | /sleep when done"
    return msg


# ============================================================
# Core: digest generation
# ============================================================

async def generate_digest():
    """Generate or update digest. Respects state machine."""
    now = datetime.now(SGT)

    if has_active_file():
        # ACTIVE state: update same file with new conversations
        logger.info("Active file exists. Collecting new conversations...")
        status = get_active_status()
        since_ts = datetime.fromisoformat(str(status.get("coverage_to", now.isoformat())))

        result = await _engine.collect(since_ts, trigger="scheduled")

        if result is None:
            await _send_to_boyang("❌ Collection failed — will retry at next scheduled time.")
            return

        if result.total == 0:
            await _send_to_boyang("No new conversations since last update.")
            return

        update_digest(new_coverage_to=result.coverage_to, session_summaries=result.summaries)
        _scheduler.mark_digest_generated()
        logger.info("Updated active digest with %d new messages." % result.total)

        msg = _build_telegram_message(result.summaries, result.total, is_update=True)
        await _send_to_boyang(msg)
    else:
        # IDLE state: create new file
        since_ts = find_latest_coverage_to()
        if since_ts is None:
            since_ts = now - timedelta(hours=24)
            logger.info("No previous digest. Covering last 24h.")
        else:
            logger.info("Coverage from: %s" % since_ts.isoformat())

        result = await _engine.collect(since_ts, trigger="scheduled")

        if result is None:
            await _send_to_boyang("❌ Collection failed — will retry at next scheduled time.")
            return

        if result.total == 0:
            await _send_to_boyang("No conversations to digest.")
            return

        logger.info("Collected %d messages across %d sessions." % (result.total, len(result.summaries)))

        create_digest(
            coverage_from=since_ts,
            coverage_to=result.coverage_to,
            session_summaries=result.summaries,
        )
        _scheduler.mark_digest_generated()
        logger.info("Digest created.")

        msg = _build_telegram_message(result.summaries, result.total)
        await _send_to_boyang(msg)


async def do_nudge():
    """Send a nudge. Called by scheduler."""
    now = datetime.now(SGT)
    nudge_text = compose_nudge("It's %s SGT." % now.strftime("%H:%M"))
    await _send_to_boyang(nudge_text)
    logger.info("Nudge sent.")


# ============================================================
# Command handlers
# ============================================================

async def cmd_start(update, context):
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    prefix = "🧪 TEST MODE\n\n" if is_test else ""
    help_text = (
        "%s🌙 *Sleep Digest Bot*\n\n"
        "/digest — Generate digest now\n"
        "/status — Check status + view document\n"
        "/sleep — Goodnight, finalize\n"
        "/reflect — Re-run reflection on last digest\n\n"
        "每晚 22:30 自动收集对话摘要。/sleep 结束记录。🌙" % prefix
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_sleep(update, context):
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        logger.info("Test /sleep — has_active=%s, file=%s" % (
            _test_recorder.has_active(),
            _test_recorder.active_file,
        ))
        # Test mode reflection: append a mock reflection section (no real agent call)
        if _test_recorder.has_active():
            mock_parsed = _test_recorder.append_reflection()
            logger.info("Test reflection appended.")
            
            # Send mock reflection summary verbatim
            now = datetime.now(SGT)
            date_str = now.strftime("%Y-%m-%d")
            mock_summary = (
                "🪞 Nightly Reflection — %s (test mode)\n\n"
                "📌 Facts: 1 item\n"
                "🔧 Feedback: 1 item\n"
                "📊 Stats: 5 messages, 8 items extracted\n"
            ) % date_str
            await update.message.reply_text(mock_summary)
            logger.info("Test reflection summary sent.")
            
        success = _test_recorder.finalize()
        if success:
            await update.message.reply_text("🧪 晚安 🌙 Test digest finalized ✅ (with reflection)")
        else:
            await update.message.reply_text("🧪 晚安 🌙 No active test digest to finalize.")
        return

    _scheduler.mark_sleep()

    # DIGEST-009: Collect any remaining conversations (supersedes any running collection)
    sleep_ts = datetime.now(SGT)
    if has_active_file():
        status = get_active_status()
        last_coverage = status.get("coverage_to")
        if last_coverage:
            since_ts = datetime.fromisoformat(str(last_coverage))
            # Collect (aborts any running collection — /sleep has higher priority)
            result = await _engine.collect(since_ts, trigger="sleep")
            if result and result.total > 0:
                update_digest(new_coverage_to=sleep_ts, session_summaries=result.summaries)
                logger.info("/sleep: advanced coverage with %d new messages" % result.total)

    # SPEC-REFLECT-01: Run reflection BEFORE finalize
    diff_images = []
    diff_info = {"stat": "", "patch": "", "files": [], "images": []}
    if has_active_file():
        await update.message.reply_text("晚安 🌙 Running reflection...")
        try:
            # Collect conversations for this cycle (full range for reflection)
            status = get_active_status()
            coverage_from = status.get("coverage_from")
            if coverage_from:
                since_ts = datetime.fromisoformat(str(coverage_from))
                prev_night, today_msgs = collect_all_messages(since_ts)
                all_msgs = prev_night + today_msgs
                if all_msgs:
                    formatted = format_messages(all_msgs)
                    now = datetime.now(SGT)
                    date_str = now.strftime("%Y-%m-%d")
                    report, diff_info, parsed = run_reflection(formatted, date_str)
                    if report:
                        append_reflection(report)
                        logger.info("Reflection appended to digest.")
                        
                        # Send agent's reflection report verbatim to Telegram
                        summary_msg = report[:4090] + "..." if len(report) > 4096 else report
                        await update.message.reply_text(summary_msg)
                        logger.info("Reflection report sent to user (%d chars)." % len(summary_msg))
                    else:
                        logger.warning("Reflection returned no report.")
                    # Collect diff images for sending after finalize
                    diff_images = diff_info.get("images", [])
                    diff_stat = diff_info.get("stat", "")
                    if diff_stat:
                        logger.info("Workspace diff stat:\n%s" % diff_stat)
                else:
                    logger.info("No messages for reflection.")
            else:
                logger.warning("No coverage_from for reflection.")
        except Exception as e:
            # SPEC-REFLECT-05: Never block /sleep
            logger.error("Reflection failed: %s" % e)
            await update.message.reply_text("⚠️ Reflection failed, finalizing anyway.")

    success = finalize()
    if success:
        await update.message.reply_text("🪞✅ 已保存到 Obsidian\nReflection + finalize complete! Saved to Obsidian ✅")
        logger.info("Digest finalized with reflection.")

        # Send workspace change summary as text
        if diff_info.get("stat"):
            try:
                await update.message.reply_text(
                    "📊 Workspace changes:\n```\n%s\n```" % diff_info["stat"],
                    parse_mode="Markdown",
                )
            except Exception:
                pass
    else:
        await update.message.reply_text("晚安 🌙\nGoodnight! (No active digest to finalize.)")


async def cmd_status(update, context):
    """SPEC-STATUS-01: metadata + full document content."""
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    now = datetime.now(SGT)

    if is_test:
        status = _test_recorder.get_status()
        header = "🧪 📊 *Test Digest Status*\n\n"
        header += "State: `%s`\n" % status["state"]
        if status.get("file"):
            header += "File: `%s`\n" % status["file"]
        header += "Time: %s SGT\n" % now.strftime("%H:%M")
        if status.get("content"):
            header += "\n---\n📄 *Current Document:*\n\n"
            header += status["content"]
        await update.message.reply_text(header)
        return

    status = get_active_status()

    header = "📊 *Digest Status*\n\n"
    header += "State: `%s`\n" % status["state"]
    if status.get("file"):
        header += "File: `%s`\n" % status["file"]
        header += "Coverage: `%s` → `%s`\n" % (
            status.get("coverage_from", "?"), status.get("coverage_to", "?"))
    header += "Sleep: %s\n" % ("✅" if _scheduler.sleep_received else "❌")
    header += "Time: %s SGT\n" % now.strftime("%H:%M")

    if status.get("content"):
        header += "\n---\n📄 *Current Document:*\n\n"
        full_msg = header + status["content"]
    else:
        full_msg = header

    await _send_to_boyang(full_msg)


async def cmd_digest(update, context):
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        await update.message.reply_text("🧪 ⏳ Working...")
        logger.info("Test /digest — has_active=%s, file=%s" % (
            _test_recorder.has_active(),
            _test_recorder.active_file,
        ))
        if _test_recorder.has_active():
            _test_recorder.update("Test update at %s" % datetime.now(SGT).strftime("%H:%M"))
            status = _test_recorder.get_status()
            await update.message.reply_text(
                "🧪 📝 Updated test digest: `%s`" % status.get("file", "?"),
                parse_mode="Markdown",
            )
        else:
            filepath = _test_recorder.create()
            await update.message.reply_text(
                "🧪 ✅ Created test digest: `%s`" % filepath.name,
                parse_mode="Markdown",
            )
        return

    await update.message.reply_text("⏳ Working...")
    await generate_digest()


async def cmd_reflect(update, context):
    """/reflect command — re-run reflection on the most recent (or specified) finalized digest.

    Preview → Approve flow:
    1. Find target digest (most recent final, or date-specified)
    2. Collect conversations using that digest's coverage range
    3. Run reflection
    4. Send preview message with inline "Accept & Save" button
    5. If button pressed → replace_reflection() updates file
    6. If not pressed → nothing saved (preview only)

    Optional argument: /reflect 2026-03-02 for specific date.
    Production users only (not test mode).
    """
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    # T17: Production-only (not test mode)
    if is_test:
        await update.message.reply_text("🧪 /reflect is production-only (not available in test mode)")
        logger.info("Test user attempted /reflect — rejected")
        return

    # Parse optional date argument
    target_date = None
    if context.args and len(context.args) > 0:
        try:
            target_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text("Invalid date format. Use: /reflect 2026-03-02")
            return

    # Find target digest file (most recent finalized, or specific date)
    from recorder import DIGEST_DIR
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)

    target_file = None
    if target_date:
        # Look for file matching the specified date
        date_str = target_date.strftime("%Y-%m-%d")
        candidates = sorted(DIGEST_DIR.glob(f"{date_str}-*.md"), reverse=True)
        for f in candidates:
            try:
                content = f.read_text()
                if "status: \"final\"" in content or "status: final" in content:
                    target_file = f
                    break
            except Exception:
                continue
        if not target_file:
            await update.message.reply_text(f"No finalized digest found for {date_str}")
            return
    else:
        # Find most recent finalized digest
        all_files = sorted(DIGEST_DIR.glob("*.md"), reverse=True)
        for f in all_files:
            try:
                content = f.read_text()
                if "status: \"final\"" in content or "status: final" in content:
                    target_file = f
                    break
            except Exception:
                continue
        if not target_file:
            await update.message.reply_text("No finalized digests found. Run /digest → /sleep first.")
            return

    await update.message.reply_text("🪞 Re-running reflection...")

    # Extract coverage range from the file
    try:
        content = target_file.read_text()
        import yaml
        parts = content.split("---", 2)
        if len(parts) < 3:
            await update.message.reply_text("⚠️ Could not parse digest frontmatter")
            return
        fm = yaml.safe_load(parts[1])
        coverage_from = fm.get("coverage_from")
        coverage_to = fm.get("coverage_to")
        if not coverage_from or not coverage_to:
            await update.message.reply_text("⚠️ Missing coverage timestamps in digest")
            return

        # Collect conversations for this time range
        since_ts = datetime.fromisoformat(str(coverage_from))
        prev_night, today_msgs = collect_all_messages(since_ts)
        all_msgs = prev_night + today_msgs

        if not all_msgs:
            await update.message.reply_text("No conversations found for this digest period")
            return

        # Run reflection
        formatted = format_messages(all_msgs)
        from reflection import run_reflection
        now = datetime.now(SGT)
        date_str = target_file.stem.split("-")[0] + "-" + target_file.stem.split("-")[1] + "-" + target_file.stem.split("-")[2]  # Extract YYYY-MM-DD
        report, diff_info, parsed = run_reflection(formatted, date_str)

        if not report:
            await update.message.reply_text("⚠️ Reflection failed — no report generated")
            return

        # Send agent's reflection report verbatim as preview
        summary_msg = report[:4090] + "..." if len(report) > 4096 else report
        await update.message.reply_text(summary_msg)
        logger.info("Sent /reflect preview to user (%d chars)" % len(summary_msg))

        # Attach inline button: "Accept & Save"
        keyboard = [[InlineKeyboardButton("✅ Accept & Save", callback_data=f"reflect_accept:{target_file.name}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Press the button below to save this reflection to the digest file.\n"
            "If you don't press it, nothing will be saved.",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logger.error("Reflection command failed: %s" % e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def callback_reflect_accept(update, context):
    """Callback handler for the "Accept & Save" button from /reflect command.

    Callback data format: "reflect_accept:<filename>"
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    # Extract filename from callback data
    try:
        _, filename = query.data.split(":", 1)
        from recorder import DIGEST_DIR, replace_reflection
        filepath = DIGEST_DIR / filename

        if not filepath.exists():
            await query.edit_message_text("⚠️ File not found. It may have been deleted.")
            return

        # Get the reflection report from the previous run (we need to store it temporarily)
        # Since we can't easily pass data between handlers, we'll re-run reflection
        # This is acceptable for the preview-accept pattern

        # Extract coverage range and re-run
        content = filepath.read_text()
        import yaml
        parts = content.split("---", 2)
        if len(parts) < 3:
            await query.edit_message_text("⚠️ Could not parse digest frontmatter")
            return
        fm = yaml.safe_load(parts[1])
        coverage_from = fm.get("coverage_from")

        if not coverage_from:
            await query.edit_message_text("⚠️ Missing coverage timestamps")
            return

        # Re-collect and re-run reflection (idempotent)
        since_ts = datetime.fromisoformat(str(coverage_from))
        prev_night, today_msgs = collect_all_messages(since_ts)
        all_msgs = prev_night + today_msgs

        if not all_msgs:
            await query.edit_message_text("No conversations to reflect on")
            return

        formatted = format_messages(all_msgs)
        from reflection import run_reflection
        date_str = filepath.stem.split("-")[0] + "-" + filepath.stem.split("-")[1] + "-" + filepath.stem.split("-")[2]
        report, diff_info, parsed = run_reflection(formatted, date_str)

        if not report:
            await query.edit_message_text("⚠️ Reflection failed")
            return

        # Replace reflection in the file
        success = replace_reflection(report, filepath)

        if success:
            await query.edit_message_text("✅ Reflection saved to %s" % filename)
            logger.info("Reflection replaced in %s via button press" % filename)
        else:
            await query.edit_message_text("⚠️ Failed to save reflection")

    except Exception as e:
        logger.error("Callback handler error: %s" % e)
        await query.edit_message_text(f"⚠️ Error: {e}")


async def handle_text(update, context):
    """Text → append recap + re-collect new conversations + report status."""
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    text = update.message.text
    if not text:
        return

    if is_test:
        if not _test_recorder.has_active():
            await update.message.reply_text("🧪 No active test digest. Send /digest first.")
            return
        _test_recorder.append_recap(text)
        await update.message.reply_text("🧪 ✍️ Recorded %d chars" % len(text))
        logger.info("Test recorded: %d chars." % len(text))
        return

    if not has_active_file():
        return

    success = append_recap(text)
    if success:
        await update.message.reply_text("✍️")
        logger.info("Recorded: %d chars." % len(text))

    # Re-collect new conversations since last coverage_to
    status = get_active_status()
    last_coverage = status.get("coverage_to")
    if not last_coverage:
        await _send_to_boyang("⚠️ No coverage_to found — cannot collect.")
        return

    try:
        since_ts = datetime.fromisoformat(str(last_coverage))
        result = await _engine.collect(since_ts, trigger="text")

        if result is None:
            await _send_to_boyang("❌ Collection failed — will retry on next message")
            return

        if result.total == 0:
            await _send_to_boyang("📭 0 new messages since %s" % since_ts.strftime("%H:%M"))
            return

        update_digest(new_coverage_to=result.coverage_to, session_summaries=result.summaries)
        logger.info("Advanced coverage with %d new messages." % result.total)

        # Build and send status + summary message
        since_str = since_ts.strftime("%H:%M")
        now_str = result.coverage_to.strftime("%H:%M")
        parts = ["📬 +%d msgs (%s→%s)\n" % (result.total, since_str, now_str)]
        for entry in result.summaries:
            parts.append("📌 *%s* (%d msgs)\n%s\n" % (
                entry["session"], entry["messages"], entry["summary"],
            ))
        await _send_to_boyang("\n".join(parts))

    except Exception as e:
        logger.error("Re-collect on text failed: %s" % e)
        await _send_to_boyang("❌ Collection failed: %s" % e)


async def handle_voice(update, context):
    """SPEC-VOICE-01..05: Download audio, save to vault, transcribe, record."""
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        await update.message.reply_text("🧪 🎙️ Voice messages not supported in test mode")
        return

    if not has_active_file():
        return

    try:
        # 1. Get the voice/audio file from Telegram
        voice = update.message.voice or update.message.audio
        if not voice:
            return

        file = await context.bot.get_file(voice.file_id)

        # 2. Save to Obsidian vault attachments (SPEC-VOICE-01, SPEC-VOICE-06)
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(SGT)
        audio_filename = "voice-%s.ogg" % now.strftime("%Y%m%d-%H%M%S")
        audio_path = ATTACHMENTS_DIR / audio_filename

        await file.download_to_drive(str(audio_path))
        logger.info("Saved voice: %s (%d bytes)" % (audio_filename, audio_path.stat().st_size))

        # 3. Transcribe via ElevenLabs Scribe (SPEC-VOICE-02)
        transcript = transcribe(str(audio_path))
        if transcript:
            logger.info("Transcribed: %d chars" % len(transcript))
        else:
            logger.warning("Transcription failed for %s" % audio_filename)

        # 4. Record in digest (SPEC-VOICE-03)
        append_voice_recap(audio_filename, transcript)

        # 5. Reply with transcription (SPEC-VOICE-04)
        if transcript:
            reply = "🎙️ ✍️\n\n> %s" % transcript
        else:
            reply = "🎙️ ✍️ (audio saved, transcription unavailable)"
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error("Voice handling error: %s" % e)
        await update.message.reply_text("🎙️ ❌ Error processing voice message")


async def handle_photo(update, context):
    """Download photo, save to vault attachments, record in digest."""
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        if not _test_recorder.has_active():
            await update.message.reply_text("🧪 No active test digest for photo.")
            return
        try:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            test_attach = TEST_DIGEST_DIR / "attachments"
            test_attach.mkdir(parents=True, exist_ok=True)
            now = datetime.now(SGT)
            image_filename = "img-%s.jpg" % now.strftime("%Y%m%d-%H%M%S")
            await file.download_to_drive(str(test_attach / image_filename))
            caption = update.message.caption or None
            _test_recorder.append_recap("📷 ![[%s]]%s" % (
                image_filename, " %s" % caption if caption else ""))
            logger.info("Test saved image: %s" % image_filename)
            await update.message.reply_text("🧪 📷 ✍️")
        except Exception as e:
            logger.error("Test photo error: %s" % e)
            await update.message.reply_text("🧪 📷 ❌ %s" % e)
        return

    if not has_active_file():
        return

    try:
        # Get the largest photo (last in the array)
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        # Save to vault attachments
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(SGT)
        image_filename = "img-%s.jpg" % now.strftime("%Y%m%d-%H%M%S")
        image_path = ATTACHMENTS_DIR / image_filename

        await file.download_to_drive(str(image_path))
        logger.info("Saved image: %s (%d bytes)" % (image_filename, image_path.stat().st_size))

        # Record in digest
        caption = update.message.caption or None
        append_image_recap(image_filename, caption)

        await update.message.reply_text("📷 ✍️")

    except Exception as e:
        logger.error("Photo handling error: %s" % e)
        await update.message.reply_text("📷 ❌ Error saving photo")


async def handle_document(update, context):
    """Download document/file, save to vault attachments, record in digest."""
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        if not _test_recorder.has_active():
            await update.message.reply_text("🧪 No active test digest for file.")
            return
        try:
            doc = update.message.document
            if not doc:
                return
            file = await context.bot.get_file(doc.file_id)
            test_attach = TEST_DIGEST_DIR / "attachments"
            test_attach.mkdir(parents=True, exist_ok=True)
            now = datetime.now(SGT)
            if doc.file_name:
                filename = "file-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), Path(doc.file_name).name)
            else:
                ext = (doc.mime_type or "").split("/")[-1] or "bin"
                filename = "file-%s.%s" % (now.strftime("%Y%m%d-%H%M%S"), ext)
            await file.download_to_drive(str(test_attach / filename))
            caption = update.message.caption or None
            _test_recorder.append_recap("📎 ![[%s]]%s" % (
                filename, " %s" % caption if caption else ""))
            logger.info("Test saved file: %s" % filename)
            await update.message.reply_text("🧪 📎 ✍️")
        except Exception as e:
            logger.error("Test file error: %s" % e)
            await update.message.reply_text("🧪 📎 ❌ %s" % e)
        return

    if not has_active_file():
        return

    try:
        doc = update.message.document
        if not doc:
            return

        file = await context.bot.get_file(doc.file_id)

        # Use original filename if available, otherwise generate one
        ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(SGT)

        if doc.file_name:
            # Keep original name, add timestamp prefix to avoid collisions
            base = Path(doc.file_name)
            filename = "file-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), base.name)
        else:
            ext = (doc.mime_type or "").split("/")[-1] or "bin"
            filename = "file-%s.%s" % (now.strftime("%Y%m%d-%H%M%S"), ext)

        file_path = ATTACHMENTS_DIR / filename

        await file.download_to_drive(str(file_path))
        logger.info("Saved file: %s (%d bytes)" % (filename, file_path.stat().st_size))

        # Record in digest
        caption = update.message.caption or None
        append_file_recap(filename, caption)

        await update.message.reply_text("📎 ✍️")

    except Exception as e:
        logger.error("Document handling error: %s" % e)
        await update.message.reply_text("📎 ❌ Error saving file")


# ============================================================
# Lifecycle
# ============================================================

async def post_init(application):
    global _app
    _app = application

    recovered = recover_active_on_startup()
    if recovered:
        _scheduler.mark_digest_generated()
        logger.info("Recovered active digest: %s" % recovered.name)

    _scheduler.set_callbacks(on_digest=generate_digest, on_nudge=do_nudge)
    _scheduler.start()
    logger.info("Bot initialized. Scheduler started.")


def main():
    logger.info("=" * 50)
    logger.info("Sleep Digest Bot starting...")

    # T5: Acquire PID lock BEFORE any Telegram operations
    acquire_pid_lock()
    
    # T5: Register SIGTERM handler
    signal.signal(signal.SIGTERM, _handle_sigterm)
    
    # T5: Register atexit cleanup
    atexit.register(_remove_pidfile)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sleep", cmd_sleep))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("reflect", cmd_reflect))
    app.add_handler(CallbackQueryHandler(callback_reflect_accept, pattern="^reflect_accept:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("Handlers registered. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
