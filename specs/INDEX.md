# Specs Index — Single Source of Truth for All Work

> **New developer / AI agent: START HERE.**
> This file tells you exactly what's in progress, what's done, and what to work on next.

---

## How This Works

1. Every bug or feature gets a PRD in `specs/prd-<name>.md`
2. Each PRD has tasks (`- [ ]` / `- [x]`) and a status badge at the top
3. This INDEX tracks all PRDs in one place with current status
4. **Status flow:** `Draft → Active → Testing → Done` (or `Stale` if abandoned)

### Status Definitions

| Status | Meaning | What to do |
|--------|---------|------------|
| 🔴 **Draft** | PRD written, not yet approved or started | Review PRD, wait for approval |
| 🟡 **Active** | Approved, implementation in progress | Check task checkboxes for progress, continue from last unchecked task |
| 🔵 **Testing** | Code done, E2E verification in progress | Run tests, fix failures, don't add features |
| 🟢 **Done** | All tasks checked, all tests pass, deployed | Nothing to do — reference only |
| ⚫ **Stale** | Abandoned or superseded | Ignore unless explicitly revived |

### How to Know What to Work On

1. Read this INDEX — find the 🔴/🟡/🔵 entries
2. Open the PRD file — find first unchecked `- [ ]` task
3. If no tests exist yet → write tests first (TDD)
4. If tests exist but fail → implement the fix
5. If tests pass → run full E2E (`python3 tests/run_e2e.py`) → mark Done
6. **Never skip E2E verification**

---

## Active Work

| # | PRD | Status | Summary | Tasks | Next Step |
|---|-----|--------|---------|-------|-----------|
| 1 | [`prd-reflection-bugfix.md`](prd-reflection-bugfix.md) | 🔴 Draft | Reflection not sent to user + retry + `/reflect` command | 0/12 done | Write failing tests (T1-T4 first) |

## Completed

| # | PRD | Completed | Summary |
|---|-----|-----------|---------|
| 1 | [`prd-nightly-reflection.md`](prd-nightly-reflection.md) | 2026-03-02 | Nightly reflection feature — Opus knowledge extraction on /sleep |
| 2 | [`bugfix-recollect-and-orphans.md`](bugfix-recollect-and-orphans.md) | 2026-03-01 | Recollect bug + orphan message cleanup |
| 3 | [`BATTLEPLAN-user-filter.md`](BATTLEPLAN-user-filter.md) | 2026-02 | User filtering for test vs production |

## Reference

| Doc | Purpose |
|-----|---------|
| [`SPEC.md`](SPEC.md) | Core spec — 27 numbered definitions (SPEC-01..27) |
| [`TESTING.md`](TESTING.md) | Three-tier testing strategy (unit + integration + E2E) |
| [`TODO.md`](TODO.md) | Legacy todo list (may contain untracked items) |

---

*Last updated: 2026-03-03 11:45 SGT*
