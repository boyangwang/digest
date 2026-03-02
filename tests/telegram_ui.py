"""
Telegram Desktop UI automation via Peekaboo.

Provides functions to send messages and read replies from the
@sleep_digest_bot chat in the Mac Mini's Telegram Desktop client.

Usage:
    from telegram_ui import navigate_to_bot, send_message, read_last_bot_reply
    navigate_to_bot()
    send_message("/digest")
    reply = read_last_bot_reply()
"""

import json
import subprocess
import time


BOT_USERNAME = "@sleep_digest_bot"
PEEKABOO = "peekaboo"


def _run(cmd, timeout=15):
    """Run a shell command, return stdout."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result.stdout.strip()


def _peekaboo(args, timeout=15):
    """Run a peekaboo command."""
    return _run(f"{PEEKABOO} {args}", timeout=timeout)


def _get_elements():
    """Get current UI elements as a list of dicts."""
    raw = _peekaboo("see --app Telegram --json 2>/dev/null", timeout=20)
    try:
        data = json.loads(raw)
        return data.get("data", {}).get("ui_elements", [])
    except (json.JSONDecodeError, KeyError):
        return []


def _find_element(role=None, label_contains=None):
    """Find an element by role and/or label substring."""
    elements = _get_elements()
    for e in elements:
        if role and e.get("role") != role:
            continue
        if label_contains and label_contains.lower() not in e.get("label", "").lower():
            continue
        return e
    return None


def focus_telegram():
    """Bring Telegram to front."""
    _peekaboo("app switch --to Telegram")
    time.sleep(0.5)


def navigate_to_bot():
    """Navigate to the @sleep_digest_bot chat.
    
    Returns True if the chat is open with a message input field.
    """
    focus_telegram()

    # Check if we're already in the bot chat with input field
    input_el = _find_element(role="textField", label_contains="write a message")
    if input_el:
        return True

    # Search for the bot
    search_el = _find_element(role="textField", label_contains="search")
    if not search_el:
        return False

    _peekaboo(f"click --on {search_el['id']} --app Telegram")
    time.sleep(0.3)
    _peekaboo(f'type "{BOT_USERNAME}" --app Telegram')
    time.sleep(1.5)

    # Click search result (coordinates-based since results aren't accessible)
    _peekaboo("click --app Telegram --coords 250,180")
    time.sleep(0.5)

    # Verify input field appeared
    input_el = _find_element(role="textField", label_contains="write a message")
    return input_el is not None


def send_message(text):
    """Type and send a message in the currently open chat.

    Uses AppleScript keystroke for reliability — Telegram's command autocomplete
    menu intercepts Peekaboo's `type` when text starts with `/`.

    Returns True on success.
    """
    input_el = _find_element(role="textField", label_contains="write a message")
    if not input_el:
        return False

    _peekaboo(f"click --on {input_el['id']} --app Telegram")
    time.sleep(0.2)

    # Use AppleScript keystroke — reliable even with Telegram autocomplete
    escaped = text.replace('"', '\\"')
    _run(
        f'osascript -e \'tell application "System Events" to tell process "Telegram" '
        f'to keystroke "{escaped}"\'',
        timeout=5,
    )
    time.sleep(0.3)
    # Press Return via AppleScript (key code 36)
    _run(
        'osascript -e \'tell application "System Events" to tell process "Telegram" '
        'to key code 36\'',
        timeout=5,
    )
    return True


def read_last_bot_reply(wait_seconds=5):
    """Read the bot's last reply using Peekaboo vision analysis.
    
    Waits up to wait_seconds for a reply, then reads via AI analysis.
    Returns the reply text or None.
    """
    time.sleep(wait_seconds)
    output = _peekaboo(
        'see --app Telegram --analyze "What is the LAST message from the bot '
        '(Sleep Digest)? Return ONLY the bot message text, nothing else."',
        timeout=30,
    )

    # Parse the AI analysis from peekaboo output
    if "AI Analysis" in output:
        lines = output.split("AI Analysis")[1].strip().split("\n")
        # Get text until the element summary
        result = []
        for line in lines:
            if "Element Summary" in line or "Snapshot ID" in line:
                break
            result.append(line.strip())
        text = "\n".join(result).strip()
        return text if text else None
    return None


def read_all_visible_messages(wait_seconds=3):
    """Read all visible messages in the chat.
    
    Returns the full analysis text.
    """
    time.sleep(wait_seconds)
    output = _peekaboo(
        'see --app Telegram --analyze "List ALL visible messages in the chat, '
        'indicating who sent each (user or bot). Include the full text of each message."',
        timeout=30,
    )
    if "AI Analysis" in output:
        lines = output.split("AI Analysis")[1].strip().split("\n")
        result = []
        for line in lines:
            if "Element Summary" in line or "Snapshot ID" in line:
                break
            result.append(line.strip())
        return "\n".join(result).strip()
    return None


if __name__ == "__main__":
    # Quick test
    print("Navigating to bot...")
    success = navigate_to_bot()
    print(f"Navigation: {'OK' if success else 'FAILED'}")

    if success:
        print("Sending test message...")
        send_message("ping")
        reply = read_last_bot_reply(wait_seconds=3)
        print(f"Bot reply: {reply}")
