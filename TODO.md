# TODO — Sleep Digest Bot

## Priority 1: Text reply re-collection is broken (silent failure)

**Bug:** When Boyang sends a text message, the bot appends the recap (✍️) but
the re-collection of new OpenClaw conversations silently fails. No error logged,
no status message sent, coverage_to never advances. All messages pile up at the
next scheduled 22:30 collection.

**Evidence from 2026-03-01 logs:**
- 20:16:00 — `Recorded: 743 chars.` then NOTHING (next log at 20:16:10)
- 21:32:55 — `Recorded: 485 chars.` then NOTHING
- 22:30:00 — Scheduler collects 48 messages (entire 3hr backlog)

**Required behavior after text reply:**
1. ✍️ emoji (acknowledgment) — already works
2. SECOND message: collection status (from→to timestamps, N messages, summary)
3. Update digest file (append summaries, advance coverage_to)
4. If collection fails → tell Boyang explicitly, never silent

**Root cause:** Unknown — needs investigation. Hypotheses:
- A: `collect_all_messages()` returns 0 (no messages in timeframe)
- B: Synchronous `subprocess.run()` in `compose_summary()` blocks event loop
- C: Exception swallowed by `except Exception: pass` in `extract_messages()`
- D: `_build_session_summaries()` throws, caught by outer try/except but warning not logged

**Status:** Tests to be written (failing), then fix.

---

## Priority 2: User allowlist + test mode + integration tests

**Goal:** Bot only responds to Boyang (411364623) and Doudou's test account
(Mac Mini Telegram client, account "Claw", user ID TBD). All other users
silently ignored.

### 2a: User filtering
- Add `ALLOWED_USER_IDS` set in `config.py` (Boyang + test account)
- Add early-return filter in every handler: if `update.effective_user.id` not
  in allowlist → ignore (log + return, no reply)
- Test user messages get `is_test = True` flag

### 2b: Test mode handling
- When message comes from test account:
  - Use a **separate test directory** (`DIGEST_DIR / "_test"`) for all file I/O
  - Skip LLM calls (`compose_summary` → return placeholder "TEST SUMMARY")
  - Skip `compose_nudge` → return placeholder "TEST NUDGE"
  - All slash commands work identically but against the test directory
  - Responses prefixed with 🧪 so it's visually obvious
- Production state (Boyang's active file, scheduler, nudges) untouched
- Test state is fully independent (own coverage chain, own files)

### 2c: Discover test account user ID
- Send a message from Mac Mini Telegram client to @sleep_digest_bot
- Read `/tmp/digest-bot.log` for the `user_id`
- Update `config.py` with the real ID

### 2d: UI automation for Telegram Desktop
- Build `tests/telegram_ui.py` helper using Peekaboo:
  1. Focus Telegram app
  2. Search for `@sleep_digest_bot` (use Search field, elem_3)
  3. Click on the bot in search results
  4. Type message in chat input field
  5. Press Enter to send
  6. Read bot's reply (Select All + Copy + pbpaste, or accessibility tree)
- Key challenge: Telegram Desktop has non-standard accessibility
  (limited element labels, custom rendering)
- Fallback: use AppleScript `keystroke` for typing + `key code 36` for Enter

### 2e: Integration tests (`tests/test_e2e.py`)
Tests run against the LIVE bot using UI automation:
1. **Send /start** → verify bot replies with menu text
2. **Send /status** → verify IDLE state response
3. **Send /digest** → verify bot creates test file, sends summary message
4. **Send text message** → verify ✍️ + recap appended to test file
5. **Send /status** → verify ACTIVE state, coverage timestamps
6. **Send /sleep** → verify finalization, test file has `status: final`
7. **Full cycle** → /digest → text → /digest (update) → /sleep
8. **Verify test files** → read files from test directory, assert YAML +
   content structure matches SPEC.md

Verification methods:
- **Bot reply**: Read from Telegram UI (accessibility tree or clipboard)
- **File content**: Direct file read from `DIGEST_DIR / "_test"`
- **Log entries**: Parse `/tmp/digest-bot.log` for expected patterns

### 2f: Unit tests for filtering
- `test_user_filter.py`: Verify non-allowlisted user messages are rejected
- Verify test user gets 🧪 prefix
- Verify Boyang's messages go through normal production path

---

## Priority 3: Revoke compromised bot token

**Issue:** Bot token `8324650609:AAGeTNX2...` is in git history (commits
`662c5ee`, `52fa822`). Public repo = anyone can find it.

**Status:** Needs Boyang to `/revoke` in BotFather (user account required).

**Fix:**
1. Boyang: @BotFather → `/revoke` → select @sleep_digest_bot → new token
2. Update: `.env`, LaunchAgent plist, `.secrets.env`
3. Restart bot
4. Scrub git history with BFG Repo-Cleaner

---

## Priority 4: Voice message feature (SPEC-VOICE)

**Status:** Implementation complete, 220 tests passing, awaiting live test.
Need Boyang to send a voice message to @sleep_digest_bot to verify Telegram
download path.

---

## Priority 5: Nightly Check-in cron disabled

**Done:** `22de298f` disabled. But verify no other orphan crons exist.

---

## Priority 6: LLM summary generation (compose_summary via Doudou)

**Status:** `openclaw agent --local` mechanism identified and verified.
The `llm.py` rewrite was in progress before the voice feature pivot.
May be related to Priority 1 (if compose_summary is the failure point).
