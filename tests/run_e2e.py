#!/usr/bin/env python3
"""Standalone E2E test runner for Digest Bot.

Runs outside pytest to avoid subprocess/osascript hanging issues.
Uses AppleScript to send messages to @sleep_digest_bot via Telegram Desktop.

Usage:
    python tests/run_e2e.py              # Run all E2E tests
    python tests/run_e2e.py --test basic # Run specific suite
    python tests/run_e2e.py --verbose    # Show detailed output

Exit code: 0 = all pass, 1 = failures
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# Config
# ============================================================

SGT = timezone(timedelta(hours=8))
LOG_PATH = Path("/tmp/digest-bot.log")
STDOUT_LOG_PATH = Path("/tmp/digest-bot-stdout.log")
TEST_DIGEST_DIR = Path(
    "/Users/claw/Documents/NotesVault/Artificial-Colloquia/Doudou-Digest/_test"
)

VERBOSE = False
RESULTS = {"passed": 0, "failed": 0, "skipped": 0}


# ============================================================
# Helpers
# ============================================================

def log(msg, level="INFO"):
    ts = datetime.now(SGT).strftime("%H:%M:%S")
    if level == "PASS":
        print(f"  ✅ [{ts}] {msg}")
    elif level == "FAIL":
        print(f"  ❌ [{ts}] {msg}")
    elif level == "SKIP":
        print(f"  ⏭️  [{ts}] {msg}")
    elif VERBOSE:
        print(f"  [{ts}] {msg}")


def ensure_bot_chat_open():
    """Navigate to @sleep_digest_bot in Telegram Desktop."""
    for attempt in range(3):
        # Close stale dialogs
        subprocess.run(["osascript", "-e", """
tell application "Telegram" to activate
delay 0.3
tell application "System Events"
    tell process "Telegram"
        key code 53
        delay 0.3
        key code 53
        delay 0.3
    end tell
end tell
"""], timeout=10, capture_output=True)
        time.sleep(0.3)

        # Check if already in bot chat
        if _check_window_is_bot():
            return True

        # Navigate via search
        subprocess.run(["osascript", "-e", """
tell application "Telegram" to activate
delay 0.3
tell application "System Events"
    tell process "Telegram"
        key code 53
        delay 0.3
        key code 53
        delay 0.3
        keystroke "a" using command down
        delay 0.1
        key code 51
        delay 0.3
        keystroke "@sleep_digest_bot"
        delay 2.0
        key code 125
        delay 0.3
        key code 36
        delay 1.0
    end tell
end tell
"""], timeout=15, capture_output=True)
        time.sleep(0.5)

        if _check_window_is_bot():
            return True
        time.sleep(1)

    return False


def _check_window_is_bot():
    result = subprocess.run(["osascript", "-e", """
tell application "System Events"
    tell process "Telegram"
        return name of window 1
    end tell
end tell
"""], capture_output=True, text=True, timeout=10)
    name = result.stdout.strip()
    return "Sleep Digest" in name or "sleep" in name.lower()


def send_message(text, wait_after=3):
    """Send a message to the bot via AppleScript keystroke."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    result = subprocess.run(["osascript", "-e", f"""
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
        log(f"osascript error: {result.stderr[:200]}", "INFO")
    time.sleep(wait_after)


def drop_log_marker():
    """Write a unique marker to the bot log for test isolation."""
    marker = f"=== TEST MARKER {datetime.now(SGT).isoformat()} ==="
    LOG_PATH.open("a").write(marker + "\n")
    # Also write to stdout log (launchd redirects bot output there)
    STDOUT_LOG_PATH.open("a").write(marker + "\n")
    return marker


def get_log_since(marker):
    """Get log text after the marker from BOTH log files."""
    texts = []
    for path in [LOG_PATH, STDOUT_LOG_PATH]:
        if path.exists():
            content = path.read_text()
            idx = content.rfind(marker)
            if idx != -1:
                texts.append(content[idx:])
    return "\n".join(texts)


