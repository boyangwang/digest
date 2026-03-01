"""
Tests for config.py — Configuration validation.

Includes P1 regression: bot token must NEVER be the main @claw_reborn_bot token.
"""

import config


class TestTokenSafety:
    """P1 INCIDENT REGRESSION: Nearly swapped main bot token.
    
    The main @claw_reborn_bot token (8304524800:...) must NEVER appear
    in this bot's config. This test exists because of a near-miss incident
    on 2026-03-01 where the wrong token was almost used.
    """

    def test_bot_token_is_not_main_token(self):
        """CRITICAL: Bot token must be @sleep_digest_bot, not @claw_reborn_bot."""
        assert not config.BOT_TOKEN.startswith("8304524800"), \
            "CRITICAL: BOT_TOKEN is the main @claw_reborn_bot token! This would break OpenClaw."

    def test_bot_token_is_sleep_digest(self):
        """Bot token should be the @sleep_digest_bot token."""
        assert config.BOT_TOKEN.startswith("8324650609"), \
            "BOT_TOKEN should be @sleep_digest_bot (8324650609:...)"

    def test_bot_token_not_empty(self):
        assert config.BOT_TOKEN and len(config.BOT_TOKEN) > 10


class TestPaths:
    """Path configuration validation."""

    def test_vault_path_is_absolute(self):
        assert config.VAULT_PATH.is_absolute()

    def test_digest_dir_under_vault(self):
        assert str(config.DIGEST_DIR).startswith(str(config.VAULT_PATH))

    def test_session_dir_under_openclaw(self):
        assert ".openclaw" in str(config.SESSION_DIR)

    def test_sessions_json_path(self):
        assert config.SESSIONS_JSON.name == "sessions.json"


class TestConstants:
    """Timing and limit constants."""

    def test_digest_time_is_2230(self):
        assert config.DIGEST_HOUR == 22
        assert config.DIGEST_MINUTE == 30

    def test_nudge_interval_reasonable(self):
        assert 10 <= config.NUDGE_INTERVAL_MINUTES <= 60

    def test_nudge_window(self):
        # Nudge should start at/after digest time
        assert config.NUDGE_START_HOUR >= config.DIGEST_HOUR

    def test_max_assistant_length(self):
        """Doudou responses truncated at 4000 chars. Boyang never truncated."""
        assert config.MAX_ASSISTANT_LENGTH == 4000

    def test_boyang_user_id(self):
        assert config.BOYANG_USER_ID == 411364623

    def test_sgt_offset(self):
        """SGT is UTC+8."""
        assert config.SGT.utcoffset(None).total_seconds() == 8 * 3600


class TestGroupNames:
    """Session display name mapping."""

    def test_claw003_mapped(self):
        assert "-5125187430" in config.GROUP_NAMES
        assert config.GROUP_NAMES["-5125187430"] == "CLAW 003"

    def test_claw008_mapped(self):
        assert "-5109089385" in config.GROUP_NAMES
        assert config.GROUP_NAMES["-5109089385"] == "CLAW 008"
