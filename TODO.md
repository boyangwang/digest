# TODO — Sleep Digest Bot

> **New developer / AI agent: START HERE.**
> This file tracks all work — active, completed, and historical.
> **Read the [Mandatory Development Process](README.md#-mandatory-development-process) in README.md.**

---

## Active Work

| ID | PRD | Status | Summary | Tasks | Next Step |
|------|-----|--------|---------|-------|-----------|
| DIGEST-007 | [`prd-reflection-bugfix.md`](specs/prd-reflection-bugfix.md) | 🟡 Active | Reflection report to user + visual diffs + retry + /reflect | 10.5/19 | T5: fallback improvement, T16-19: retry + /reflect |

| DIGEST-009 | [`prd-collection-engine.md`](specs/prd-collection-engine.md) | 🔴 Draft | Parallel + retriable + supersedable collection with generation counter | 0/14 | Awaiting Boyang approval |

---

## Backlog (Needs PRD)

### ~~Text re-collection silent failure~~ → Covered by `prd-collection-engine.md`
### ~~LLM summary generation reliability~~ → Covered by `prd-collection-engine.md`

*(Investigation 2026-03-03: "silent failures" were actually correct 0-message results — Boyang was talking to digest bot, not OpenClaw sessions. Real issues are architectural: sequential collection, fallback advancing coverage, no supersession. All addressed in collection engine PRD.)*

---

## Completed

| ID | PRD | Completed | Summary |
|------|-----|-----------|---------|
| DIGEST-008 | [`prd-singleton-guard.md`](specs/prd-singleton-guard.md) | 2026-03-03 | PID lock singleton guard + 29 orphan files cleaned |
| DIGEST-006 | [`prd-nightly-reflection.md`](specs/prd-nightly-reflection.md) | 2026-03-02 | Nightly reflection — Opus knowledge extraction on /sleep |
| DIGEST-005 | [`bugfix-recollect-and-orphans.md`](specs/bugfix-recollect-and-orphans.md) | 2026-03-01 | Recollect bug + orphan message cleanup |
| DIGEST-004 | Voice message feature | 2026-03-01 | SPEC-VOICE implemented, 220+ tests passing |
| DIGEST-003 | [`BATTLEPLAN-user-filter.md`](specs/BATTLEPLAN-user-filter.md) | 2026-02 | User filtering, test mode, UI automation |
| DIGEST-002 | Token revocation | 2026-02 | Old token revoked, new token deployed |
| DIGEST-001 | Nightly check-in cron | 2026-02 | Cron `22de298f` disabled |

---

## Reference

| Doc | Purpose |
|-----|---------|
| [README.md](README.md) | **Mandatory** 5-step lifecycle (Intake → TDD → Implement → E2E → Deploy) |
| [`specs/SPEC.md`](specs/SPEC.md) | Core spec — 27 numbered definitions |
| [`specs/TESTING.md`](specs/TESTING.md) | Three-tier testing strategy (unit + integration + E2E) |

---

## Status Legend

| Badge | Meaning |
|-------|---------|
| 🔴 Draft | PRD written, not yet started |
| 🟡 Active | Implementation in progress |
| 🔵 Testing | E2E verification in progress |
| 🟢 Done | All verified and deployed |
| ⚫ Stale | Abandoned or superseded |

---

*Last updated: 2026-03-03 13:50 SGT*
