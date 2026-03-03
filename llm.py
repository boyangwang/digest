"""
LLM — Uses Doudou (OpenClaw agent) for all text composition.

Architecture: Save conversations to a temp file → tell Doudou the file path →
Doudou reads the file with its own `read` tool → composes summary.

This avoids CLI argument length limits and truncation. Doudou processes the
full conversation text using its normal toolchain.

If Doudou is unavailable, falls back to a simple note. Bot never stops working.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import tempfile
from datetime import datetime

logger = logging.getLogger("digest-bot.llm")

# Conversation dumps saved to Obsidian vault (persistent, synced, good for reference/debug)
CONV_DUMP_DIR = "/Users/claw/Documents/NotesVault/Artificial-Colloquia/Doudou-Digest/transcripts"


def _ask_doudou(prompt, timeout=120):
    """Ask Doudou to compose text. Returns the response text or None."""
    try:
        result = subprocess.run(
            [
                "openclaw", "agent", "--local",
                "--session-id", "digest-bot",
                "--message", prompt,
                "--json",
                "--timeout", str(timeout),
            ],
            capture_output=True, text=True, timeout=timeout + 15,
            env=_get_env(),
        )
        if result.returncode != 0:
            logger.warning("Doudou call failed (rc=%d): %s" % (result.returncode, result.stderr[:300]))
            return None

        data = json.loads(result.stdout)
        payloads = data.get("payloads", [])
        if payloads and payloads[0].get("text"):
            text = payloads[0]["text"]
            logger.info("Doudou responded: %d chars" % len(text))
            return text

        logger.warning("Doudou returned empty payloads")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Doudou call timed out")
        return None
    except json.JSONDecodeError as e:
        logger.warning("Doudou response not JSON: %s" % e)
        return None
    except Exception as e:
        logger.warning("Doudou call exception: %s" % e)
        return None


def _get_env():
    """Get environment with PATH for openclaw CLI."""
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    return env


def _save_conversations_to_file(conversations_text):
    """Save conversation text to a temp file for Doudou to read.
    
    Returns the file path. Files are in /tmp/ so they auto-clean.
    """
    os.makedirs(CONV_DUMP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filepath = os.path.join(CONV_DUMP_DIR, "conv-%s.md" % timestamp)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(conversations_text)
    logger.info("Saved %d chars to %s" % (len(conversations_text), filepath))
    return filepath


def compose_summary(conversations_text):
    """Compose a bilingual summary via Doudou.
    
    1. Save all conversation text to a temp file
    2. Ask Doudou to read that file and compose a summary
    3. Doudou uses its `read` tool internally to access the full text
    """
    if not conversations_text.strip():
        return "_No conversations to summarize._\n\n_今天没有对话可以总结。_"

    # Step 1: Save to file
    filepath = _save_conversations_to_file(conversations_text)

    # Step 2: Tell Doudou where to find it
    prompt = (
        "[DIGEST_SUMMARY_REQUEST]\n\n"
        "Read the conversation transcript at: %s\n\n"
        "Then compose a nightly summary for Boyang's sleep journal.\n"
        "Write 2-4 paragraphs, bilingual (English then Chinese).\n"
        "Warm, reflective journal tone — not a report.\n"
        "No bullet points, no action items, no headers.\n"
        "Just natural flowing narrative about what was discussed today.\n\n"
        "Reply with ONLY the summary text. No preamble, no tool output, no explanations."
    ) % filepath

    result = _ask_doudou(prompt, timeout=180)
    if result:
        return result
    return _fallback(conversations_text)


def compose_nudge(context=""):
    """Compose a bedtime nudge via Doudou."""
    prompt = (
        "Compose a brief warm bedtime nudge for Boyang (2-3 sentences, bilingual EN+中文). "
        "Vary the tone. Never preachy. Reply with ONLY the nudge text.\n\n%s" % context
    )
    result = _ask_doudou(prompt, timeout=30)
    if result:
        return result
    return "🌙 Still up? Share what's on your mind, or /sleep when ready.\n\n还醒着吗？想说什么都可以，准备睡了就 /sleep 🌙"


def _fallback(conversations_text):
    boyang_count = conversations_text.count("**Boyang:**")
    doudou_count = conversations_text.count("**Doudou:**")
    return (
        "Today's conversations recorded (%d from Boyang, %d from Doudou). "
        "Summary pending.\n\n"
        "今日对话已记录（Boyang %d 条，Doudou %d 条）。摘要待生成。"
    ) % (boyang_count, doudou_count, boyang_count, doudou_count)


async def async_compose_summary(conversations_text, session_id="digest-bot"):
    """Async version of compose_summary for parallel collection.
    
    Args:
        conversations_text: Formatted conversation text to summarize.
        session_id: OpenClaw session ID. Each parallel call MUST use a unique ID
                    to avoid lock contention on the session .jsonl file.
    
    Uses asyncio.create_subprocess_exec with start_new_session=True
    for killable process groups.
    """
    if not conversations_text.strip():
        return "_No conversations to summarize._\n\n_今天没有对话可以总结。_"

    # Save to file
    filepath = _save_conversations_to_file(conversations_text)

    # Build prompt
    prompt = (
        "[DIGEST_SUMMARY_REQUEST]\n\n"
        "Read the conversation transcript at: %s\n\n"
        "Then compose a nightly summary for Boyang's sleep journal.\n"
        "Write 2-4 paragraphs, bilingual (English then Chinese).\n"
        "Warm, reflective journal tone — not a report.\n"
        "No bullet points, no action items, no headers.\n"
        "Just natural flowing narrative about what was discussed today.\n\n"
        "Reply with ONLY the summary text. No preamble, no tool output, no explanations."
    ) % filepath

    try:
        # Launch subprocess with start_new_session=True for killable process groups
        proc = await asyncio.create_subprocess_exec(
            "openclaw", "agent", "--local",
            "--session-id", session_id,
            "--message", prompt,
            "--json",
            "--timeout", "180",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # Process group isolation for killability
            env=_get_env(),
        )

        # Wait for completion
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=195)

        if proc.returncode != 0:
            logger.warning("Doudou call failed (rc=%d): %s" % (proc.returncode, stderr[:300].decode()))
            return None

        data = json.loads(stdout.decode())
        payloads = data.get("payloads", [])
        if payloads and payloads[0].get("text"):
            text = payloads[0]["text"]
            logger.info("Doudou responded: %d chars" % len(text))
            return text

        logger.warning("Doudou returned empty payloads")
        return None

    except asyncio.TimeoutError:
        logger.warning("Doudou call timed out")
        # Kill the process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        return None
    except json.JSONDecodeError as e:
        logger.warning("Doudou response not JSON: %s" % e)
        return None
    except Exception as e:
        logger.warning("Doudou call exception: %s" % e)
        return None
