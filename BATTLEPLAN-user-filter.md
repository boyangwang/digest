# Battle Plan: User Allowlist + Test Mode + Integration Tests

> Created: 2026-03-02
> Status: IN PROGRESS

## Execution Order

### Step 1: Discover test account user ID ✅
- [x] 1a. Peekaboo: Telegram → search @sleep_digest_bot → START → send message
- [x] 1b. Log shows: user_id=6805433372, @claw0606, name="mala"
- [x] 1c. Updated config.py with TEST_USER_ID=6805433372

### Step 2: Implement user filtering + test mode ✅
- [x] 2a. config.py: TEST_USER_ID, ALLOWED_USER_IDS, TEST_DIGEST_DIR
- [x] 2b. main.py: _check_user(), _is_allowed(), _is_test_user(), TestRecorder class
- [x] 2c. tests/test_user_filter.py — 19 tests
- [x] 2d. All 248 unit tests pass
- [x] 2e. Bot restarts clean via `launchctl kickstart -k`

### Step 3: Verify test mode works via Telegram UI ✅
- [x] 3a-3f: Full cycle verified: /digest → text → /status → /sleep
- [x] Test files in _test/, production untouched

### Step 4: Build UI automation helper ✅
- [x] 4a. tests/telegram_ui.py with Peekaboo functions
- [x] 4b. Tested manually — works

### Step 5: Integration tests ✅
- [x] 5a. tests/test_live_e2e.py — 8 live tests
- [x] 5b. All 8 pass (94s, UI automation is slow but reliable)
- [x] Fixed YAML quoting bug in TestRecorder.finalize()

### Step 6: Commit + push ✅
- [x] 256 tests all passing
- [x] Commit ce74466 pushed to github-digest

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
