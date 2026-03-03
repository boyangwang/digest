# Specs Index — Single Source of Truth for All Work

> **New developer / AI agent: START HERE.**
> This file tells you what's in progress, what's done, and what to work on next.
> **Then read [`SOP.md`](SOP.md)** for the mandatory step-by-step workflow.

---

## How This Works

1. Every bug or feature gets a PRD in `specs/prd-<name>.md`
2. Each PRD has tasks (`- [ ]` / `- [x]`) and a status badge at the top
3. This INDEX tracks all PRDs in one place with current status
4. **[`SOP.md`](SOP.md)** defines the mandatory 5-step lifecycle: Intake → TDD → Implement → E2E → Deploy
5. **Status flow:** `🔴 Draft → 🟡 Active → 🔵 Testing → 🟢 Done` (or `⚫ Stale`)

### What to Do

1. Find the first 🔴/🟡/🔵 entry below
2. Open the PRD — check the status badge for current phase
3. Follow [`SOP.md`](SOP.md) for that phase — it tells you the exact next action
4. **Never skip E2E verification**

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
