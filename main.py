#!/usr/bin/env python3
"""
Sleep Digest Bot — Standalone Telegram bot for nightly digests.

State machine:
  IDLE  → /digest → collect, create file, start nudging → ACTIVE
  ACTIVE → /digest → collect NEW msgs, update same file  → ACTIVE
  ACTIVE → text    → append verbatim                     → ACTIVE
  ACTIVE → /sleep  → finalize file, stop nudging         → IDLE
  IDLE  → /digest → NEW file, collect since last coverage → ACTIVE

Files: YYYY-MM-DD-HHMM.md (multiple per day supported).
Timestamp chain unbroken across files.
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

from config import BOT_TOKEN, BOYANG_USER_ID, SGT, DIGEST_DIR

if not BOT_TOKEN:
    print("FATAL: DIGEST_BOT_TOKEN not set. Set it in .env or environment.", file=sys.stderr)
    sys.exit(1)
from collector import (
    collect_all_messages,
    format_messages,
    group_by_session,
)
from recorder import (
    find_latest_coverage_to,
    create_digest,
    update_digest,
    append_recap,
    finalize,
    has_active_file,
    get_active_status,
    recover_active_on_startup,
)
from scheduler import DigestScheduler
from llm import compose_summary, compose_nudge

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
    """Send a message to Boyang via the bot. Splits if > 4096 chars."""
    if not (_app and _app.bot):
        return
    chunks = []
    while text:
        chunks.append(text[:4000])
        text = text[4000:]
    for chunk in chunks:
        try:
            await _app.bot.send_message(
                chat_id=BOYANG_USER_ID, text=chunk, parse_mode="Markdown",
            )
        except Exception:
            try:
                await _app.bot.send_message(chat_id=BOYANG_USER_ID, text=chunk)
            except Exception as e:
                logger.error("Send failed: %s" % e)


def _collect_and_format(since_ts):
    """Collect messages and format them. Returns (prev, today, total, prev_text, today_text, all_text)."""
    now = datetime.now(SGT)
    prev_night, today_msgs = collect_all_messages(since_ts)
    total = len(prev_night) + len(today_msgs)

    prev_text = ""
    if prev_night:
        for sess_name, msgs in sorted(group_by_session(prev_night).items()):
            prev_text += "### %s\n\n%s\n" % (sess_name, format_messages(msgs))
    else:
        prev_text = "_No late-night conversations._\n"

    today_text = ""
    if today_msgs:
        for sess_name, msgs in sorted(group_by_session(today_msgs).items()):
            today_text += "### %s\n\n%s\n" % (sess_name, format_messages(msgs))
    else:
        today_text = "_No conversations recorded._\n"

    return prev_night, today_msgs, total, prev_text, today_text


def _build_telegram_message(summary, prev_night, today_msgs, total):
    """Build a clean, readable message for Boyang.
    
    Primary content is Doudou's summary. 
    Below that: session names and message counts (not raw message dumps).
    """
    now = datetime.now(SGT)
    display_date = now.strftime("%B %-d, %Y")
    footer = "\n---\n随时分享想法，/sleep 结束 | Share thoughts, /sleep when done."

    msg = "🌙 *%s*\n\n" % display_date
    msg += summary + "\n\n"

    # Session overview (just names + counts, NOT raw messages)
    all_msgs = []
    if prev_night:
        msg += "🌃 *Previous Night*\n"
        for sess_name, msgs in sorted(group_by_session(prev_night).items()):
            boyang_count = sum(1 for m in msgs if m["role"] == "user")
            msg += "  • %s — %d messages (%d from Boyang)\n" % (sess_name, len(msgs), boyang_count)
        msg += "\n"
        all_msgs.extend(prev_night)

    if today_msgs:
        msg += "🗣️ *Today*\n"
        for sess_name, msgs in sorted(group_by_session(today_msgs).items()):
            boyang_count = sum(1 for m in msgs if m["role"] == "user")
            msg += "  • %s — %d messages (%d from Boyang)\n" % (sess_name, len(msgs), boyang_count)
        msg += "\n"
        all_msgs.extend(today_msgs)

    sessions_count = len(set(m["session"] for m in all_msgs)) if all_msgs else 0
    msg += "📊 %d messages across %d sessions" % (total, sessions_count)
    msg += footer
    return msg


# ============================================================
# Core: digest generation
# ============================================================

async def generate_digest():
    """Generate or update digest. Respects state machine."""
    now = datetime.now(SGT)

    if has_active_file():
        # ACTIVE state: /digest again → update same file with new conversations
        logger.info("Active file exists. Updating with new conversations...")
        status = get_active_status()
        since_ts = datetime.fromisoformat(status.get("coverage_to", now.isoformat()))

        prev_night, today_msgs, total, prev_text, today_text = _collect_and_format(since_ts)

        if total == 0:
            await _send_to_boyang("No new conversations since last digest update.")
            return

        new_text = prev_text + "\n" + today_text
        summary = compose_summary(new_text)
        update_digest(new_coverage_to=now, new_sections_text=new_text, new_summary=summary)
        logger.info("Updated active digest with %d new messages." % total)

        msg = _build_telegram_message(summary, prev_night, today_msgs, total)
        msg = "📝 **Updated** (+" + str(total) + " new messages)\n\n" + msg
        await _send_to_boyang(msg)
    else:
        # IDLE state: /digest → create new file
        since_ts = find_latest_coverage_to()
        if since_ts is None:
            since_ts = now - timedelta(hours=24)
            logger.info("No previous digest. Covering last 24h.")
        else:
            logger.info("Coverage from: %s" % since_ts.isoformat())

        prev_night, today_msgs, total, prev_text, today_text = _collect_and_format(since_ts)
        logger.info("Collected %d messages." % total)

        summary = compose_summary(prev_text + "\n" + today_text)

        filepath = create_digest(
            coverage_from=since_ts,
            coverage_to=now,
            previous_night_sections=prev_text,
            today_sections=today_text,
            summary=summary,
        )
        logger.info("Digest created: %s" % filepath)

        msg = _build_telegram_message(summary, prev_night, today_msgs, total)
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
    """Handle /start."""
    await update.message.reply_text(
        "🌙 *Sleep Digest Bot*\n\n"
        "Every night at 22:30, I collect all your conversations with Doudou "
        "and send you a summary.\n\n"
        "/digest — Generate digest now\n"
        "/status — Check status\n"
        "/sleep — Goodnight, finalize\n\n"
        "每晚 22:30 自动收集对话摘要。/sleep 结束记录。🌙",
        parse_mode="Markdown",
    )


async def cmd_sleep(update, context):
    """Handle /sleep — finalize and stop nudging."""
    _scheduler.mark_sleep()
    success = finalize()

    if success:
        await update.message.reply_text("晚安 🌙 已保存到 Obsidian ✅\nGoodnight! Saved to Obsidian ✅")
        logger.info("Digest finalized.")
    else:
        await update.message.reply_text("晚安 🌙\nGoodnight! (No active digest to finalize.)")
        logger.info("Sleep received, no active file.")


async def cmd_status(update, context):
    """Handle /status."""
    now = datetime.now(SGT)
    status = get_active_status()

    lines = ["📊 *Digest Status*\n"]
    lines.append("State: `%s`" % status["state"])
    if status.get("file"):
        lines.append("File: `%s`" % status["file"])
        lines.append("Coverage: %s → %s" % (
            status.get("coverage_from", "?"), status.get("coverage_to", "?")))
    lines.append("Sleep: %s" % ("✅" if _scheduler.sleep_received else "❌"))
    lines.append("Time: %s SGT" % now.strftime("%H:%M"))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_digest(update, context):
    """Handle /digest — generate or update."""
    await update.message.reply_text("⏳ Working...")
    await generate_digest()


async def handle_text(update, context):
    """Any text → append to active digest + re-collect new conversations.
    
    If Boyang is replying, he's still awake. Any new OpenClaw conversations
    since last coverage should be captured NOW, not left for another day.
    """
    text = update.message.text
    if not text:
        return

    if not has_active_file():
        return  # No active digest, ignore

    # 1. Record Boyang's text verbatim
    success = append_recap(text)
    if success:
        await update.message.reply_text("✍️")
        logger.info("Recorded: %d chars." % len(text))

    # 2. Re-collect new conversations and advance timestamp
    status = get_active_status()
    last_coverage = status.get("coverage_to")
    if last_coverage:
        try:
            since_ts = datetime.fromisoformat(str(last_coverage))
            now = datetime.now(SGT)
            prev_night, today_msgs, total, prev_text, today_text = _collect_and_format(since_ts)
            if total > 0:
                new_text = prev_text + "\n" + today_text
                update_digest(new_coverage_to=now, new_sections_text=new_text, new_summary=None)
                logger.info("Advanced coverage with %d new messages." % total)
        except Exception as e:
            logger.warning("Re-collect on text failed: %s" % e)


async def handle_voice(update, context):
    """Voice message → note receipt."""
    if not has_active_file():
        return
    append_recap("[Voice message received]")
    await update.message.reply_text("🎙️ ✍️")


async def handle_photo(update, context):
    """Photo → record caption."""
    if not has_active_file():
        return
    caption = update.message.caption or "[Photo]"
    append_recap("📷 %s" % caption)
    await update.message.reply_text("✍️")


# ============================================================
# Lifecycle
# ============================================================

async def post_init(application):
    """After init: recover state, start scheduler."""
    global _app
    _app = application

    # Recover active file from previous run (crash recovery)
    recovered = recover_active_on_startup()
    if recovered:
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
