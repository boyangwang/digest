# Sleep Digest Bot 🌙

Standalone Telegram bot that collects OpenClaw conversation transcripts nightly,
generates intelligent summaries via Doudou (OpenClaw AI agent), and writes
them to an Obsidian vault for archival.

**Priority:** #1 pillar application (of the Three D's: Diary, Digest, Depo)

---

## ⚠️ Mandatory Development Process

> **Every agent working on this codebase MUST follow these steps in order.**
> **Skipping steps = broken product. No exceptions.**
>
> **Track all work in [`TODO.md`](TODO.md)** — active bugs, PRDs, backlog, completed.

### Step 0: Intake

Bug reported or feature requested → **check existing work first, then create PRD.**

1. **READ `TODO.md` first** — check Active Work and Backlog for duplicates. If someone is already working on this, coordinate instead of creating a new PRD.
2. **Check `specs/` directory** — `ls specs/prd-*.md` to see all existing PRDs. Search by keyword.
3. **If no duplicate exists:** Create `specs/prd-<descriptive-name>.md` using [PRD template](#prd-template) below
4. Set status badge: `🔴 Draft`
5. Add entry to [`TODO.md`](TODO.md) Active Work table
6. Commit: `spec: PRD for <name>`
7. **STOP.** Do not write code or tests yet. Wait for approval or proceed only if PRD was written collaboratively with Boyang.

**⚠️ Why steps 1-2 matter:** Multiple agents/sessions may work on this repo simultaneously. Creating duplicate PRDs wastes effort and creates confusion. Always check first.

### Step 1: TDD — Write Failing Tests

PRD approved → write tests that prove it's broken.

1. Update PRD status: `🟡 Active — writing tests`
2. Update `TODO.md` status + "Next Step"
3. For each task T1..TN: write a **failing** test
   - Unit/integration → `tests/test_<module>.py`
   - E2E → `tests/run_e2e.py`
4. Confirm new tests **FAIL** and existing tests still **PASS**
5. Commit: `test: failing tests for <PRD> (T1-TN)`

### Step 2: Implement

Failing tests exist → make them green, one task at a time.

1. Update PRD status: `🟡 Active — implementing`
2. For each task: implement → run test → check `- [x]` in PRD → commit `feat: T1 — desc`
3. After all tasks: run **ALL** unit/integration tests individually
4. Commit: `feat: all tasks complete for <PRD>`

### Step 3: E2E Verification

All tasks done → prove it works end-to-end.

1. Update PRD status: `🔵 Testing`
2. Run: `python3 tests/run_e2e.py --test all`
3. If failures: fix → re-run ALL tests → repeat until green
4. Record in PRD: `Unit: XXX passed | E2E: X/X passed | Date: YYYY-MM-DD`
5. Commit: `test: E2E verified for <PRD>`

### Step 4: Deploy & Close

All green → ship it.

1. Restart: `launchctl kickstart -k gui/$(id -u)/com.digest-bot`
2. Verify: `pgrep -f "digest-bot/main.py"`
3. **Send a real message to the bot** to verify production behavior
4. Update PRD status: `🟢 Done — Completed YYYY-MM-DD`
5. Move entry in `TODO.md` to "Completed" table
6. Commit: `docs: close <PRD>`
7. Notify Boyang

### Step 5: Post-Deploy Monitoring

1. Watch `/tmp/digest-bot.log` for errors
2. For nightly features: wait for natural trigger or ask Boyang to test
3. Errors found → new PRD (Step 0)

### Rules

1. **No code without a PRD.** Even quick fixes.
2. **No implementation without failing tests.** TDD is not optional.
3. **No "done" without E2E green.** If you can't prove it, it doesn't work.
4. **Every status change gets a commit.** Git history = audit trail.
5. **`TODO.md` is always current.** Stale = fix it first.
6. **One PRD at a time per agent.** Finish or park before starting another.

---

## Architecture

```
OpenClaw sessions (JSONL) → collector.py → raw messages
                                              ↓
                                   llm.py → save to file → Doudou reads & summarizes
                                              ↓
                                   recorder.py → Obsidian vault (YAML + Markdown)
                                              ↓
                                   main.py → Telegram DM to Boyang
                                              ↓ (/sleep)
                                   reflection.py → Opus extracts 8 knowledge categories
                                              ↓
                                   workspace memory files + Obsidian digest
```

## State Machine

```
IDLE   → /digest  → collect, create file, start nudging       → ACTIVE
ACTIVE → /digest  → collect NEW msgs, update same file         → ACTIVE
ACTIVE → text     → append verbatim recap                      → ACTIVE
ACTIVE → voice    → save .ogg, transcribe, append              → ACTIVE
ACTIVE → photo    → save .jpg to vault, append embed           → ACTIVE
ACTIVE → /sleep   → run reflection → finalize file, stop nudge → IDLE
IDLE   → /reflect → re-run reflection on last finalized digest → IDLE
```

## Commands

| Command | Description |
|---------|-------------|
| `/digest` | Generate or update nightly digest |
| `/sleep` | Run reflection + finalize + goodnight |
| `/status` | Check current state and document content |
| `/reflect` | Re-run reflection on most recent (or specified) digest |
| `/start` | Show help and available commands |

---

## Project Structure

```
digest-bot/
├── TODO.md              # ⚠️ START HERE — all work tracking
├── README.md            # This file — process + architecture
├── main.py              # Telegram bot, command handlers, state machine
├── collector.py         # Read OpenClaw JSONL transcripts, extract & filter
├── recorder.py          # Atomic writes to Obsidian vault, YAML frontmatter
├── llm.py               # Summary composition via Doudou (file-based handoff)
├── reflection.py        # Nightly reflection — Opus knowledge extraction
├── scheduler.py         # APScheduler: 22:30 digest, 30-min nudge cycle
├── stt.py               # Voice message transcription (OpenAI Whisper)
├── config.py            # All tokens, paths, constants
│
├── specs/               # 📋 PRDs and specs
│   ├── SPEC.md          # Core spec — 27 numbered definitions
│   ├── TESTING.md       # Three-tier testing strategy
│   └── prd-*.md         # Individual PRDs (features + bugfixes)
│
├── tests/               # 🧪 Test suites
│   ├── test_*.py        # Unit + integration tests (pytest)
│   ├── run_e2e.py       # Standalone E2E runner (NOT pytest)
│   └── conftest.py      # Shared fixtures
│
└── scripts/             # 🔧 Utility scripts
    └── backfill.py      # Historical digest backfill (Feb 7 → Mar 1)
```

---

## Testing

### Three Tiers (Mandatory)

| Tier | Tool | What | How to Run |
|------|------|------|------------|
| Unit | pytest | Individual functions, parsing, formatting | `pytest tests/test_recorder.py -v` |
| Integration | pytest | Module interactions, mock Telegram handlers | `pytest tests/test_integration.py -v` |
| E2E | `run_e2e.py` | Real Telegram UI via AppleScript | `python3 tests/run_e2e.py` |

### Running Tests

```bash
cd ~/digest-bot && source venv/bin/activate

# Unit + integration (run each file individually)
for f in tests/test_*.py; do
    [[ "$f" == *live_e2e* || "$f" == *test_e2e* ]] && continue
    python -m pytest "$f" -q
done

# E2E (Telegram Desktop must be open, bot must be running)
python3 tests/run_e2e.py              # All suites
python3 tests/run_e2e.py --test basic # Basic commands only
python3 tests/run_e2e.py -v           # Verbose
```

### Known Issue

`pytest` hangs when running 3+ async tests in bulk. Run test files individually.

---

## Output Locations

| What | Where |
|------|-------|
| Digest files | `NotesVault/Artificial-Colloquia/Doudou-Digest/YYYY-MM-DD-HHMM.md` |
| Conversation transcripts | `.../Doudou-Digest/transcripts/conv-YYYYMMDD-HHMMSS.md` |
| Reflection transcripts | `.../Doudou-Digest/transcripts/reflection-YYYY-MM-DD-*.md` |
| Test files | `.../Doudou-Digest/_test/test-*.md` |

## Service

- **LaunchAgent:** `~/Library/LaunchAgents/com.digest-bot.plist`
- **Log:** `/tmp/digest-bot.log`
- **Restart:** `launchctl kickstart -k gui/$(id -u)/com.digest-bot`
- **Status:** `pgrep -f "digest-bot/main.py"`

## Git

- **Remote:** `github-digest:boyangwang/digest.git`
- **Branch:** `main`

---

## PRD Template

```markdown
# PRD: <Title>

> **Status:** 🔴 Draft — <current state>
> **Project:** Sleep Digest Bot — <area>
> **Date:** YYYY-MM-DD
> **Priority:** P0 Critical | P1 High | P2 Medium | P3 Low
> **Estimated effort:** Small (1-2hr) | Medium (2-4hr) | Large (4-8hr)
> **Origin:** <who reported, context>
> **Tasks:** 0/N complete

---

## Problem Statement
<What's broken. Be specific. Include evidence.>

## Root Cause Analysis
<Why it's broken. Reference code files + line numbers.>

## Tasks
- [ ] **T1** — <description>
- [ ] **T2** — <description>

## Acceptance Criteria
1. <testable condition>

## Files to Modify
| File | Changes |
|------|---------|

## Verification Results
_Filled at Step 3_
- Unit/integration: ___ passed
- E2E: ___/___ passed
- Date: ___
```
