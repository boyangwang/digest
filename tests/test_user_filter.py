"""
Tests for user allowlist and test mode filtering.

Covers:
1. Allowed users (Boyang) get normal processing
2. Test users get 🧪-prefixed test mode
3. Unknown users are silently rejected
4. No user → rejected
5. Test mode uses separate state (doesn't contaminate production)
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BOYANG_USER_ID, SGT


# ============================================================
# Helpers
# ============================================================

def _make_update(text, user_id=BOYANG_USER_ID, username="b0yan913", name="Boyang"):
    """Create a mock Telegram Update with proper user info."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.chat_id = user_id
    update.message.from_user = MagicMock()
    update.message.from_user.id = user_id
    update.message.from_user.username = username
    update.message.from_user.first_name = name
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    return update


def _make_update_no_user(text):
    """Create a mock Update with no user info."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.from_user = None
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    update.effective_user = None
    return update


UNKNOWN_USER_ID = 999999999


# ============================================================
# Test: _check_user function
# ============================================================

class TestCheckUser:
    """Unit tests for the _check_user filter function."""

    def test_boyang_allowed(self):
        """Boyang (411364623) should be allowed and not flagged as test."""
        import main
        update = _make_update("/start", user_id=BOYANG_USER_ID)
        allowed, is_test = main._check_user(update)
        assert allowed is True
        assert is_test is False

    def test_unknown_user_rejected(self):
        """Unknown user should be rejected."""
        import main
        update = _make_update("/start", user_id=UNKNOWN_USER_ID, username="hacker", name="Eve")
        allowed, is_test = main._check_user(update)
        assert allowed is False
        assert is_test is False

    def test_no_user_rejected(self):
        """Message with no user should be rejected."""
        import main
        update = _make_update_no_user("/start")
        allowed, is_test = main._check_user(update)
        assert allowed is False
        assert is_test is False

    def test_test_user_allowed_when_configured(self):
        """When TEST_USER_ID is set, test user is allowed and flagged."""
        import main
        import config

        original = config.TEST_USER_ID
        original_allowed = config.ALLOWED_USER_IDS.copy()
        try:
            config.TEST_USER_ID = 12345
            config.ALLOWED_USER_IDS = {BOYANG_USER_ID, 12345}

            update = _make_update("/start", user_id=12345, username="test", name="Test")
            allowed, is_test = main._check_user(update)
            assert allowed is True
            assert is_test is True
        finally:
            config.TEST_USER_ID = original
            config.ALLOWED_USER_IDS = original_allowed

    def test_test_user_zero_not_in_allowlist(self):
        """TEST_USER_ID=0 should not be in the allowlist."""
        import config
        assert 0 not in config.ALLOWED_USER_IDS


# ============================================================
# Test: Command handlers reject unknown users
# ============================================================

class TestHandlerRejection:
    """Verify all handlers silently reject unknown users."""

    @pytest.mark.asyncio
    async def test_cmd_start_rejects_unknown(self):
        import main
        update = _make_update("/start", user_id=UNKNOWN_USER_ID)
        ctx = MagicMock()
        await main.cmd_start(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_status_rejects_unknown(self):
        import main
        update = _make_update("/status", user_id=UNKNOWN_USER_ID)
        ctx = MagicMock()
        await main.cmd_status(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_digest_rejects_unknown(self):
        import main
        update = _make_update("/digest", user_id=UNKNOWN_USER_ID)
        ctx = MagicMock()
        await main.cmd_digest(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_sleep_rejects_unknown(self):
        import main
        update = _make_update("/sleep", user_id=UNKNOWN_USER_ID)
        ctx = MagicMock()
        await main.cmd_sleep(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_text_rejects_unknown(self):
        import main
        update = _make_update("Hello bot!", user_id=UNKNOWN_USER_ID)
        ctx = MagicMock()
        await main.handle_text(update, ctx)
        update.message.reply_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_photo_rejects_unknown(self):
        import main
        update = _make_update("", user_id=UNKNOWN_USER_ID)
        update.message.photo = [MagicMock()]
        ctx = MagicMock()
        await main.handle_photo(update, ctx)
        update.message.reply_text.assert_not_called()


# ============================================================
# Test: Test mode commands produce 🧪 prefixed responses
# ============================================================

class TestTestMode:
    """Verify test mode produces 🧪 responses and uses separate state."""

    @pytest.fixture(autouse=True)
    def setup_test_user(self, tmp_path):
        """Temporarily configure a test user."""
        import config
        import main

        self.test_id = 12345
        self.orig_test_id = config.TEST_USER_ID
        self.orig_allowed = config.ALLOWED_USER_IDS.copy()

        config.TEST_USER_ID = self.test_id
        config.ALLOWED_USER_IDS = {BOYANG_USER_ID, self.test_id}

        # Point test recorder to tmp dir
        test_dir = tmp_path / "_test"
        main._test_recorder = main.TestRecorder(test_dir)

        yield

        config.TEST_USER_ID = self.orig_test_id
        config.ALLOWED_USER_IDS = self.orig_allowed

    @pytest.mark.asyncio
    async def test_start_shows_test_mode(self):
        import main
        update = _make_update("/start", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.cmd_start(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "🧪" in reply
        assert "TEST MODE" in reply

    @pytest.mark.asyncio
    async def test_status_idle_test_mode(self):
        import main
        update = _make_update("/status", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.cmd_status(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "🧪" in reply
        assert "IDLE" in reply

    @pytest.mark.asyncio
    async def test_digest_creates_test_file(self):
        import main
        update = _make_update("/digest", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.cmd_digest(update, ctx)

        # Should have created a test file
        assert main._test_recorder.has_active()
        assert main._test_recorder.active_file.name.startswith("test-")

        # Reply should contain 🧪
        calls = update.message.reply_text.call_args_list
        replies = [c[0][0] for c in calls]
        assert any("🧪" in r for r in replies)

    @pytest.mark.asyncio
    async def test_text_appends_to_test_file(self):
        import main
        # First create a test digest
        main._test_recorder.create()

        update = _make_update("Test recap text", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.handle_text(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "🧪" in reply
        assert "✍️" in reply

        # Verify file content
        content = main._test_recorder.active_file.read_text()
        assert "Test recap text" in content

    @pytest.mark.asyncio
    async def test_sleep_finalizes_test_file(self):
        import main
        main._test_recorder.create()
        filepath = main._test_recorder.active_file

        update = _make_update("/sleep", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.cmd_sleep(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "🧪" in reply
        assert "finalized" in reply.lower() or "🌙" in reply
        assert not main._test_recorder.has_active()

        # Verify file was finalized
        content = filepath.read_text()
        assert "final" in content

    @pytest.mark.asyncio
    async def test_test_mode_doesnt_touch_production(self):
        """Test commands should NOT affect production recorder state."""
        import main
        import recorder

        # Set production to IDLE
        recorder._active_file = None
        assert not recorder.has_active_file()

        # Run test digest cycle
        update = _make_update("/digest", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.cmd_digest(update, ctx)

        # Production must still be IDLE
        assert not recorder.has_active_file()
        assert recorder._active_file is None

    @pytest.mark.asyncio
    async def test_full_test_lifecycle(self):
        """Full cycle: /digest → text → /digest (update) → /sleep."""
        import main
        ctx = MagicMock()
        uid = self.test_id

        # 1. /digest → creates file
        u1 = _make_update("/digest", user_id=uid, username="test", name="Test")
        await main.cmd_digest(u1, ctx)
        assert main._test_recorder.has_active()

        # 2. Text → appends recap
        u2 = _make_update("My test recap", user_id=uid, username="test", name="Test")
        await main.handle_text(u2, ctx)
        content = main._test_recorder.active_file.read_text()
        assert "My test recap" in content

        # 3. /digest → updates same file
        u3 = _make_update("/digest", user_id=uid, username="test", name="Test")
        await main.cmd_digest(u3, ctx)
        assert main._test_recorder.has_active()

        # 4. /sleep → finalizes
        u4 = _make_update("/sleep", user_id=uid, username="test", name="Test")
        await main.cmd_sleep(u4, ctx)
        assert not main._test_recorder.has_active()

    @pytest.mark.asyncio
    async def test_text_without_active_digest_tells_user(self):
        """Text in test mode without active file should prompt /digest first."""
        import main
        update = _make_update("Some text", user_id=self.test_id, username="test", name="Test")
        ctx = MagicMock()
        await main.handle_text(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "🧪" in reply
        assert "/digest" in reply.lower()
