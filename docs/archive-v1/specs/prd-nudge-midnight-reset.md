# PRD: Pure State Machine Scheduler — Remove All Date Logic

> **Project:** digest-bot
> **Date:** 2026-03-06
> **Priority:** P0 Critical — nudges silently stop every night after midnight
> **Estimated effort:** Small: <1hr

---

## Context

### The bug
Nudges stop at midnight (00:00) every night because `_reset_if_new_day()` clears `_digest_generated` when the calendar date changes. The nudge window is 22:30-07:00, spanning midnight.

### The WRONG fix (reverted)
Adding smarter date comparison (check if yesterday == _today). This adds MORE date logic to fix a date logic bug. Wrong direction.

### The RIGHT fix
**Remove ALL date logic.** The scheduler should be a pure state machine with exactly 3 transitions:

```
digest_job → starts cycle (digest_generated=True, sleep_received=False)
mark_sleep → stops nudging (sleep_received=True)
nudge_job  → checks flags + time window → fire or skip
```

No `_today`. No `_reset_if_new_day()`. No `_midnight_reset()`. The digest job IS the only reset point.

### Why this works
- APScheduler CronTrigger fires `_digest_job` once at 22:30 daily
- `_digest_job` resets state (clears sleep) and generates → new cycle starts
- Nudge jobs check `_digest_generated` and `_sleep_received` flags + time window
- No date comparison ever happens
- Between 07:00 and 22:30, nudge cron doesn't fire → stale state is irrelevant
- At 22:30, `_digest_job` always resets → no stale state carries over

### Key files
- `scheduler.py` — the ONLY file to modify
- `tests/test_scheduler.py` — existing tests (some need updating: `test_resets_on_new_day` and `test_no_reset_same_day` test deleted functionality)
- `tests/test_nudge_midnight_reset.py` — **21 new tests** enforcing pure state machine architecture

---

## Requirements

- [ ] R1: Remove `_today` attribute from DigestScheduler
- [ ] R2: Remove `_reset_if_new_day()` method entirely
- [ ] R3: Remove `_midnight_reset()` method and its APScheduler job
- [ ] R4: `_digest_job()` always runs (no `if _digest_generated: return` guard), resets `_sleep_received=False`, sets `_digest_generated=True`, calls callback
- [ ] R5: `_nudge_job()` does NOT call any reset method — it only checks flags + time window
- [ ] R6: `_nudge_job()` is read-only on state (never mutates `_digest_generated` or `_sleep_received`)
- [ ] R7: All 21 tests in `test_nudge_midnight_reset.py` pass
- [ ] R8: Update `test_scheduler.py` — replace `test_resets_on_new_day` and `test_no_reset_same_day` with tests for the new digest-job-as-reset behavior
- [ ] R9: No regressions in any other test file

---

## Tasks

### Implementation
- [ ] T1: Remove `_today` attribute from `__init__`
- [ ] T2: Delete `_reset_if_new_day()` method entirely
- [ ] T3: Delete `_midnight_reset()` method entirely
- [ ] T4: Remove the "reset" APScheduler job from `start()`
- [ ] T5: Rewrite `_digest_job()`: remove guard, always reset sleep_received=False, set digest_generated=True, call callback
- [ ] T6: Remove `_reset_if_new_day()` call from `_nudge_job()`
- [ ] T7: Remove `timedelta` import if no longer needed
- [ ] T8: Update `tests/test_scheduler.py` — replace date-reset tests with digest-cycle-reset tests

### Testing Strategy

#### Unit/Integration (agent runs — 21 tests already written)
- [ ] T-UNIT-1: All 3 architecture enforcement tests pass (no _today, no _reset_if_new_day, no _midnight_reset)
- [ ] T-UNIT-2: All 3 digest-job-as-cycle-start tests pass
- [ ] T-UNIT-3: All 6 nudge pure-flag-check tests pass
- [ ] T-UNIT-4: All 6 midnight crossover tests pass
- [ ] T-UNIT-5: All 3 cycle isolation tests pass
- [ ] T-UNIT-6: All existing scheduler tests pass (after update)
- [ ] T-UNIT-7: Full test suite passes

#### E2E (parent agent runs AFTER)
- [ ] T-E2E-1: Full test suite — no regressions

---

## Acceptance Criteria

```bash
cd ~/digest-bot

# Architecture enforcement + midnight fix:
python3 -m pytest tests/test_nudge_midnight_reset.py -v
# Expected: 21 passed, 0 failed

# Updated scheduler tests:
python3 -m pytest tests/test_scheduler.py -v
# Expected: all pass (after replacing date-reset tests)

# Full suite:
python3 -m pytest tests/ --ignore=tests/test_live_e2e.py --ignore=tests/test_e2e.py -q
# Expected: 0 new failures
```

---

## Codebase Conventions

> **CRITICAL: Agent must follow these.**

- **Only modify** `scheduler.py` and `tests/test_scheduler.py`
- **DO NOT modify** `tests/test_nudge_midnight_reset.py` — those tests define the architecture
- **DO NOT modify** `config.py`, `main.py`, or any other file
- **Timezone:** `datetime.now(SGT)` always
- **Logging:** `logger.info()` / `logger.error()`
- **The scheduler must have NO date-string tracking** — no `_today`, no date comparison, no `strftime("%Y-%m-%d")`

---

## Out of Scope

- Changing nudge timing or frequency
- Changing config values
- Changing main.py or any handler logic
- Adding date-based anything

---

## Architecture Summary

### Before (WRONG — date-based)
```
_reset_if_new_day() called in digest_job AND nudge_job
  → compares _today string to current date
  → resets flags on date change
  → breaks at midnight because nudge window spans two dates
```

### After (CORRECT — pure state machine)
```
digest_job (22:30 daily):
  sleep_received = False
  digest_generated = True
  callback()

nudge_job (every 30 min, 22:00-07:00):
  if sleep_received → skip
  if not digest_generated → skip
  if not in time window → skip
  callback()

mark_sleep:
  sleep_received = True
```
