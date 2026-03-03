# Standard Operating Procedure — Bug & Feature Lifecycle

> **This is mandatory process. Every agent working on this codebase MUST follow these steps in order.**
> **Skipping steps = broken product. No exceptions.**

---

## Step 0: Intake

**Trigger:** Bug reported or feature requested by Boyang.

**Actions:**
1. Create `specs/prd-<descriptive-name>.md` using template below
2. Set status badge: `🔴 Draft`
3. Add entry to `TODO.md` Active Work table
4. Commit: `spec: PRD for <name>`
5. **STOP. Do not write code. Do not write tests. Wait for PRD approval or move to Step 1 only if Boyang said to proceed.**

---

## Step 1: TDD — Write Failing Tests

**Trigger:** PRD approved (Boyang says proceed, or PRD was written collaboratively).

**Actions:**
1. Update PRD status badge: `🟡 Active — writing tests`
2. Update `TODO.md` status column and "Next Step"
3. For each task T1..TN in the PRD:
   - Write a **failing** test that validates the acceptance criteria
   - Test goes in `tests/test_<module>.py` (unit/integration) or `tests/run_e2e.py` (E2E)
4. Run all new tests — confirm they **FAIL** (red)
5. Run all existing tests — confirm they still **PASS** (no regressions)
6. Commit: `test: failing tests for <PRD name> (T1-TN)`
7. Update PRD: note which tests cover which tasks

---

## Step 2: Implement

**Trigger:** Failing tests exist for all tasks.

**Actions:**
1. Update PRD status badge: `🟡 Active — implementing`
2. Implement tasks one by one (T1, T2, T3...)
3. After each task:
   - Run its test — confirm it **PASSES**
   - Check the `- [ ]` box in the PRD: `- [x] **T1** — ...`
   - Commit: `feat: T1 — <description>`
4. After all tasks implemented:
   - Run **ALL** unit/integration tests: every file in `tests/test_*.py` individually
   - Fix any regressions before proceeding
5. Commit: `feat: all tasks complete for <PRD name>`

---

## Step 3: E2E Verification

**Trigger:** All tasks checked, all unit/integration tests pass.

**Actions:**
1. Update PRD status badge: `🔵 Testing — E2E verification`
2. Update `TODO.md`
3. Run full E2E suite: `python3 tests/run_e2e.py --test all`
4. If any E2E test fails:
   - Debug and fix
   - Re-run ALL tests (unit + integration + E2E)
   - Do NOT proceed until green
5. Record results in PRD:
   ```
   ## Verification Results
   - Unit/integration: XXX passed, 0 failed
   - E2E: X/X passed
   - Date: YYYY-MM-DD HH:MM SGT
   ```
6. Commit: `test: E2E verification complete for <PRD name>`

---

## Step 4: Deploy & Close

**Trigger:** All tests green (unit + integration + E2E).

**Actions:**
1. Restart bot: `launchctl kickstart -k gui/$(id -u)/com.digest-bot`
2. Verify bot is running: `pgrep -f "digest-bot/main.py"`
3. **Send a real message to the bot** (not just tests) to verify production behavior
4. Update PRD status badge: `🟢 Done — Completed YYYY-MM-DD`
5. Update PRD: add `Tasks: 12/12 complete`
6. Move entry in `TODO.md` from "Active Work" to "Completed" table
7. Commit: `docs: close <PRD name> — all verified`
8. Notify Boyang with summary of what was fixed

---

## Step 5: Post-Deploy Monitoring

**Trigger:** Deployed to production.

**Actions:**
1. Check `/tmp/digest-bot.log` for errors in the next hour
2. If the fix involves nightly features (reflection, /sleep):
   - Wait for the next natural trigger OR
   - Ask Boyang to test with `/reflect` or relevant command
3. If errors found → open new PRD (go to Step 0)

---

## PRD Template

```markdown
# PRD: <Title>

> **Status:** 🔴 Draft — <current state description>
> **Project:** Sleep Digest Bot — <area>
> **Date:** YYYY-MM-DD
> **Priority:** P0 Critical | P1 High | P2 Medium | P3 Low
> **Estimated effort:** Small (1-2hr) | Medium (2-4hr) | Large (4-8hr)
> **Origin:** <who reported, context>
> **Tasks:** 0/N complete

---

## Problem Statement

<What's broken or missing. Be specific. Include evidence.>

---

## Root Cause Analysis

<Why it's broken. Reference actual code files and line numbers.>

---

## Tasks

- [ ] **T1** — <description>
  - <details, acceptance criteria>

- [ ] **T2** — <description>

...

---

## Acceptance Criteria

1. <specific, testable condition>
2. <specific, testable condition>

---

## Files to Modify

| File | Changes |
|------|---------|

---

## Verification Results

_Filled in at Step 3_

- Unit/integration: ___ passed, ___ failed
- E2E: ___/___ passed
- Date: ___
```

---

## Rules

1. **No code without a PRD.** Even "quick fixes" get a PRD (can be 1 task).
2. **No implementation without failing tests.** TDD is not optional.
3. **No "done" without E2E green.** If you can't prove it works, it doesn't work.
4. **Every status change gets a commit.** The git history IS the audit trail.
5. **INDEX.md is always current.** If it's stale, fix it before doing anything else.
6. **One PRD at a time per agent.** Finish or explicitly park before starting another.

---

*Version 1.0 — 2026-03-03*
