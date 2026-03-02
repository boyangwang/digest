# Battle Plan: User Allowlist + Test Mode + Integration Tests

> Created: 2026-03-02
> Status: IN PROGRESS

## Execution Order

### Step 1: Discover test account user ID ⬜
- [ ] 1a. Use Peekaboo to navigate Telegram Desktop to @sleep_digest_bot
- [ ] 1b. Send a test message (e.g., "test_id_discovery")
- [ ] 1c. Read `/tmp/digest-bot.log` to capture user_id from the Update
- [ ] 1d. Record the user_id

### Step 2: Implement user filtering + test mode ⬜
- [ ] 2a. Update `config.py`: TEST_USER_ID, ALLOWED_USER_IDS, TEST_DIGEST_DIR
- [ ] 2b. Update `main.py`:
  - Add `_is_allowed()` and `_is_test_user()` helpers
  - Add filter to ALL handlers (commands + text + voice + photo)
  - For test users: use TEST_DIGEST_DIR, skip LLM, prefix 🧪
  - Key: test mode needs its OWN recorder state (separate from production)
- [ ] 2c. Write `tests/test_user_filter.py` (unit tests for filtering logic)
- [ ] 2d. Run unit tests → all pass
- [ ] 2e. Restart bot, verify it starts clean

### Step 3: Verify test mode works via Telegram UI ⬜
- [ ] 3a. Send `/start` from Mac client → verify 🧪 prefix in reply
- [ ] 3b. Send `/status` → verify test state (IDLE)
- [ ] 3c. Send `/digest` → verify test file created in `_test/` dir
- [ ] 3d. Send text message → verify ✍️ + test file updated
- [ ] 3e. Send `/sleep` → verify test file finalized
- [ ] 3f. Check that NO production files were touched

### Step 4: Build UI automation helper ⬜
- [ ] 4a. Create `tests/telegram_ui.py` with Peekaboo-based functions:
  - `send_message(text)` — type + Enter in active chat
  - `read_last_reply()` — get bot's reply text
  - `navigate_to_bot()` — search + click @sleep_digest_bot
- [ ] 4b. Test the helper manually

### Step 5: Integration tests ⬜
- [ ] 5a. Create `tests/test_e2e.py`:
  - test_start_command
  - test_status_idle
  - test_digest_creates_file
  - test_text_appends_recap
  - test_sleep_finalizes
  - test_full_lifecycle
- [ ] 5b. Each test: send via UI → wait → verify reply + file content
- [ ] 5c. Run until all pass

### Step 6: Commit + push ⬜
- [ ] 6a. Run full test suite (220+ existing + new)
- [ ] 6b. Commit all changes
- [ ] 6c. Push to github-digest

## Design Decisions

### Test mode isolation
- Separate directory: `DIGEST_DIR / "_test"` 
- Separate recorder state: test handlers call recorder with test dir
- No LLM calls: `compose_summary` → "TEST SUMMARY", `compose_nudge` → "TEST NUDGE"
- Scheduler NOT affected by test commands (no mark_digest_generated, etc.)
- 🧪 prefix on all test responses

### User filtering
- Silent rejection for unknown users (no reply = no info leak)
- Log the rejection: `logger.info("Ignored message from user %d" % user_id)`
- Filter applied at handler level, not middleware (simpler, more explicit)

### UI automation approach
- Peekaboo `see` → identify elements → `click`/`type`/`press`
- For reading replies: Peekaboo `see --analyze` or clipboard (triple-click + Cmd+C)
- Fallback: AppleScript keystroke if Peekaboo fails on specific elements

## Risks & Mitigations
- **Telegram Desktop accessibility is limited**: Mitigate with `see --analyze` (vision)
- **Bot restart needed after code changes**: Will cause brief downtime (~2s)
- **Test files accumulating**: Clean up `_test/` dir in test teardown
- **Race condition**: If Boyang sends message during bot restart → message queued by Telegram, processed on restart
