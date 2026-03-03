# DIGEST-010: Derived Session Architecture — Proper Testing

> **Status:** 🟡 Active
> **Priority:** P1
> **Tasks:** 0/10 complete
> **Created:** 2026-03-03
> **Depends on:** DIGEST-009 (collection engine — complete)

---

## Problem Statement

The collection engine uses derived session IDs for parallel LLM calls, but this critical behavior has **zero test coverage**. The production lock contention bug (2026-03-03 21:55) slipped through because:

1. All 14 collection engine tests mock `_summarize_session` / `_run_with_retry` — never testing actual session ID derivation
2. No unit tests verify the sanitization logic (special chars, length limits, edge cases)
3. No integration tests verify that parallel calls actually use different session IDs
4. No E2E test verifies lock-free parallel execution in production

### Background

**Architecture decision:** Each parallel summary call derives its session ID from the source session name:
- `"Direct with Boyang"` → `digest-summary-direct-with-boyang`
- `"CLAW 003"` → `digest-summary-claw-003`
- `"agent:main:subagent:5c16b1cc-..."` → `digest-summary-agent-main-subagent-5c16b1cc` (truncated to 40 chars)

**Why derived (not actual sessions):**
- Gateway OWNS its session files — external writes violate caching/locking
- Lock contention: gateway holds locks during live chat
- Data pollution: summary prompt/response would pollute real conversation history

**Why source-name-based (not UUID):**
- Natural partition key — bounded file count (~5), not ∞
- Context reuse — agent accumulates summary style per session
- Debuggable — filename reveals which session it belongs to

---

## Tasks

### Phase 1: Unit Tests for Session ID Derivation

- [ ] **T1** — `tests/test_derived_sessions.py`: Sanitization tests
  - `test_simple_name_sanitized` — `"CLAW 003"` → `"digest-summary-claw-003"`
  - `test_spaces_to_hyphens` — `"Direct with Boyang"` → `"digest-summary-direct-with-boyang"`
  - `test_special_chars_stripped` — `"agent:main:subagent:5c16b1cc-6bdf"` → `"digest-summary-agent-main-subagent-5c16b1cc-6bdf"`
  - `test_max_length_40` — very long session name truncated to 40 chars in the safe_name portion
  - `test_empty_name_handled` — empty string doesn't crash
  - `test_unicode_name` — non-ASCII characters handled gracefully

- [ ] **T2** — `tests/test_derived_sessions.py`: Session ID uniqueness tests
  - `test_different_sessions_get_different_ids` — 3 different source names → 3 different session IDs
  - `test_same_session_gets_same_id` — same source name called twice → same session ID (deterministic)
  - `test_no_collision_similar_names` — `"CLAW 003"` vs `"CLAW-003"` — verify behavior is well-defined

- [ ] **T3** — `tests/test_derived_sessions.py`: Integration — session ID passed to subprocess
  - `test_session_id_passed_to_async_compose` — mock `asyncio.create_subprocess_exec`, verify `--session-id` arg matches derived name
  - `test_parallel_calls_use_different_session_ids` — 3 parallel `_summarize_session` calls → verify 3 different `--session-id` values passed

### Phase 2: Extract & Refactor

- [ ] **T4** — Extract `derive_session_id(source_name: str) -> str` as a standalone function
  - Currently inline in `_summarize_session` — not testable in isolation
  - Move to `collection_engine.py` as module-level function
  - Import `re` at module level (not inside method)

- [ ] **T5** — Verify `async_compose_summary(text, session_id)` contract
  - Default `session_id="digest-bot"` for backward compatibility (sync callers)
  - Session ID correctly interpolated into subprocess args

### Phase 3: Parallel Lock-Freedom Integration Test

- [ ] **T6** — `tests/test_derived_sessions.py`: Verify parallel calls don't share lock files
  - Spy on `asyncio.create_subprocess_exec` calls during a `collect()` with 3 sessions
  - Extract all `--session-id` arguments
  - Assert: all 3 are different AND none is `"digest-bot"`

- [ ] **T7** — Verify all new tests FAIL (no implementation of T4 yet) + existing 122 pass

### Phase 4: Implementation

- [ ] **T8** — Implement T4 (extract function) + make all tests pass

### Phase 5: E2E

- [ ] **T9** — E2E test: parallel collection creates separate session files
  - Send text to bot in test mode
  - Check log output for session IDs used
  - Verify no lock contention errors in log

- [ ] **T10** — Run full E2E suite (all 24+ existing tests pass + new test)

---

## Acceptance Criteria

1. ✅ `derive_session_id()` is a standalone, tested function
2. ✅ All sanitization edge cases covered (special chars, length, unicode, empty)
3. ✅ Different source sessions always produce different session IDs
4. ✅ Same source session always produces the same session ID (deterministic)
5. ✅ Parallel `collect()` calls verified to use distinct session IDs
6. ✅ No `"digest-bot"` session ID used by parallel collection (only by sync callers)
7. ✅ All new tests pass + existing 122 pass (no regressions)

---

## Files to Create/Modify

| File | Changes |
|------|---------|
| `tests/test_derived_sessions.py` | **NEW**: All session ID tests |
| `collection_engine.py` | Extract `derive_session_id()` function |
| `tests/run_e2e.py` | Add E2E test for parallel session files |

---

*PRD v1.0 — 2026-03-03*
