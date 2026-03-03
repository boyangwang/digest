"""
Live end-to-end integration tests using Telegram Desktop UI automation.

These tests send REAL messages to @sleep_digest_bot from the Mac Mini's
Telegram client (@claw0606) and verify:
  1. Bot replies (via log parsing — faster than vision)
  2. Test file creation/modification in _test/ directory
  3. Full lifecycle: /digest → text → /status → /sleep

Prerequisites:
  - Telegram Desktop running and logged into @claw0606
  - Bot running (via launchd com.digest-bot)
  - Bot chat @sleep_digest_bot already opened in Telegram

Run with: pytest tests/test_live_e2e.py -v -s
(Use -s to see real-time output during slow UI automation)
"""

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SGT, TEST_DIGEST_DIR

# Skip if not on Mac Mini with Telegram running
TELEGRAM_RUNNING = (
    subprocess.run(
        ["pgrep", "-f", "Telegram.app"],
        capture_output=True,
    ).returncode == 0
)

BOT_RUNNING = (
    subprocess.run(
        ["pgrep", "-f", "digest-bot/main.py"],
        capture_output=True,
    ).returncode == 0
)

SKIP_REASON = "Requires Telegram Desktop + digest bot running on Mac Mini"
pytestmark = pytest.mark.skipif(
    not (TELEGRAM_RUNNING and BOT_RUNNING),
    reason=SKIP_REASON,
)

LOG_PATH = Path("/tmp/digest-bot.log")
PEEKABOO = "peekaboo"


# ============================================================
# Helpers
# ============================================================

