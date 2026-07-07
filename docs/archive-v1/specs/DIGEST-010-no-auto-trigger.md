# DIGEST-010: Remove Auto-Trigger — Make Digest Fully /digest-Based

## Context

The scheduler's `_digest_job` fires at 22:30 SGT via cron, unconditionally setting `_digest_generated = True` and calling `generate_digest()`. This bypasses the documented state machine (`IDLE → /digest → ACTIVE`) — the bot generates a digest and starts nudging even when the user never sent `/digest`.

**Bug report (2026-03-09):** Boyang didn't send `/digest` but received a full digest at 22:30 + sleep nudges every 30 min from 22:30 to 01:30. The scheduler auto-triggered everything.

## Requirements

The digest bot must be **completely `/digest` command-based**:
- No digest generation without explicit `/digest` from the user
- No nudging without explicit `/digest` from the user
- No coverage timestamp advancement without explicit `/digest`
- The 22:30 cron should send a **harmless reminder** to run `/digest`, nothing more
- Recovery on startup (active file from previous `/digest`) should still resume nudging — that's a legitimate active session

## Tasks

- [ ] **T1: Change `_digest_job` to send reminder only**
  - In `scheduler.py`, `_digest_job` must NOT set `_digest_generated = True`
  - It must NOT call `_on_digest_callback` (which generates the digest)
  - Instead, it should call a new `_on_reminder_callback` that sends a gentle message
  - Reset `_sleep_received = False` still makes sense here (new day, new cycle)

- [ ] **T2: Add reminder callback to scheduler**
  - Add `_on_reminder_callback` field to `DigestScheduler`
  - Update `set_callbacks()` to accept `on_reminder` parameter
  - The reminder callback is a simple async function that sends a Telegram message

- [ ] **T3: Wire up reminder in main.py**
  - Create `do_reminder()` async function in `main.py` that sends a gentle bilingual message like:
    ```
    📝 Ready for tonight's digest? Send /digest when you're ready.
    📝 今晚的摘要准备好了吗？准备好了就发 /digest 吧。
    ```
  - This is a static message — NO LLM call needed. Keep it cheap and deterministic.
  - Update `_scheduler.set_callbacks(on_digest=..., on_nudge=..., on_reminder=...)` in `post_init`

- [ ] **T4: Ensure `/digest` command properly gates nudging**
  - Verify that `cmd_digest` → `generate_digest()` → `_scheduler.mark_digest_generated()` is the ONLY path that enables nudging (besides recovery)
  - The `_nudge_job` already checks `if not self._digest_generated: return` — this is correct and should keep working

- [ ] **T5: Keep startup recovery behavior**
  - `recover_active_on_startup()` → `_scheduler.mark_digest_generated()` must remain
  - If the bot restarts with an active file from a previous `/digest`, nudging should resume
  - This is NOT auto-triggering — it's resuming an explicitly started session

- [ ] **T6: Update `trigger_digest_now()` test helper**
  - `trigger_digest_now()` currently calls `_digest_job()` which will now only send a reminder
  - Add a separate `trigger_generate_now()` method or update the test helper to call the digest callback directly
  - Tests that need to simulate `/digest` should call the generate callback, not the reminder job

- [ ] **T7: Write tests**
  - Test: 22:30 cron job sends reminder, does NOT generate digest, does NOT set `_digest_generated`
  - Test: Nudge job skips when no `/digest` has been sent (22:30 reminder alone doesn't enable nudging)
  - Test: After `/digest` command, `_digest_generated` is True and nudging works
  - Test: Startup recovery with active file enables nudging
  - Test: `_sleep_received` resets at 22:30 reminder (new day cycle)

- [ ] **T8: Update docstrings and module docstring**
  - `scheduler.py` module docstring: update to reflect reminder-based 22:30 behavior
  - `_digest_job` docstring: "Sends reminder to run /digest"
  - State machine comment in `main.py` line 11: verify it's accurate

## Acceptance Criteria

- [ ] Running the bot without sending `/digest` at any point → NO digest generated, NO nudges sent (only 22:30 reminder)
- [ ] Sending `/digest` → digest generated, nudging starts as before
- [ ] Bot restart with active file → nudging resumes
- [ ] All existing tests pass (`pytest tests/ -x`)
- [ ] New tests for the above scenarios pass

## Out of Scope

- Changing the nudge window timing
- Changing the `/sleep` command behavior
- Changing the digest content/format
- Any LLM-generated reminder text (keep it static)
