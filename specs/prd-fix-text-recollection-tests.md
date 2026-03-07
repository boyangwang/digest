# PRD: Fix Broken test_text_recollection.py Tests

> **Project:** digest-bot
> **Date:** 2026-03-05
> **Priority:** P0 Critical — broken tests left undetected for 2 days
> **Estimated effort:** Small: <1hr

---

## Context

**P0 INCIDENT: 5 tests in `tests/test_text_recollection.py` have been broken since commit `3a19a87` (Mar 3, 21:26 SGT).**

### What happened

Commit `3a19a87` ("DIGEST-009: Parallel, retriable, supersedable collection engine") refactored `main.py`:
- **Removed** `_build_session_summaries()` function from `main.py`
- **Replaced** with `CollectionEngine.collect()` in new `collection_engine.py`
- **Did NOT update** `tests/test_text_recollection.py` which directly imports/patches `_build_session_summaries`

### Proof

All 9 tests passed at commit `3a19a87^` (before refactor). After the commit, 5 fail:

| Test | Failure | Root Cause |
|------|---------|------------|
| `test_build_session_summaries_returns_nonzero` | `ImportError: cannot import name '_build_session_summaries'` | Function deleted |
| `test_status_message_includes_summary` | `AssertionError: summary text not in output` | Tests mock `compose_summary` but actual code now uses `_engine.collect()` |
| `test_collection_failure_sends_error_message` | `AttributeError: module 'main' does not have '_build_session_summaries'` | Patches deleted function |
| `test_zero_messages_reports_no_new` | `AttributeError: module 'main' does not have '_build_session_summaries'` | Patches deleted function |
| `test_handle_text_with_real_collector` | `AssertionError: Summary not appended` | Mocks wrong layer — uses `compose_summary` but code now uses engine |

### Key files
- `main.py` — `handle_text` now calls `_collect_and_report()` which calls `_engine.collect()`
- `collection_engine.py` — `CollectionEngine.collect()` is the new collection API
- `tests/test_text_recollection.py` — 586 lines, 9 tests, 5 broken
- `tests/test_voice_collection_trigger.py` — working example of how to mock the new `_engine` API

---

## Requirements

- [ ] R1: All 9 tests in `test_text_recollection.py` pass
- [ ] R2: Tests mock the CURRENT API (`_engine.collect()` and `_collect_and_report`), not deleted functions
- [ ] R3: Tests preserve their ORIGINAL INTENT — they test the same behaviors as before (collection runs, status sent, coverage advances, failures reported, integration works)
- [ ] R4: No regressions in any other test file
- [ ] R5: No changes to production code (`main.py`, `collection_engine.py`, etc.) — only test file changes

---

## Tasks

### Implementation
- [ ] T1: In `test_build_session_summaries_returns_nonzero` — replace `from main import _build_session_summaries` with a test that calls `_engine.collect()` and verifies it returns results. Use the mock patterns from `test_voice_collection_trigger.py` as reference.
- [ ] T2: In `test_status_message_includes_summary` — mock `_engine.collect()` to return a result with summaries, then verify the summary text appears in the Telegram messages sent to Boyang.
- [ ] T3: In `test_collection_failure_sends_error_message` — replace `patch("main._build_session_summaries", ...)` with `patch.object(_engine, "collect", side_effect=Exception("DB error"))` and verify error is reported.
- [ ] T4: In `test_zero_messages_reports_no_new` — replace `patch("main._build_session_summaries", return_value=([], 0))` with mocking `_engine.collect()` to return a result with `total=0` and verify 0-message notification is sent.
- [ ] T5: In `test_handle_text_with_real_collector` — mock `_engine.collect()` to return a proper result with summaries including "Evening walk summary", then verify recap appended + coverage advanced + summary in file + status message sent.

### Testing Strategy

#### Unit/Integration (agent runs)
- [ ] T-UNIT-1: All 9 tests in test_text_recollection.py pass
- [ ] T-UNIT-2: Full test suite passes (no regressions)

#### E2E (parent agent runs AFTER agent completion)
- [ ] T-E2E-1: Run `python3 -m pytest tests/test_text_recollection.py -v` — 9/9 pass
- [ ] T-E2E-2: Run full suite — no regressions

---

## Acceptance Criteria

```bash
cd ~/digest-bot

# All 9 recollection tests pass:
python3 -m pytest tests/test_text_recollection.py -v
# Expected: 9 passed, 0 failed

# Full suite (no regressions):
python3 -m pytest tests/ --ignore=tests/test_live_e2e.py --ignore=tests/test_e2e.py -q
# Expected: 0 new failures
```

- [ ] All 9 tests in test_text_recollection.py pass
- [ ] No regressions in existing test suite
- [ ] Only test file modified (no production code changes)
- [ ] Changes committed with descriptive message

---

## Codebase Conventions

> **CRITICAL: Agent must follow these.**

- **Mock the right layer:** The collection API is now `_engine.collect()` (a `CollectionEngine` instance). See `test_voice_collection_trigger.py` for correct mock patterns.
- **CollectionEngine.collect() returns:** An object with `.total` (int), `.coverage_to` (datetime), `.summaries` (list of dicts with "session", "messages", "summary" keys). Returns `None` on failure.
- **`_collect_and_report(trigger)`** is the shared helper in `main.py` that calls `_engine.collect()` and sends status to Boyang.
- **`_engine`** is a module-level variable in `main.py`: `_engine = CollectionEngine(...)`. Mock it with `patch.object(main_mod._engine, "collect", ...)`.
- **`_send_to_boyang(text)`** sends via `_app.bot.send_message(chat_id=BOYANG_ID, text=text)`.
- **Timezone:** Always `datetime.now(SGT)`, never naive.
- **Test framework:** pytest + pytest-asyncio.
- **DO NOT change test_voice_collection_trigger.py or any production code files.**

---

## Reference: How tests/test_voice_collection_trigger.py mocks the engine

```python
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from config import SGT

# Create mock result
mock_result = MagicMock()
mock_result.total = 5
mock_result.coverage_to = datetime(2026, 3, 5, 18, 30, tzinfo=SGT)
mock_result.summaries = [{"session": "CLAW 003", "messages": 5, "summary": "Test summary"}]

# Patch engine.collect
with patch.object(main_mod._engine, "collect", new_callable=AsyncMock, return_value=mock_result):
    await main_mod.handle_text(mock_update, mock_context)
```

---

## Out of Scope

- Changes to production code
- Changes to other test files
- Adding new tests (just fix the broken 5)

---

## Notes

- The 4 PASSING tests in this file don't reference `_build_session_summaries` — they mock at higher levels and still work. The 5 failing ones directly import or patch the deleted function.
- `test_voice_collection_trigger.py` is the gold standard for the current mock pattern.