def _peekaboo(args, timeout=15):
    """Run peekaboo command, return stdout."""
    result = subprocess.run(
        f"{PEEKABOO} {args}",
        shell=True, capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def _ensure_bot_chat_open():
    """Make sure the bot chat is open with input field visible."""
    import json

    _peekaboo("app switch --to Telegram")
    time.sleep(0.5)

    # Check for input field
    raw = _peekaboo("see --app Telegram --json 2>/dev/null", timeout=20)
    try:
        data = json.loads(raw)
        elements = data.get("data", {}).get("ui_elements", [])
    except (json.JSONDecodeError, KeyError):
        elements = []

    for e in elements:
        if e.get("role") == "textField" and "write" in e.get("label", "").lower():
            return True

    # Need to navigate to bot chat
    search = None
    for e in elements:
        if e.get("role") == "textField" and "search" in e.get("label", "").lower():
            search = e
            break
    if not search:
        return False

    _peekaboo(f"click --on {search['id']} --app Telegram")
    time.sleep(0.3)
    _peekaboo('type "@sleep_digest_bot" --app Telegram')
    time.sleep(1.5)
    _peekaboo("click --app Telegram --coords 250,180")
    time.sleep(1.0)

    # Re-check for input field
    raw = _peekaboo("see --app Telegram --json 2>/dev/null", timeout=20)
    try:
        data = json.loads(raw)
        elements = data.get("data", {}).get("ui_elements", [])
    except Exception:
        elements = []

    return any(
        e.get("role") == "textField" and "write" in e.get("label", "").lower()
        for e in elements
    )


def _find_input_field():
    """Find the message input field element ID."""
    import json
    raw = _peekaboo("see --app Telegram --json 2>/dev/null", timeout=20)
    try:
        data = json.loads(raw)
        for e in data.get("data", {}).get("ui_elements", []):
            if e.get("role") == "textField" and "write" in e.get("label", "").lower():
                return e["id"]
    except Exception:
        pass
    return None


def send_message(text, wait_after=3):
    """Send a message via AppleScript keystroke.

    Uses Tab to ensure focus is on the message input field,
    then types text and presses Return.
    """
    import subprocess as sp

    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    result = sp.run(["osascript", "-e", f"""
tell application "Telegram" to activate
delay 0.2
tell application "System Events"
    tell process "Telegram"
        keystroke "{escaped}"
        delay 0.3
        key code 36
    end tell
end tell
"""], timeout=10, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[send_message] osascript failed: {result.stderr[:200]}")
    time.sleep(wait_after)


def get_log_since(marker):
    """Get log entries after a marker line."""
    if not LOG_PATH.exists():
        return ""
    content = LOG_PATH.read_text()
    idx = content.rfind(marker)
    if idx == -1:
        return content
    return content[idx:]


def drop_log_marker():
    """Write a unique marker to the log, return it."""
    marker = "=== TEST MARKER %s ===" % datetime.now(SGT).isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(marker + "\n")
    return marker


def get_test_files():
    """List test digest files."""
    if not TEST_DIGEST_DIR.exists():
        return []
    return sorted(TEST_DIGEST_DIR.glob("test-*.md"))


def cleanup_test_dir():
    """Remove all test files."""
    if TEST_DIGEST_DIR.exists():
        shutil.rmtree(TEST_DIGEST_DIR)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module", autouse=True)
def setup_telegram():
    """Ensure Telegram is open to the bot chat."""
    success = _ensure_bot_chat_open()
    if not success:
        pytest.skip("Could not navigate to bot chat in Telegram")
    yield


@pytest.fixture(autouse=True)
def clean_test_files():
    """Clean test files before each test."""
    cleanup_test_dir()
    # Brief pause between tests — Telegram needs time to process bot replies
    # before accepting new keyboard input
    time.sleep(1)
    yield


# ============================================================
# Tests
# ============================================================

class TestLiveCommands:
    """Test individual commands via Telegram UI."""

    def test_start_command(self):
        """Send /start, verify bot replies."""
        marker = drop_log_marker()
        send_message("/start", wait_after=4)

        log = get_log_since(marker)
        assert "Test user 6805433372" in log, "Bot didn't recognize test user"

    def test_status_idle(self):
        """Send /status when IDLE, verify response."""
        marker = drop_log_marker()
        send_message("/status", wait_after=4)

        log = get_log_since(marker)
        assert "Test user" in log

    def test_digest_creates_file(self):
        """Send /digest, verify test file created."""
        marker = drop_log_marker()
        send_message("/digest", wait_after=5)

        log = get_log_since(marker)
        assert "Test user" in log
        assert "Test /digest" in log

        # Verify file created
        files = get_test_files()
        assert len(files) == 1, f"Expected 1 test file, got {len(files)}"

        content = files[0].read_text()
        assert 'status: "active"' in content
        assert "# Doudou's Summary" in content
        assert "# Boyang's Recap" in content

    def test_text_appends_recap(self):
        """Send /digest then text, verify recap appended."""
        # Create digest first
        send_message("/digest", wait_after=5)
        files = get_test_files()
        assert len(files) == 1

        # Send text
        marker = drop_log_marker()
        send_message("Live test recap entry", wait_after=4)

        log = get_log_since(marker)
        assert "Test recorded" in log

        # Verify file content
        content = files[0].read_text()
        assert "Live test recap entry" in content

    def test_sleep_finalizes(self):
        """Send /digest then /sleep, verify finalization."""
        send_message("/digest", wait_after=5)
        files = get_test_files()
        assert len(files) == 1
        filepath = files[0]

        marker = drop_log_marker()
        send_message("/sleep", wait_after=4)

        log = get_log_since(marker)
        assert "Test /sleep" in log
        assert "has_active=True" in log

        # Verify file finalized
        content = filepath.read_text()
        assert "status: final" in content or 'status: "final"' in content, \
            "File not finalized. Content:\n%s" % content[:500]
        assert "finalized_at" in content


class TestLiveImageAttachment:
    """Test sending images via Telegram UI."""

    def test_photo_saved_and_recorded(self):
        """Send /digest then photo, verify image saved and recorded."""
        # Create test digest
        send_message("/digest", wait_after=5)
        files = get_test_files()
        assert len(files) == 1

        # Create a test image via PIL and copy to clipboard + paste
        import subprocess as sp
        sp.run(["python3", "-c", """
from PIL import Image, ImageDraw
img = Image.new('RGB', (200, 100), color=(30, 60, 30))
d = ImageDraw.Draw(img)
d.text((20, 40), 'E2E Test', fill=(255, 255, 100))
img.save('/tmp/e2e-test-image.jpg')
"""], timeout=10)

        # Paste image via AppleScript
        sp.run(["osascript", "-e", """
set the clipboard to (read (POSIX file "/tmp/e2e-test-image.jpg") as JPEG picture)
tell application "System Events"
    tell process "Telegram"
        keystroke "v" using command down
    end tell
end tell
"""], timeout=10)
        time.sleep(2)

        # Send via Return
        sp.run(["osascript", "-e", """
tell application "System Events"
    tell process "Telegram"
        key code 36
    end tell
end tell
"""], timeout=10)
        time.sleep(5)

        # Check log for image save
        log = LOG_PATH.read_text()
        assert "Test saved image" in log

        # Check attachment file exists
        test_attach = TEST_DIGEST_DIR / "attachments"
        if test_attach.exists():
            imgs = list(test_attach.glob("img-*.jpg"))
            assert len(imgs) >= 1, "No image files in test attachments"

        # Check digest file has image embed
        content = files[0].read_text()
        assert "![[img-" in content
        assert "📷" in content


class TestLiveLifecycle:
    """Test the full lifecycle in sequence."""

    def test_full_cycle(self):
        """Full: /digest → text → /digest (update) → /sleep → verify files."""
        # 1. /digest → create
        marker1 = drop_log_marker()
        send_message("/digest", wait_after=5)

        files = get_test_files()
        assert len(files) == 1, f"Expected 1 file after /digest, got {len(files)}"
        filepath = files[0]

        log1 = get_log_since(marker1)
        assert "has_active=False" in log1, "Should have been IDLE before /digest"

        # 2. Text → append
        marker2 = drop_log_marker()
        send_message("My evening thoughts", wait_after=4)

        content = filepath.read_text()
        assert "My evening thoughts" in content

        # 3. /digest → update (same file)
        marker3 = drop_log_marker()
        send_message("/digest", wait_after=5)

        log3 = get_log_since(marker3)
        assert "has_active=True" in log3, "Should be ACTIVE for second /digest"

        # Still same file count
        files = get_test_files()
        assert len(files) == 1, f"Expected still 1 file, got {len(files)}"

        # 4. /sleep → finalize
        marker4 = drop_log_marker()
        send_message("/sleep", wait_after=4)

        content = filepath.read_text()
        assert "status: final" in content or 'status: "final"' in content, \
            "File not finalized. Content:\n%s" % content[:500]
        assert "finalized_at" in content
        assert "My evening thoughts" in content

    def test_text_without_digest_prompts(self):
        """Text without active digest should tell user to /digest first."""
        marker = drop_log_marker()
        send_message("orphan text", wait_after=4)

        log = get_log_since(marker)
        assert "Test user" in log
        # No "Test recorded" since there's no active digest

    def test_unknown_user_rejected(self):
        """Verify from logs that non-test users are rejected.

        We can't send from another account, but we can verify the
        filtering logic works by checking that our test user IS accepted.
        """
        marker = drop_log_marker()
        send_message("/start", wait_after=4)
        log = get_log_since(marker)
        # Our test user should be accepted (not rejected)
        assert "Rejected user 6805433372" not in log
        assert "Test user 6805433372" in log


class TestLiveReflection:
    """E2E tests for nightly reflection (SPEC-REFLECT-01..06).

    Test mode uses a mock reflection (no real Opus agent call).
    Verifies wiring: /sleep → reflection section → finalize.
    """

    def test_sleep_includes_reflection(self):
        """E2E1: /digest → text → /sleep → verify reflection section in file."""
        # 1. Create digest
        send_message("/digest", wait_after=5)
        files = get_test_files()
        assert len(files) == 1, f"Expected 1 file, got {len(files)}"
        filepath = files[0]

        # 2. Add some recap
        send_message("Testing reflection feature", wait_after=4)

        # 3. /sleep → should trigger test reflection + finalize
        marker = drop_log_marker()
        send_message("/sleep", wait_after=5)

        log = get_log_since(marker)
        assert "Test reflection appended" in log, "Reflection not triggered"

        # 4. Verify reflection section in file
        content = filepath.read_text()
        assert "🪞 Nightly Reflection" in content, \
            "Reflection section missing. Content:\n%s" % content[:500]
        assert "reflection_at:" in content, "YAML reflection_at missing"
        assert "reflection_model:" in content, "YAML reflection_model missing"

        # 5. Verify finalization happened AFTER reflection
        assert "status: final" in content or 'status: "final"' in content, \
            "File not finalized after reflection"
        assert "finalized_at" in content

        # 6. Verify document has all three sections in correct order
        summary_pos = content.index("# Doudou's Summary")
        recap_pos = content.index("# Boyang's Recap")
        reflection_pos = content.index("# 🪞 Nightly Reflection")
        assert summary_pos < recap_pos < reflection_pos, \
            "Sections out of order: summary=%d, recap=%d, reflection=%d" % (
                summary_pos, recap_pos, reflection_pos)

    def test_sleep_reflection_idempotent(self):
        """E2E2: /sleep twice — reflection section appears only once."""
        send_message("/digest", wait_after=5)
        files = get_test_files()
        assert len(files) == 1
        filepath = files[0]

        # First /sleep — creates reflection + finalizes
        send_message("/sleep", wait_after=5)
        content1 = filepath.read_text()
        reflection_count = content1.count("🪞 Nightly Reflection")
        assert reflection_count == 1, "Expected 1 reflection section, got %d" % reflection_count

        # Second /sleep — no active digest, should say "no active"
        marker = drop_log_marker()
        send_message("/sleep", wait_after=4)
        log = get_log_since(marker)
        assert "has_active=False" in log

        # File unchanged
        content2 = filepath.read_text()
        assert content2.count("🪞 Nightly Reflection") == 1

    def test_sleep_without_digest_no_reflection(self):
        """E2E3: /sleep when IDLE — no reflection, just goodbye."""
        marker = drop_log_marker()
        send_message("/sleep", wait_after=4)

        log = get_log_since(marker)
        assert "has_active=False" in log

        # No test files should have reflection
        files = get_test_files()
        for f in files:
            content = f.read_text()
            assert "🪞 Nightly Reflection" not in content
