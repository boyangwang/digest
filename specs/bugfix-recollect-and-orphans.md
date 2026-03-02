# Bugfix: Text Recollection Silent Failure + Orphan Files

## Context

Two bugs in the Sleep Digest Bot:

### Bug A: handle_text re-collection silently fails
When Boyang sends a text recap to the bot during an active digest, the bot should:
1. Append the recap text (✍️)
2. Re-collect new conversations since last `coverage_to`
3. Compose LLM summaries for each session
4. **Send the summary to Boyang** (📬 +N msgs with summary text)
5. Update the digest file with new summaries + advance coverage_to
6. If 0 new messages, send "📭 0 new messages since HH:MM"
7. If collection fails, send "❌ Collection failed: reason"

**Current behavior:** Steps 2-7 silently fail. The bot sends ✍️ but nothing else. The 8 failing tests in `tests/test_text_recollection.py` define the expected behavior precisely.

**Root cause:** The async tests fail because `handle_text` uses `_check_user()` which returns `(False, False)` when the mock user doesn't match ALLOWED_USER_IDS. The tests mock `from_user.id = 411364623` but the code imports from config at check time. The handler early-returns before reaching the re-collection code.

Also: python-telegram-bot async handlers need `pytest-asyncio` marker to work. The tests use `@pytest.mark.asyncio` but `pytest-asyncio` may not be configured correctly (mode=auto needed or explicit fixture).

### Bug B: Orphan empty digest files in Obsidian vault
Bot restarts create empty 394-byte digest files with `status: active` that never get content. Today there are 12 orphan files polluting Boyang's Obsidian vault.

**Expected:** On startup, `recover_active_on_startup()` should detect and clean up stale empty active files (files with `status: active` but no actual summary content and older than 1 hour).

## Codebase

- **Main bot:** `/Users/claw/digest-bot/main.py`
- **Recorder:** `/Users/claw/digest-bot/recorder.py`
- **Collector:** `/Users/claw/digest-bot/collector.py`
- **LLM:** `/Users/claw/digest-bot/llm.py`
- **Config:** `/Users/claw/digest-bot/config.py`
- **Tests:** `/Users/claw/digest-bot/tests/`
- **Existing failing tests:** `tests/test_text_recollection.py` (8 failing, defines Bug A)
- **New tests needed:** `tests/test_orphan_cleanup.py` (Bug B)

## Test Infrastructure

- Run tests: `cd /Users/claw/digest-bot && python3 -m pytest tests/test_text_recollection.py tests/test_orphan_cleanup.py -v`
- Existing passing tests (DO NOT BREAK): `python3 -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_nudge_bug.py --ignore=tests/test_scheduler.py -v`
- pytest-asyncio mode must be configured (add `asyncio_mode = "auto"` to pytest.ini if needed)

## Tasks

- [ ] **T1.** Fix the pytest-asyncio configuration so `@pytest.mark.asyncio` tests actually run as async (not skipped/failed due to missing event loop). Check pytest.ini.
- [ ] **T2.** Fix the mock user ID issue in test fixtures — ensure `_check_user` returns `(True, False)` for the test user. The tests mock `from_user.id = 411364623` which IS Boyang's real ID. Check that `config.ALLOWED_USER_IDS` includes this ID during tests, or mock `_check_user` / `_is_allowed`.
- [ ] **T3.** Make all 8 existing tests in `test_text_recollection.py` pass by fixing the actual `handle_text` function in `main.py`. The re-collection code path must: (a) call `_build_session_summaries`, (b) call `update_digest`, (c) send summary via `_send_to_boyang`, (d) handle 0-message case, (e) handle exceptions. The tests define expected behavior — read them carefully.
- [ ] **T4.** Write `tests/test_orphan_cleanup.py` with tests for orphan file detection and cleanup on startup. Tests must verify: (a) `recover_active_on_startup` cleans up files with `status: active` + no summary content + older than 1 hour, (b) real active files with content are NOT cleaned up, (c) finalized files are NOT touched.
- [ ] **T5.** Implement orphan cleanup in `recorder.py` `recover_active_on_startup()`. After recovering the valid active file, scan for other `status: active` files that are empty (no summary content after "# Doudou's Summary") and older than 1 hour — auto-finalize them as `status: stale`.
- [ ] **T6.** Verify ALL existing tests still pass (the full suite minus known-broken e2e/scheduler tests). Run: `python3 -m pytest tests/ --ignore=tests/test_e2e.py --ignore=tests/test_nudge_bug.py --ignore=tests/test_scheduler.py -v`

## Acceptance Criteria

- All 8 tests in `test_text_recollection.py` pass
- New orphan cleanup tests in `test_orphan_cleanup.py` pass
- All existing tests still pass (no regressions)
- `handle_text` reliably sends summary message after recap
- Orphan active files are cleaned up on startup
