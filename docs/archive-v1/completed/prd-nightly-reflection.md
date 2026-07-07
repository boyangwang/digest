# PRD: Nightly Reflection — Automated Knowledge Extraction

> **Status:** 🟢 Done — Completed 2026-03-02. Bugfixes tracked in `prd-reflection-bugfix.md`
> **Project:** Sleep Digest Bot — Nightly Reflection Feature
> **Date:** 2026-03-02
> **Priority:** P1 High
> **Estimated effort:** Large (4-8hr)
> **Origin:** Boyang's request + "How to Hire an AI" proposals A3, G3, G4

---

## Context

### Current System (How It Actually Works)

The Sleep Digest Bot is a standalone Python Telegram bot (`main.py`) with this state machine:

```
IDLE  → /digest → collect conversations via collector.py, compose per-session
                   summaries via llm.py (Doudou), create file via recorder.py,
                   start nudge cycle → ACTIVE
ACTIVE → /digest → collect NEW messages since coverage_to, append summaries → ACTIVE
ACTIVE → text    → append verbatim recap to "# Boyang's Recap" + re-collect → ACTIVE
ACTIVE → voice   → save .ogg to vault, transcribe via stt.py, append → ACTIVE
ACTIVE → photo   → save .jpg to vault, append embed → ACTIVE
ACTIVE → /sleep  → set status="final", stop nudging → IDLE
```

**Key architecture facts** (from reading the actual code):
- **Sleep detection:** `/sleep` command ONLY. No verbal/lexical detection. (`cmd_sleep` in `main.py`)
- **LLM interaction:** `llm.py` calls `openclaw agent --local --session-id digest-bot` as a subprocess. Conversations are saved to a file in `transcripts/`, then Doudou is told to read that file and compose a summary.
- **File writes:** All via `recorder.py` using atomic writes (`.tmp` → `os.rename`). This pattern has been working without Obsidian sync conflicts.
- **Scheduling:** `scheduler.py` uses APScheduler — 22:30 SGT digest trigger, 30-min nudge cycle (22:30–07:00).
- **Document format:** v2 per `specs/SPEC.md` — two sections only: `# Doudou's Summary` + `# Boyang's Recap`. No raw conversations in digest file (stored in `transcripts/`).
- **File naming:** `YYYY-MM-DD-HHMM.md` (supports multiple per day, date-decoupled).

### The Problem

The digest captures WHAT was discussed (summaries + verbatim recap). But it extracts no structured knowledge. Facts, preferences, corrections, decisions, ideas — all buried in narrative text. My workspace files (`RULES.md`, `MEMORY.md`, `memory/*.md`) are updated only when I remember during conversation. This is unreliable.

### The Solution

Add a **Nightly Reflection** step that triggers when `/sleep` is received. Before finalizing the document, the system:

1. Collects all conversation transcripts for this cycle (reusing `collector.py`)
2. Spawns an Opus sub-agent to extract structured knowledge across 8 categories
3. Stores extracted items in the correct workspace locations
4. Appends a reflection report to the digest document
5. THEN finalizes (sets `status: "final"`)

---

## Architecture

### Trigger: Extending `/sleep`

The trigger is the existing `/sleep` command handler (`cmd_sleep` in `main.py`). The flow becomes:

```python
# Current /sleep flow:
async def cmd_sleep(update, context):
    _scheduler.mark_sleep()
    finalize()              # sets status="final"
    reply("晚安 🌙")

# New /sleep flow:
async def cmd_sleep(update, context):
    _scheduler.mark_sleep()
    reply("晚安 🌙 Running reflection...")
    await run_reflection()  # NEW: extract knowledge, append to digest
    finalize()              # THEN finalize (status="final")
    reply("🪞 Reflection complete. Saved to Obsidian ✅")
```

**No verbal/lexical sleep detection.** The `/sleep` command is the sole trigger. This is the existing pattern and it works.