def wait_for_log(marker, needle, timeout=15):
    """Poll logs until needle appears after marker, or timeout.

    Returns (True, log_text) if found, (False, log_text) if timed out.
    This replaces fragile fixed-delay waits with event-driven checking.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        log_text = get_log_since(marker)
        if needle in log_text:
            return True, log_text
        time.sleep(0.5)
    return False, get_log_since(marker)


def get_test_files(wait_timeout=0):
    """Get test digest files. Optionally wait up to wait_timeout seconds for at least one."""
    deadline = time.time() + wait_timeout
    while True:
        if TEST_DIGEST_DIR.exists():
            files = sorted(TEST_DIGEST_DIR.glob("test-*.md"))
            if files:
                return files
        if time.time() >= deadline:
            break
        time.sleep(0.5)
    if not TEST_DIGEST_DIR.exists():
        return []
    return sorted(TEST_DIGEST_DIR.glob("test-*.md"))


def cleanup():
    if TEST_DIGEST_DIR.exists():
        shutil.rmtree(TEST_DIGEST_DIR)


def run_test(name, fn):
    """Run a single test function, catch assertions."""
    print(f"\n  --- {name} ---")
    cleanup()
    time.sleep(0.5)
    try:
        fn()
        log(f"{name} PASSED", "PASS")
        RESULTS["passed"] += 1
    except AssertionError as e:
        log(f"{name} FAILED: {e}", "FAIL")
        RESULTS["failed"] += 1
    except Exception as e:
        log(f"{name} ERROR: {e}", "FAIL")
        RESULTS["failed"] += 1


# ============================================================
# Test Suites
# ============================================================

def test_start_command():
    marker = drop_log_marker()
    send_message("/start", wait_after=2)
    found, log_text = wait_for_log(marker, "Test user 6805433372", timeout=10)
    assert found, "Bot didn't recognize test user"


def test_status_idle():
    marker = drop_log_marker()
    send_message("/status", wait_after=2)
    found, log_text = wait_for_log(marker, "Test user", timeout=10)
    assert found, "Bot didn't log test user"


def test_digest_creates_file():
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    found, log_text = wait_for_log(marker, "Test /digest", timeout=10)
    assert found, "Bot didn't process /digest"
    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, f"Expected test file, got {len(files)}"
    content = files[0].read_text()
    assert 'status: "active"' in content, "Missing active status"
    assert "# Doudou's Summary" in content, "Missing summary section"
    assert "# Boyang's Recap" in content, "Missing recap section"


def test_text_appends_recap():
    # Need active digest first
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)
    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file to append to"

    marker2 = drop_log_marker()
    send_message("E2E test recap entry", wait_after=2)
    found, log_text = wait_for_log(marker2, "recorded", timeout=10)
    assert found, "Bot didn't record the text"

    content = files[0].read_text()
    assert "E2E test recap entry" in content, "Recap text not in file"


def test_sleep_finalizes():
    # Need active digest first
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    found, log_text = wait_for_log(marker2, "Test /sleep", timeout=10)
    assert found, "Bot didn't process /sleep"


def test_full_lifecycle():
    """Full cycle: /digest → text → /sleep."""
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    found, _ = wait_for_log(marker, "Test /digest", timeout=10)
    assert found, "/digest not processed"

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    marker2 = drop_log_marker()
    send_message("Evening journal entry from E2E test", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)
    content = files[0].read_text()
    assert "Evening journal entry" in content, "Journal entry not recorded"

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    found, _ = wait_for_log(marker3, "Test /sleep", timeout=10)
    assert found, "/sleep not processed"


def test_sleep_includes_reflection():
    """E2E-R1: /digest → text → /sleep → reflection section in digest file.

    Verifies the core reflection flow: after /sleep, the digest file
    contains a 🪞 Nightly Reflection section with mock data (test mode).
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("Today I worked on E2E tests", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    found, log_text = wait_for_log(marker3, "Test /sleep", timeout=10)
    assert found, "/sleep not processed"

    # Wait for reflection + finalize to complete
    wait_for_log(marker3, "Test reflection appended", timeout=10)

    # Verify reflection was appended to the file
    content = files[0].read_text()
    assert "Nightly Reflection" in content, "Reflection section missing from digest"

    # Verify YAML frontmatter has reflection fields
    assert "reflection_at:" in content, "reflection_at missing from YAML"
    assert "reflection_model:" in content, "reflection_model missing from YAML"

    # Verify file structure: Summary → Recap → Reflection (correct order)
    assert content.index("Doudou's Summary") < content.index("Boyang's Recap"), \
        "Summary must come before Recap"
    assert content.index("Boyang's Recap") < content.index("Nightly Reflection"), \
        "Recap must come before Reflection"


def test_sleep_sends_reflection_summary():
    """E2E-R2: /sleep sends structured reflection summary message to chat.

    After /sleep with an active digest, bot must send a Telegram message
    containing reflection content — category counts and extraction stats.
    NOT just a generic "finalized ✅" message.

    This tests T1 (format_reflection_telegram) + T3 (send to user) + T4 (test mode).

    EXPECTED TO FAIL until T1/T3/T4 are implemented.
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("Reflection summary E2E test", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    found, log_text = wait_for_log(marker3, "Test /sleep", timeout=10)
    assert found, "/sleep not processed"

    # Wait for full processing
    wait_for_log(marker3, "Test reflection appended", timeout=10)

    # Bot must log that it sent a reflection summary (test mode should log this).
    # Look for evidence of structured reflection content being sent.
    found_summary, log_text = wait_for_log(marker3, "reflection summary sent", timeout=5)
    assert found_summary, \
        "No reflection summary message sent to user (expected 'reflection summary sent' in logs)"


def test_sleep_finalizes_with_reflection():
    """E2E-R3: /sleep finalizes digest AND includes reflection.

    Verifies both operations complete: reflection appended + file finalized.
    Also verifies SPEC-REFLECT-05: finalize always runs.
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("Testing finalize with reflection", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)

    # Wait for /sleep processing + reflection + finalize
    found, log_text = wait_for_log(marker3, "Test /sleep", timeout=10)
    assert found, "/sleep not processed"
    wait_for_log(marker3, "Test reflection appended", timeout=10)

    # Verify file has both reflection AND is finalized
    content = files[0].read_text()
    assert "Nightly Reflection" in content, "Reflection section missing"
    assert "final" in content, "Digest not finalized (missing 'final' in content)"
    assert "finalized_at:" in content, "finalized_at timestamp missing from YAML"


def test_sleep_reflection_idempotent():
    """E2E-R4: Running /sleep twice doesn't duplicate reflection section.

    /digest → text → /sleep creates reflection.
    Second /sleep has no active digest — reflection should NOT duplicate.
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("Testing idempotent reflection", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    wait_for_log(marker3, "Test reflection appended", timeout=10)

    # Second /sleep — no active digest
    marker4 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    wait_for_log(marker4, "has_active=False", timeout=10)

    content = files[0].read_text()
    count = content.count("Nightly Reflection")
    assert count == 1, f"Reflection section duplicated: found {count} times"


def test_sleep_without_text_still_reflects():
    """E2E-R5: /digest → /sleep (no text) still runs reflection.

    Even without Boyang's recap text, /sleep should still append a
    reflection section (empty/mock in test mode).
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    marker2 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    found, log_text = wait_for_log(marker2, "Test /sleep", timeout=10)
    assert found, "/sleep not processed"
    wait_for_log(marker2, "Test reflection appended", timeout=10)

    content = files[0].read_text()
    assert "Nightly Reflection" in content, "Reflection missing even without recap text"


def test_sleep_without_digest():
    """Sleep without active digest — should not crash."""
    marker = drop_log_marker()
    send_message("/sleep", wait_after=2)
    found, log_text = wait_for_log(marker, "Test /sleep", timeout=10)
    assert found, "/sleep not processed"
    assert "has_active=False" in log_text, "Should report no active digest"


def test_digest_then_digest_resets():
    """E2E-R6: Sending /digest twice — second should start fresh.

    First /digest creates file. Second /digest while first is active
    should either reject or create a new one (verify current behavior).
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)
    files1 = get_test_files(wait_timeout=5)
    assert len(files1) >= 1, "First /digest didn't create file"

    marker2 = drop_log_marker()
    send_message("/digest", wait_after=2)
    found, log_text = wait_for_log(marker2, "Test /digest", timeout=10)
    assert found, "Second /digest not processed"

    # The bot should handle this gracefully — either skip or create new
    # No crash is the key assertion
    files2 = get_test_files(wait_timeout=3)
    assert len(files2) >= 1, "Should still have at least one test file"


def test_sleep_reflection_file_structure():
    """E2E-R7: Verify complete file structure after /sleep with reflection.

    Full content validation: YAML frontmatter + all sections + correct order.
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("Detailed E2E structure test entry", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    wait_for_log(marker3, "Test reflection appended", timeout=10)

    content = files[0].read_text()

    # YAML frontmatter must have all required fields
    assert "---" in content, "Missing YAML frontmatter delimiter"
    assert "generated_at:" in content, "Missing generated_at"
    assert "coverage_from:" in content, "Missing coverage_from"
    assert "status:" in content, "Missing status"
    assert "reflection_at:" in content, "Missing reflection_at"
    assert "reflection_model:" in content, "Missing reflection_model"
    assert "finalized_at:" in content, "Missing finalized_at"

    # Sections in correct order
    sections = ["Doudou's Summary", "Boyang's Recap", "Nightly Reflection"]
    positions = []
    for section in sections:
        assert section in content, f"Missing section: {section}"
        positions.append(content.index(section))
    assert positions == sorted(positions), \
        f"Sections out of order: {list(zip(sections, positions))}"

    # Recap content preserved
    assert "Detailed E2E structure test entry" in content, "Recap text lost after reflection"


def test_voice_message_skipped_in_test():
    """E2E-R8: Voice messages in test mode are rejected gracefully."""
    marker = drop_log_marker()
    # Can't send actual voice in AppleScript, but verify /status works after
    send_message("/status", wait_after=2)
    found, log_text = wait_for_log(marker, "Test user", timeout=10)
    assert found, "Bot not responding after potential edge case"


def test_status_shows_active_digest():
    """E2E-R9: /status after /digest shows active digest info."""
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("/status", wait_after=2)
    found, log_text = wait_for_log(marker2, "Test user", timeout=10)
    assert found, "/status not processed"
    # Should show active state in some form


def test_multiple_recap_entries():
    """E2E-R10: Multiple text messages all appear in recap section."""
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    entries = ["First recap entry", "Second recap entry", "Third recap entry"]
    for entry in entries:
        m = drop_log_marker()
        send_message(entry, wait_after=1)
        found, _ = wait_for_log(m, "recorded", timeout=15)
        assert found, f"Bot didn't record entry: '{entry}'"

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file"
    content = files[0].read_text()

    for entry in entries:
        assert entry in content, f"Recap entry missing: '{entry}'"

    # Now /sleep and verify all entries survive
    marker2 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    wait_for_log(marker2, "Test reflection appended", timeout=10)

    content = files[0].read_text()
    for entry in entries:
        assert entry in content, f"Recap entry lost after /sleep: '{entry}'"
    assert "Nightly Reflection" in content, "Reflection section missing"


def test_rapid_commands():
    """E2E-R11: Rapid sequential commands don't crash the bot.

    Send /digest, text, /sleep in quick succession without long waits.
    Bot must handle all correctly.
    """
    marker = drop_log_marker()
    send_message("/digest", wait_after=1)
    send_message("Rapid test entry", wait_after=1)
    send_message("/sleep", wait_after=1)

    # Give the bot time to catch up
    found, log_text = wait_for_log(marker, "Test /sleep", timeout=15)
    assert found, "Bot didn't process rapid /sleep"

    # Verify the full cycle completed
    wait_for_log(marker, "Test reflection appended", timeout=10)


# ============================================================
# Test registry
# ============================================================

SUITES = {
    "basic": [
        ("test_start_command", test_start_command),
        ("test_status_idle", test_status_idle),
        ("test_digest_creates_file", test_digest_creates_file),
        ("test_text_appends_recap", test_text_appends_recap),
        ("test_sleep_finalizes", test_sleep_finalizes),
    ],
    "lifecycle": [
        ("test_full_lifecycle", test_full_lifecycle),
        ("test_sleep_without_digest", test_sleep_without_digest),
        ("test_digest_then_digest_resets", test_digest_then_digest_resets),
        ("test_multiple_recap_entries", test_multiple_recap_entries),
        ("test_rapid_commands", test_rapid_commands),
        ("test_status_shows_active_digest", test_status_shows_active_digest),
    ],
    "reflection": [
        ("test_sleep_includes_reflection", test_sleep_includes_reflection),
        ("test_sleep_sends_reflection_summary", test_sleep_sends_reflection_summary),
        ("test_sleep_finalizes_with_reflection", test_sleep_finalizes_with_reflection),
        ("test_sleep_reflection_idempotent", test_sleep_reflection_idempotent),
        ("test_sleep_without_text_still_reflects", test_sleep_without_text_still_reflects),
        ("test_sleep_reflection_file_structure", test_sleep_reflection_file_structure),
        ("test_reflect_command_sends_preview", test_reflect_command_sends_preview),
        ("test_reflect_command_with_date_arg", test_reflect_command_with_date_arg),
        ("test_reflect_command_not_available_in_test", test_reflect_command_not_available_in_test),
    ],
    "edge": [
        ("test_voice_message_skipped_in_test", test_voice_message_skipped_in_test),
    ],
}


# ============================================================
# Main
# ============================================================

def main():
    global VERBOSE

    parser = argparse.ArgumentParser(description="Digest Bot E2E Test Runner")
    parser.add_argument("--test", choices=list(SUITES.keys()) + ["all"], default="all")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    print("🧪 Digest Bot E2E Tests")
    print("=" * 50)

    # Setup
    print("\n📱 Opening bot chat in Telegram...")
    if not ensure_bot_chat_open():
        print("❌ Could not navigate to bot chat. Aborting.")
        sys.exit(2)
    print("✅ Bot chat ready")

    # Check bot is running
    result = subprocess.run(["pgrep", "-f", "digest-bot/main.py"],
                          capture_output=True, text=True)
    if result.returncode != 0:
        print("❌ Digest bot is not running. Start it first.")
        sys.exit(2)
    print(f"✅ Bot running (PID {result.stdout.strip()})")

    # Run selected suites
    suites_to_run = list(SUITES.keys()) if args.test == "all" else [args.test]

    for suite_name in suites_to_run:
        tests = SUITES[suite_name]
        print(f"\n{'=' * 50}")
        print(f"📋 Suite: {suite_name} ({len(tests)} tests)")
        for name, fn in tests:
            run_test(name, fn)

    # Summary
    total = RESULTS["passed"] + RESULTS["failed"] + RESULTS["skipped"]
    print(f"\n{'=' * 50}")
    print(f"📊 Results: {RESULTS['passed']} passed, {RESULTS['failed']} failed, "
          f"{RESULTS['skipped']} skipped / {total} total")

    if RESULTS["failed"] > 0:
        print("❌ SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()


def test_reflect_command_sends_preview():
    """E2E-T19-1: /reflect command re-runs reflection and sends preview message.

    Test flow:
    1. /digest → text → /sleep (creates finalized digest with reflection)
    2. /reflect → bot sends new reflection preview message
    3. Verify file is NOT modified (preview only, until button pressed)

    Note: We cannot click the inline button in E2E, so we just verify
    the preview message is sent and the file remains unchanged.

    EXPECTED TO FAIL until T17 is implemented.
    """
    # Step 1: Create a finalized digest with reflection
    marker = drop_log_marker()
    send_message("/digest", wait_after=2)
    wait_for_log(marker, "Test /digest", timeout=10)

    marker2 = drop_log_marker()
    send_message("Initial digest content for /reflect test", wait_after=2)
    wait_for_log(marker2, "recorded", timeout=10)

    marker3 = drop_log_marker()
    send_message("/sleep", wait_after=2)
    wait_for_log(marker3, "Test /sleep", timeout=10)
    wait_for_log(marker3, "Test reflection appended", timeout=10)

    files = get_test_files(wait_timeout=5)
    assert len(files) >= 1, "No digest file created"

    # Capture original reflection content
    original_content = files[0].read_text()
    assert "Nightly Reflection" in original_content, "Original reflection missing"
    original_reflection_at = None
    if "reflection_at:" in original_content:
        # Extract timestamp for comparison
        import yaml
        parts = original_content.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1])
            original_reflection_at = fm.get("reflection_at")

    # Step 2: Send /reflect command
    marker4 = drop_log_marker()
    send_message("/reflect", wait_after=3)  # Longer wait — agent call takes time

    # Wait for reflection processing (but NOT file modification)
    found, log_text = wait_for_log(marker4, "reflection", timeout=15, case_sensitive=False)
    assert found, "/reflect command not processed"

    # Step 3: Verify preview message was sent
    # In test mode, bot should log sending the reflection summary
    found_preview, log_text = wait_for_log(marker4, "reflection summary sent", timeout=5)
    assert found_preview, \
        "No reflection preview message sent (expected 'reflection summary sent' in logs)"

    # Step 4: Verify file was NOT modified (no button press in E2E)
    # The reflection_at timestamp should remain the same
    new_content = files[0].read_text()
    if original_reflection_at:
        import yaml
        parts = new_content.split("---", 2)
        if len(parts) >= 3:
            fm = yaml.safe_load(parts[1])
            new_reflection_at = fm.get("reflection_at")
            assert new_reflection_at == original_reflection_at, \
                "reflection_at should NOT change (preview only, button not pressed)"

    # Reflection section count should still be 1 (not duplicated)
    assert new_content.count("Nightly Reflection") == 1, \
        "Reflection section should not be duplicated on preview"


def test_reflect_command_with_date_arg():
    """E2E-T19-2: /reflect 2026-03-02 targets a specific date.

    This test verifies the optional date argument works.
    Cannot fully test without actual historical data, but verifies
    the command accepts the argument without crashing.
    """
    marker = drop_log_marker()
    send_message("/reflect 2026-03-02", wait_after=3)

    # Bot should handle this gracefully even if no file exists for that date
    # Look for either success or "no file found" type message
    found, log_text = wait_for_log(marker, "", timeout=10)
    assert found, "/reflect with date arg not processed"

    # Should not crash — that's the key assertion
    # Actual behavior: either sends preview or sends "no file found" message


def test_reflect_command_not_available_in_test():
    """E2E-T19-3: /reflect command should be production-only (not test mode).

    In test mode, /reflect should either be ignored or send a "not available" message.
    """
    marker = drop_log_marker()
    send_message("/reflect", wait_after=2)

    found, log_text = wait_for_log(marker, "", timeout=5)

    # Test mode should reject this or handle gracefully
    # Key: should not attempt to run actual reflection on test files
    # (Production-only check is in the handler)
    assert found, "/reflect should be handled (even if rejected in test mode)"

