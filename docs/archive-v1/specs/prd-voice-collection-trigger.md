# PRD: Voice Messages Must Trigger Collection

> **Project:** digest-bot
> **Date:** 2026-03-05
> **Priority:** P1 High
> **Estimated effort:** Small: <1hr

---

## Context

When Boyang sends a **text message** to the digest bot, two things happen:
1. The text is appended to the digest recap section
2. `_engine.collect()` is called to fetch new conversations from OpenClaw sessions since `coverage_to`, updating Doudou's Summary

When Boyang sends a **voice message**, only the first part happens:
1. Audio is saved, transcribed via STT, and appended to the digest recap ✅
2. **No collection is triggered** ❌

This means voice messages don't update the digest with new OpenClaw conversations. There's no reason for this asymmetry — voice should behave identically to text for collection triggering.

**Evidence from production (2026-03-05):**
```
17:39 — Voice message received. Log: "Saved voice" + "Transcribed: 142 chars". No collection.
17:55 — Text message received. Log: "Recorded: 83 chars" + "Collection Gen 3 started".
```

**Key files:**
- `main.py` — `handle_text()` (lines ~912-970) has collection logic; `handle_voice()` (lines ~976-1025) does NOT
- `tests/test_voice_collection_trigger.py` — **5 failing tests already written** that prove the bug

---

## Requirements

- [ ] R1: `handle_voice` triggers `_engine.collect()` after saving/transcribing, using the same logic as `handle_text`
- [ ] R2: When collection finds new messages, the digest is updated and Boyang is notified (same as text path)
- [ ] R3: When collection finds 0 messages, Boyang is notified with "📭 0 new messages since HH:MM" (same as text path)
- [ ] R4: All 5 existing failing tests in `test_voice_collection_trigger.py` pass
- [ ] R5: All existing tests continue to pass (zero regressions)

---

## Tasks

### Implementation
- [ ] T1: Extract the collection logic from `handle_text` (lines ~939-970) into a shared helper function (e.g., `_collect_and_report()`)
- [ ] T2: Call `_collect_and_report()` from `handle_voice` after `append_voice_recap` and the reply
- [ ] T3: Call `_collect_and_report()` from `handle_text` (replacing the inline logic) to ensure symmetry

### Testing Strategy

#### Unit/Integration (5 tests already exist — agent must make them PASS)
- [ ] T-UNIT-1: `test_voice_triggers_collection` — verify `_engine.collect()` is called after voice
- [ ] T-UNIT-2: `test_voice_collection_uses_coverage_to` — verify correct `since` timestamp
- [ ] T-UNIT-3: `test_voice_collection_updates_digest_on_new_messages` — verify `update_digest()` called
- [ ] T-UNIT-4: `test_voice_collection_silent_on_zero_messages` — verify 0-message notification
- [ ] T-UNIT-5: `test_both_handlers_call_collect` — verify text/voice symmetry

#### E2E (parent agent runs AFTER agent completion)
- [ ] T-E2E-1: Run full E2E suite: `python3 tests/run_e2e.py --test all`

---

## Acceptance Criteria

```bash
cd ~/digest-bot

# The 5 failing tests must now pass:
python3 -m pytest tests/test_voice_collection_trigger.py -v
# Expected: 6 passed, 0 failed

# Full test suite must still pass:
python3 -m pytest tests/ --ignore=tests/test_live_e2e.py --ignore=tests/test_e2e.py -q
# Expected: 0 failures (excluding pre-existing failures in test_text_recollection.py)
```

- [ ] All 6 tests in `test_voice_collection_trigger.py` pass
- [ ] No regressions in existing test suite
- [ ] Changes committed with descriptive message

---

## Codebase Conventions

> **CRITICAL: Agent must follow these.**

- **Timezone:** Always use `datetime.now(SGT)`, never naive `datetime.now()`
- **Error handling:** Never `except Exception: pass` — always log errors
- **Imports:** `from recorder import ...` at top of file; `from config import SGT` for timezone
- **Test framework:** pytest + pytest-asyncio for async tests
- **Logging:** Use `logger.info()` / `logger.error()` — the bot uses Python logging module
- **Collection trigger:** Must use `trigger="voice"` (not "text") in the `_engine.collect()` call so logs distinguish the trigger source

---

## Out of Scope

- Changing STT provider or transcription logic
- Modifying the voice save/transcribe flow (that works correctly)
- Adding collection to `handle_photo` or `handle_document` (separate task if needed)
- Changing the E2E test runner

---

## Notes

- The refactoring into a shared helper (`_collect_and_report`) is preferred over copy-pasting the collection block from `handle_text` into `handle_voice`. DRY matters here.
- The 5 failing test cases are in `tests/test_voice_collection_trigger.py` — read them first.
- Pre-existing failures in `tests/test_text_recollection.py` (5 tests) are NOT related — ignore them.