**No fallback timer.** The document eventually seals when `/sleep` is sent. The bot can't be active forever — the scheduler resets at noon, and a new `/digest` creates a new file.

### Data Source: Reusing collector.py

The reflection reads the same data as `generate_digest()`:

```python
from collector import collect_all_messages, format_messages, group_by_session

# Get cycle boundaries from active digest file
status = get_active_status()
coverage_from = datetime.fromisoformat(status["coverage_from"])
coverage_to = datetime.now(SGT)  # everything up to now

# Collect all messages in this cycle
prev_night, today_msgs = collect_all_messages(coverage_from)
all_msgs = prev_night + today_msgs
formatted = format_messages(all_msgs)
```

This is the exact same collector logic already proven across 271 tests. No new data collection code needed.

### Execution Model: Sub-Agent on Opus

**Why sub-agent, not inline:**
- The reflection needs to process 50-100K tokens of conversation data
- `llm.py` already uses the sub-process pattern (`openclaw agent --local`)
- A fresh context window is cleaner for extraction
- The same prompt is reusable for historical backfill

**Implementation:** New module `reflection.py` (following the pattern of `llm.py`):

```python
def run_reflection(conversations_text: str, existing_memory: dict) -> dict:
    """
    1. Save conversations + existing memory context to temp files
    2. Call `openclaw agent --local` with Opus model and reflection prompt
    3. Agent reads files, extracts 8 categories, writes to workspace files
    4. Agent returns structured JSON summary of what was extracted
    5. Parse and return for appending to digest document
    """
```

The sub-agent has full tool access (read, write, edit, exec) — it can directly modify workspace files, run git, etc. Same as how `llm.py` already delegates to Doudou.

**Model: Opus always.** This is persistent knowledge. No cost-cutting.

### Interaction Between Digest Bot and OpenClaw

Already working via `llm.py`'s `_ask_doudou()` pattern:

```
Digest Bot (Python)
  → saves data to file (transcripts/)
  → subprocess: `openclaw agent --local --session-id digest-bot-reflection --message "..."`
  → OpenClaw agent reads file, does work, writes results
  → subprocess returns JSON response
  → Digest Bot parses response, appends to digest document
```

The reflection follows this EXACT pattern. No new interaction mechanism needed.

---

## Extraction Categories (8)

### 1. 📌 Durable Facts
**What:** Information that remains true beyond today. People, places, companies, numbers, relationships, dates.
**Examples:** "Ashley's birthday is March 15", "VO2max measured at 46"
**Workspace:** `memory/facts/YYYY-MM-DD.md` (new directory, one file per day)
**Format:** `- **[Category/Entity]** Fact statement`

### 2. 🔧 Feedback Lessons
**What:** Corrections Boyang gives me. Behavior changes, output preferences, style adjustments.
**Examples:** "Don't use markdown tables in Telegram", "Always search before answering"
**Workspace:** `memory/feedback-lessons.md` (append-only, with dates)
**Also:** Auto-apply to RULES.md when correction is clear and unambiguous. Log every auto-applied change in the reflection report.

### 3. ⚠️ Rules & Incidents
**What:** Formalized rules, near-misses, mistakes, patterns to prevent.
**Examples:** "I fabricated a technical explanation", "New rule: verify before acting"
**Workspace:** INCIDENTS.md, RULES.md
**Policy:** Auto-apply sparingly. Only when evidence is clear. Every change logged in report.

### 4. 🌟 Compliments & Positive Feedback
**What:** What Boyang praised, what went well, positive signals about my performance.
**Examples:** "That analysis was excellent", "Good catch", sharing my work with others
**Workspace:** `memory/compliments.md` (append-only)

### 5. 🧭 Decisions & Rationale
**What:** Choices made and WHY. The rationale matters more than the decision.
**Examples:** "Use Opus for reflection — quality matters for persistent knowledge"
**Workspace:** `memory/decisions/YYYY-MM-DD.md` (new directory)

