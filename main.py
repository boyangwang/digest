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

import logging
import os
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

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
    finalize,
    has_active_file,
    get_active_status,
    recover_active_on_startup,
)
from scheduler import DigestScheduler
from llm import compose_summary, compose_nudge
from stt import transcribe


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


def _build_session_summaries(since_ts):
    """Collect messages, group by session, compose per-session summaries.

    Returns (session_summaries, total_messages).
    session_summaries: list of {"session": str, "messages": int, "summary": str}
    """
    prev_night, today_msgs = collect_all_messages(since_ts)
    all_msgs = prev_night + today_msgs
    total = len(all_msgs)

    if total == 0:
        return [], 0

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

    return session_summaries, total


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

        session_summaries, total = _build_session_summaries(since_ts)

        if total == 0:
            await _send_to_boyang("No new conversations since last update.")
            return

        update_digest(new_coverage_to=now, session_summaries=session_summaries)
        _scheduler.mark_digest_generated()
        logger.info("Updated active digest with %d new messages." % total)

        msg = _build_telegram_message(session_summaries, total, is_update=True)
        await _send_to_boyang(msg)
    else:
        # IDLE state: create new file
        since_ts = find_latest_coverage_to()
        if since_ts is None:
            since_ts = now - timedelta(hours=24)
            logger.info("No previous digest. Covering last 24h.")
        else:
            logger.info("Coverage from: %s" % since_ts.isoformat())

        session_summaries, total = _build_session_summaries(since_ts)
        logger.info("Collected %d messages across %d sessions." % (total, len(session_summaries)))

        if total == 0:
            await _send_to_boyang("No conversations to digest.")
            return

        create_digest(
            coverage_from=since_ts,
            coverage_to=now,
            session_summaries=session_summaries,
        )
        _scheduler.mark_digest_generated()
        logger.info("Digest created.")

        msg = _build_telegram_message(session_summaries, total)
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
    await update.message.reply_text(
        "%s🌙 *Sleep Digest Bot*\n\n"
        "/digest — Generate digest now\n"
        "/status — Check status + view document\n"
        "/sleep — Goodnight, finalize\n\n"
        "每晚 22:30 自动收集对话摘要。/sleep 结束记录。🌙" % prefix,
        parse_mode="Markdown",
    )


async def cmd_sleep(update, context):
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        logger.info("Test /sleep — has_active=%s, file=%s" % (
            _test_recorder.has_active(),
            _test_recorder.active_file,
        ))
        success = _test_recorder.finalize()
        if success:
            await update.message.reply_text("🧪 晚安 🌙 Test digest finalized ✅")
        else:
            await update.message.reply_text("🧪 晚安 🌙 No active test digest to finalize.")
        return

    _scheduler.mark_sleep()
    success = finalize()
    if success:
        await update.message.reply_text("晚安 🌙 已保存到 Obsidian ✅\nGoodnight! Saved to Obsidian ✅")
        logger.info("Digest finalized.")
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
        now = datetime.now(SGT)
        session_summaries, total = _build_session_summaries(since_ts)

        if total > 0:
            update_digest(new_coverage_to=now, session_summaries=session_summaries)
            logger.info("Advanced coverage with %d new messages." % total)

            # Build and send status + summary message
            since_str = since_ts.strftime("%H:%M")
            now_str = now.strftime("%H:%M")
            parts = ["📬 +%d msgs (%s→%s)\n" % (total, since_str, now_str)]
            for entry in session_summaries:
                parts.append("📌 *%s* (%d msgs)\n%s\n" % (
                    entry["session"], entry["messages"], entry["summary"],
                ))
            await _send_to_boyang("\n".join(parts))
        else:
            await _send_to_boyang("📭 0 new messages since %s" % (
                since_ts.strftime("%H:%M"),
            ))
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
    allowed, is_test = _check_user(update)
    if not allowed:
        return

    if is_test:
        await update.message.reply_text("🧪 📷 Photos not supported in test mode")
        return

    if not has_active_file():
        return
    caption = update.message.caption or "[Photo]"
    append_recap("📷 %s" % caption)
    await update.message.reply_text("✍️")


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

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("sleep", cmd_sleep))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Handlers registered. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
