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

from config import BOT_TOKEN, BOYANG_USER_ID, SGT, ATTACHMENTS_DIR

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
    await update.message.reply_text(
        "🌙 *Sleep Digest Bot*\n\n"
        "/digest — Generate digest now\n"
        "/status — Check status + view document\n"
        "/sleep — Goodnight, finalize\n\n"
        "每晚 22:30 自动收集对话摘要。/sleep 结束记录。🌙",
        parse_mode="Markdown",
    )


async def cmd_sleep(update, context):
    _scheduler.mark_sleep()
    success = finalize()
    if success:
        await update.message.reply_text("晚安 🌙 已保存到 Obsidian ✅\nGoodnight! Saved to Obsidian ✅")
        logger.info("Digest finalized.")
    else:
        await update.message.reply_text("晚安 🌙\nGoodnight! (No active digest to finalize.)")


async def cmd_status(update, context):
    """SPEC-STATUS-01: metadata + full document content."""
    now = datetime.now(SGT)
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
    await update.message.reply_text("⏳ Working...")
    await generate_digest()


async def handle_text(update, context):
    """Text → append recap + re-collect new conversations."""
    text = update.message.text
    if not text or not has_active_file():
        return

    success = append_recap(text)
    if success:
        await update.message.reply_text("✍️")
        logger.info("Recorded: %d chars." % len(text))

    # Re-collect new conversations since last coverage_to
    status = get_active_status()
    last_coverage = status.get("coverage_to")
    if last_coverage:
        try:
            since_ts = datetime.fromisoformat(str(last_coverage))
            now = datetime.now(SGT)
            session_summaries, total = _build_session_summaries(since_ts)
            if total > 0:
                update_digest(new_coverage_to=now, session_summaries=session_summaries)
                logger.info("Advanced coverage with %d new messages." % total)
        except Exception as e:
            logger.warning("Re-collect on text failed: %s" % e)


async def handle_voice(update, context):
    """SPEC-VOICE-01..05: Download audio, save to vault, transcribe, record."""
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