### 6. 📋 Action Items & Commitments
**What:** Promises, tasks planned, follow-ups needed. Things that should be on KANBAN.
**Examples:** "Set up Cloudflare Tunnel tomorrow", "Research X"
**Workspace:** KANBAN.md (direct add, check for duplicates)

### 7. 💡 Ideas & Brainstorms
**What:** Ideas mentioned in passing, creative suggestions, future possibilities.
**Examples:** "What if we built a voice-first LP interface?"
**Workspace:** `memory/ideas.md` (append-only)

### 8. 🔬 Technical Learnings
**What:** New tools, API quirks, debugging insights, architectural patterns.
**Examples:** "OpenClaw sessions.json uses mtime for cache invalidation"
**Workspace:** `memory/YYYY-MM-DD.md` (existing daily files) + TOOLS.md if critical

---

## Output: Reflection Section in Digest Document

After extraction, a new section is appended to the digest document BEFORE finalization:

```markdown
# Doudou's Summary
[existing summaries]

# Boyang's Recap
[existing recap entries]

# 🪞 Nightly Reflection

> Extracted by Doudou (Opus). All items stored in workspace.

### 📌 Durable Facts (3)
- **[People/Ashley]** Birthday is March 15
- **[Health]** VO2max: 46 (up from 44)
- **[Portfolio/CompanyX]** Series A at $50M

### 🔧 Feedback Lessons (1)
- **[Formatting]** No markdown tables in Telegram

### ⚠️ Rules & Incidents (0)
_None identified today._

### 🌟 Compliments (1)
- "That analysis was thorough" — re: hire-ai report

### 🧭 Decisions (2)
- Use Opus for nightly reflection (quality > cost)
- Implement table-share as proper skill

### 📋 Action Items (1)
- [ ] Set up Cloudflare Tunnel evaluation → added to KANBAN

### 💡 Ideas (1)
- CGM morning health briefing

### 🔬 Technical Learnings (1)
- `openclaw agent --local` subprocess pattern for digest-bot ↔ OpenClaw

### 📊 Stats
- Messages processed: 142
- Sessions scanned: 5
- Items extracted: 10
- Model: Opus
- Duration: ~4 min
```

**This adds a third top-level heading** (`# 🪞 Nightly Reflection`) to the document format. Requires spec amendments:

### New Spec Definitions (to add to `specs/SPEC.md`)

**SPEC-STRUCT-04: Optional third section — Nightly Reflection**
The document MAY have a third top-level heading `# 🪞 Nightly Reflection`, appended after `# Boyang's Recap`. This section is optional — only present when reflection has run.

**SPEC-REFLECT-01: Reflection triggers on `/sleep`**
When `/sleep` is received and an active digest exists, the reflection step runs BEFORE `finalize()`. If reflection fails, `/sleep` still finalizes.

**SPEC-REFLECT-02: Reflection section position**
`# 🪞 Nightly Reflection` is always the LAST section in the document, after `# Boyang's Recap`.

