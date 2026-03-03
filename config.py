"""
Configuration for Sleep Digest Bot.
All paths, tokens, and timing constants in one place.
"""

import os
from pathlib import Path
from datetime import timezone, timedelta

# --- Timezone ---
SGT = timezone(timedelta(hours=8))

# --- Telegram ---
BOT_TOKEN = os.environ.get("DIGEST_BOT_TOKEN", "")
if not BOT_TOKEN and __name__ != "__main__":
    # Allow import for tests (conftest sets a dummy), but warn
    pass
BOYANG_USER_ID = 411364623  # Only process messages from Boyang

# Test account (Doudou's Telegram client on Mac Mini, @claw0606, name: "mala")
# Discovered 2026-03-02: user_id=6805433372
TEST_USER_ID = int(os.environ.get("TEST_USER_ID", "6805433372"))

# Allowlist: only these users can interact with the bot
ALLOWED_USER_IDS = {BOYANG_USER_ID, TEST_USER_ID} - {0}

# --- Paths ---
VAULT_PATH = Path("/Users/claw/Documents/NotesVault")
DIGEST_DIR = VAULT_PATH / "Artificial-Colloquia" / "Doudou-Digest"
ATTACHMENTS_DIR = DIGEST_DIR / "attachments"
PID_FILE = Path("/tmp/digest-bot.pid")

# Test mode uses a separate directory to avoid contaminating production
TEST_DIGEST_DIR = DIGEST_DIR / "_test"
SESSION_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
SESSIONS_JSON = SESSION_DIR / "sessions.json"
LOG_PATH = Path("/tmp/digest-bot.log")

# --- Timing ---
DIGEST_HOUR = 22
DIGEST_MINUTE = 30
NUDGE_INTERVAL_MINUTES = 30
NUDGE_START_HOUR = 22   # Nudging window: 22:30 - 07:00
NUDGE_START_MINUTE = 30
NUDGE_END_HOUR = 7
NUDGE_END_MINUTE = 0

# --- Session display names ---
GROUP_NAMES = {
    "-5125187430": "CLAW 003",
    "-5109089385": "CLAW 008",
    "-4995445768": "CLAW Group 1",
    "-5204018860": "CLAW Group 2",
    "-5129926053": "CLAW Group 3",
    "-5192299370": "CLAW Group 4",
    "-4886214955": "CLAW Group 5",
    "-5062669375": "CLAW Group 6",
}

# --- Message truncation ---
MAX_ASSISTANT_LENGTH = 4000  # Truncate Doudou's responses; never truncate Boyang's
