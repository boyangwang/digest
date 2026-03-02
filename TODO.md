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

## Priority 2: Revoke compromised bot token

**Issue:** Bot token `8324650609:AAGeTNX2...` is in git history (commits
`662c5ee`, `52fa822`). Public repo = anyone can find it.

**Status:** Needs Boyang to `/revoke` in BotFather (user account required).

**Fix:**
1. Boyang: @BotFather → `/revoke` → select @sleep_digest_bot → new token
2. Update: `.env`, LaunchAgent plist, `.secrets.env`
3. Restart bot
4. Scrub git history with BFG Repo-Cleaner

---

## Priority 3: Voice message feature (SPEC-VOICE)

**Status:** Implementation complete, 220 tests passing, awaiting live test.
Need Boyang to send a voice message to @sleep_digest_bot to verify Telegram
download path.

---

## Priority 4: Nightly Check-in cron disabled

**Done:** `22de298f` disabled. But verify no other orphan crons exist.

---

## Priority 5: LLM summary generation (compose_summary via Doudou)

**Status:** `openclaw agent --local` mechanism identified and verified.
The `llm.py` rewrite was in progress before the voice feature pivot.
May be related to Priority 1 (if compose_summary is the failure point).