**SPEC-REFLECT-03: Reflection section format**
Contains 8 subsections (### level) for each extraction category, each with item count in the heading. Empty categories show `_None identified today._`. Ends with `### 📊 Stats`.

**SPEC-REFLECT-04: YAML fields added by reflection**
Reflection adds two fields to frontmatter: `reflection_at: "ISO8601"` and `reflection_model: "opus"`.

**SPEC-REFLECT-05: Reflection is non-blocking**
If the agent subprocess fails, times out, or returns empty — the reflection section is omitted and `/sleep` proceeds to finalize normally. The bot NEVER hangs on a failed reflection.

**SPEC-REFLECT-06: Idempotent reflection**
If `/sleep` is called twice (e.g., retry), the second call finds status already "final" and skips. No duplicate reflection sections.

---

## Workspace Changes After Reflection

| File | Action | Notes |
|------|--------|-------|
| `memory/facts/YYYY-MM-DD.md` | Create | Tagged durable facts |
| `memory/feedback-lessons.md` | Append | Corrections and preferences |
| `memory/compliments.md` | Append | Positive feedback log |
| `memory/decisions/YYYY-MM-DD.md` | Create | Decisions with rationale |
| `memory/ideas.md` | Append | Ideas and brainstorms |
| `memory/YYYY-MM-DD.md` | Append | Technical learnings |
| `KANBAN.md` | Append | New action items (deduped) |
| `INCIDENTS.md` | Append | New incidents (rare, cautious) |
| `RULES.md` | Append | New rules (rare, only clear corrections) |
| Git | Commit + push | `"nightly-reflection: YYYY-MM-DD"` |

**RULES.md / INCIDENTS.md policy:** Auto-apply but sparingly. Only when the correction is unambiguous. Every change logged in the reflection report for auditability. Boyang reviews in morning briefing.

---

## Historical Backfill

### Overview

Generate historical digest + reflection documents for all days with available transcript data.

**Available data:** 88 transcript files. Earliest message: `2026-02-07T05:44:31Z` (13:44 SGT).

**Existing digest files:** Only Mar 1-2 (testing artifacts with `YYYY-MM-DD-HHMM` naming). No pre-existing digests for Feb 7-28.

### Cycle Definition

**Universal cycle boundary: 22:30 SGT** (matches `DIGEST_HOUR`/`DIGEST_MINUTE` in `config.py`).

| Day's Document | coverage_from | coverage_to |
|---------------|---------------|-------------|
| Feb 7 (first) | Earliest available message (Feb 7 13:44 SGT) | Feb 7 22:30 SGT |
| Feb 8 | Feb 7 22:30 SGT | Feb 8 22:30 SGT |
| ... | previous day 22:30 | current day 22:30 |
| Mar 1 (last backfill) | Feb 28 22:30 SGT | Mar 1 22:30 SGT |

- **Feb 7 is partial** — coverage starts from earliest message, not midnight or previous 22:30
- **Mar 1 is the last backfill day** — Mar 2 onward has the live system
- **Total: 23 documents** (Feb 7 through Mar 1)

### Backfill Document Format

Each backfill document follows the same v2 format, with additions:

```yaml
---
generated_at: "2026-03-03T01:00:00+08:00"    # when backfill ran
coverage_from: "2026-02-07T13:44:31+08:00"
coverage_to: "2026-02-07T22:30:00+08:00"
status: backfill                               # NOT "final" — no /sleep was sent
backfill: true
backfill_at: "2026-03-03T01:00:00+08:00"
reflection_at: "2026-03-03T01:05:00+08:00"
reflection_model: opus
---

# Doudou's Summary
[per-session summaries, same as live format]

# Boyang's Recap
_No recap — historical backfill. Recap system started March 2026._

# 🪞 Nightly Reflection
[full 8-category extraction]
```

### Backfill Rules (Strict)

1. **Cycle boundary = 22:30 SGT** (from `config.py` `DIGEST_HOUR`/`DIGEST_MINUTE`)
2. **No Boyang recap** — the recap system didn't exist. Section reads: `_No recap — historical backfill._`
3. **Status = "backfill"** — never "active" or "final" (no real `/sleep` was sent)
4. **YAML includes `backfill: true`** — machine-readable flag
5. **Full summaries + reflection** — same quality as live documents
6. **No overwrites** — skip any date that already has a digest file
7. **Process chronologically (oldest first)** — correct cumulative deduplication
8. **File naming:** `YYYY-MM-DD-2230.md` (uniform, since 22:30 is the cycle boundary)
9. **Feb 7 is partial:** coverage_from = earliest message, coverage_to = 22:30 SGT
10. **Extracted items tagged with source date** — `memory/facts/2026-02-08.md` for Feb 8's facts

### Backfill Script

`scripts/backfill.py` — standalone script that:
1. Iterates dates Feb 7 → Mar 1 chronologically
2. For each date: defines cycle, collects messages via `collector.py`, composes summaries via `llm.py`
3. Spawns Opus sub-agent for reflection extraction (same prompt as live)
4. Writes digest file to Obsidian vault
5. Commits workspace changes: `"nightly-reflection: backfill YYYY-MM-DD"`
6. Pauses between days (rate limiting)

### Backfill Cost Estimate

| Item | Calculation | Cost |
|------|-------------|------|
| 23 days × LLM summary | 23 × ~$0.30 (Sonnet via llm.py) | ~$6.90 |
| 23 days × Opus reflection | 23 × ~$1.50 (75K in, 5K out) | ~$34.50 |
| **Total backfill** | | **~$41.40** |

---

## New Files in Digest Repo

| File | Purpose |
|------|---------|
| `reflection.py` | Reflection orchestration — collect data, spawn Opus agent, parse results |
| `scripts/backfill.py` | Historical backfill runner |
| `tests/test_reflection.py` | Unit + integration tests for reflection |
| `specs/SPEC.md` | Amendment: SPEC-STRUCT-04 (third section: `# 🪞 Nightly Reflection`) |

## New Files in Workspace

| File | Purpose |
|------|---------|
| `memory/facts/` | Directory for daily fact files |
| `memory/decisions/` | Directory for daily decision files |
| `memory/feedback-lessons.md` | Append-only feedback/correction log |
| `memory/compliments.md` | Append-only positive feedback log |
| `memory/ideas.md` | Append-only idea capture |
| `templates/reflection-prompt.md` | The master prompt sent to Opus sub-agent |

---

## Requirements

### Core

- [ ] R1: Trigger = `/sleep` command (existing handler, extended)
- [ ] R2: Conversation collection reuses `collector.py` with cycle boundaries from active digest
- [ ] R3: 8-category extraction via Opus sub-agent
- [ ] R4: Each category stored in its designated workspace location
- [ ] R5: Deduplication — check existing files before adding (sub-agent reads existing memory files)
- [ ] R6: Reflection report appended to digest document as `# 🪞 Nightly Reflection`
- [ ] R7: Document finalized (status="final") AFTER reflection completes
- [ ] R8: Git commit + push after workspace changes
- [ ] R9: Idempotent — running twice produces no duplicates
- [ ] R10: Auditable — report shows exactly what was extracted and where stored

### Model & Execution

- [ ] R11: Model is always Opus
- [ ] R12: Uses `openclaw agent --local` subprocess pattern (same as `llm.py`)
- [ ] R13: Conversations saved to file, agent reads via `read` tool (same as `compose_summary`)

### Error Handling

- [ ] R14: If reflection sub-agent fails, still finalize the document (never block `/sleep`)
- [ ] R15: Log errors to `/tmp/digest-bot.log`
- [ ] R16: Send error message to Boyang via Telegram if reflection fails

### Spec Amendments

- [ ] R17: Add SPEC-STRUCT-04 to `specs/SPEC.md` — third section `# 🪞 Nightly Reflection`
- [ ] R18: Update SPEC-STRUCT-01 to say "two or three sections" (reflection is optional)

### Historical Backfill

- [ ] R19: Backfill script processes Feb 7 → Mar 1 (23 days, chronological)
- [ ] R20: 22:30 SGT cycle boundary (from config.py constants)
- [ ] R21: Feb 7 partial coverage (earliest message → 22:30)
- [ ] R22: No Boyang recap in backfill documents
- [ ] R23: Status = "backfill", YAML includes `backfill: true`
- [ ] R24: No overwrites of existing digest files
- [ ] R25: Workspace changes committed per-day

---

## Testing Strategy

### Three-Tier Testing Principle (MANDATORY)

This repository follows a strict three-tier testing approach. Every new feature MUST have tests at all three levels:

| Tier | Tests In | What | How |
|------|----------|------|-----|
| **Unit** | `tests/test_reflection.py` | Pure logic, mocked deps | `pytest` with mocks |
| **Integration** | `tests/test_reflection.py` | Module interactions, mocked LLM | Real file I/O, mocked subprocess |
| **Live E2E** | `tests/test_live_e2e.py` | Full lifecycle via Telegram UI | Peekaboo UI automation on Mac Mini |

**E2E is non-negotiable.** The live E2E tests send real Telegram messages via the Mac Mini's Telegram Desktop client (@claw0606) to the real running bot (@sleep_digest_bot), using Peekaboo for UI automation (`tests/telegram_ui.py`). This catches bugs that unit/integration tests cannot — network issues, bot handler registration, Obsidian file sync, real LLM responses.

**Existing E2E infrastructure:**
- `tests/telegram_ui.py` — Peekaboo-based helper (navigate_to_bot, send_message, read_last_bot_reply, etc.)
- `tests/test_live_e2e.py` — 9 live tests including full `/digest` → text → `/sleep` cycle
- Requires: Telegram Desktop running + bot running via launchd

### Reflection-Specific Test Plan

#### Unit Tests (in `tests/test_reflection.py`)

- [ ] UT1: `parse_reflection_response()` — parse structured JSON from agent into 8 categories
- [ ] UT2: `parse_reflection_response()` — handle malformed/partial JSON gracefully
- [ ] UT3: `build_reflection_prompt()` — correct prompt with file paths and instructions
- [ ] UT4: `format_reflection_report()` — markdown report from parsed categories
- [ ] UT5: `format_reflection_report()` — empty categories show `_None identified today._`
- [ ] UT6: `format_reflection_report()` — stats section includes message count, session count, duration
- [ ] UT7: Edge case: zero messages in cycle → reflection gracefully skips
- [ ] UT8: Edge case: agent returns empty response → graceful fallback

#### Integration Tests (in `tests/test_reflection.py`)

- [ ] IT1: `run_reflection()` with mocked `subprocess.run` — verify correct CLI args (`--local`, `--session-id`, `--model`)
- [ ] IT2: `run_reflection()` — conversations saved to temp file before agent call
- [ ] IT3: `run_reflection()` — agent subprocess timeout → returns fallback, no crash
- [ ] IT4: `run_reflection()` — agent subprocess failure (rc≠0) → returns fallback, no crash
- [ ] IT5: `append_reflection()` in recorder.py — reflection section appended AFTER `# Boyang's Recap`
- [ ] IT6: `append_reflection()` — idempotent: calling twice doesn't duplicate
- [ ] IT7: `append_reflection()` — YAML frontmatter gets `reflection_at` and `reflection_model` fields
- [ ] IT8: `cmd_sleep` with reflection — finalize() called AFTER reflection completes
- [ ] IT9: `cmd_sleep` with reflection failure — finalize() still called (graceful degradation)
- [ ] IT10: Atomic write — reflection append uses `.tmp` → `os.rename` pattern

#### Live E2E Tests (added to `tests/test_live_e2e.py`)

- [ ] E2E1: Full reflection cycle — `/digest` → send text → `/sleep` → verify `# 🪞 Nightly Reflection` section exists in test digest file
- [ ] E2E2: Reflection failure graceful — mock agent failure, `/sleep` still finalizes, bot replies "晚安 🌙"
- [ ] E2E3: Verify test digest file has three sections after `/sleep`: `# Doudou's Summary`, `# Boyang's Recap`, `# 🪞 Nightly Reflection`

---

## Tasks

### Phase 1: Infrastructure

- [ ] T1: Create workspace directories: `memory/facts/`, `memory/decisions/`
- [ ] T2: Create seed files: `memory/feedback-lessons.md`, `memory/compliments.md`, `memory/ideas.md`
- [ ] T3: Write reflection prompt template: `templates/reflection-prompt.md`
- [ ] T4: Write `reflection.py` — data collection, sub-agent orchestration, result parsing
- [ ] T5: Amend `specs/SPEC.md` with SPEC-STRUCT-04 and SPEC-REFLECT-01..06

### Phase 2: Tests First (TDD — per workspace RULES.md §17)

- [ ] T6: Write `tests/test_reflection.py` — all unit tests (UT1-UT8) + integration tests (IT1-IT10)
- [ ] T7: Run tests — all FAIL (no implementation yet)

### Phase 3: Implementation

- [ ] T8: Implement `reflection.py` — `run_reflection()`, `build_reflection_prompt()`, `parse_reflection_response()`, `format_reflection_report()`
- [ ] T9: Add `append_reflection()` to `recorder.py` (follows `append_recap` pattern, inserts after `# Boyang's Recap`)
- [ ] T10: Modify `cmd_sleep` in `main.py` — call `run_reflection()` before `finalize()`
- [ ] T11: Run tests — all unit + integration PASS

### Phase 4: E2E Verification

- [ ] T12: Add E2E tests (E2E1-E2E3) to `tests/test_live_e2e.py`
- [ ] T13: Run full test suite — all 271+ existing tests pass + all new tests pass
- [ ] T14: Manual live test: real `/sleep` → verify Obsidian file + workspace files updated

### Phase 5: Backfill

- [ ] T15: Write `scripts/backfill.py`
- [ ] T16: Dry run on one day (Feb 8) — verify document format and extraction quality
- [ ] T17: Run full backfill (23 days, chronological)
- [ ] T18: Verify all 23 digest files + workspace memory files
- [ ] T19: Verify timestamp chain integrity

---

## Acceptance Criteria

- [ ] AC1: `/sleep` triggers reflection → results in Obsidian + workspace → then finalizes
- [ ] AC2: If reflection fails, `/sleep` still finalizes (graceful degradation)
- [ ] AC3: Reflection report is readable and well-formatted in Obsidian
- [ ] AC4: Workspace files correctly updated with no duplicates
- [ ] AC5: RULES.md auto-applied changes are sparse and logged
- [ ] AC6: All new tests pass + existing 271 tests still pass
- [ ] AC7: Backfill produces 23 documents (Feb 7 → Mar 1)
- [ ] AC8: Total cost per nightly run ≤ $2.00

---

## Cost Estimate

### Nightly Run (Ongoing)

| Component | Model | Est. Tokens | Cost |
|-----------|-------|-------------|------|
| Conversation data prep | Python | 0 | $0.00 |
| Reflection sub-agent | Opus ($15/$75 per 1M) | ~75K in, ~5K out | ~$1.50 |
| **Per nightly run** | | | **~$1.50** |
| **Monthly (30 days)** | | | **~$45.00** |

### Historical Backfill (One-time)

| Component | Calculation | Cost |
|-----------|-------------|------|
| 23 summaries via Sonnet | 23 × $0.30 | ~$6.90 |
| 23 reflections via Opus | 23 × $1.50 | ~$34.50 |
| **Total backfill** | | **~$41.40** |

---

## Decisions Log

| Question | Decision | Rationale |
|----------|----------|-----------|
| Trigger mechanism | `/sleep` command only | That's what the code does. No verbal detection. |
| Execution model | Sub-agent via `openclaw agent --local` | Matches `llm.py` pattern. Reusable for backfill. |
| Model | Opus always | Quality matters for persistent knowledge |
| RULES.md safety | Auto-apply, sparingly | Boyang's preference. Every change logged. |
| Fallback timer | None | Document seals when `/sleep` sent. |
| KANBAN auto-add | Direct add | Reduce friction |
| Obsidian sync conflict | Use existing atomic write pattern | `recorder.py`'s `.tmp` → `os.rename` has been working |
| Backfill range | Feb 7 → Mar 1 (23 days) | Earliest transcript: Feb 7 13:44 SGT |
| Code location | `~/digest-bot/` (this repo) | Feature of the digest bot, not the workspace |

---

*PRD version 2.0 — 2026-03-02 — Rewritten from actual codebase, not hallucination. 🦮*
