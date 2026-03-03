# Sleep Digest Bot 🌙

Standalone Telegram bot that collects OpenClaw conversation transcripts nightly,
generates intelligent summaries via Doudou (OpenClaw AI agent), and writes
them to an Obsidian vault for archival.

**Priority:** #1 pillar application (of the Three D's: Diary, Digest, Depo)

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
├── main.py              # Telegram bot, command handlers, state machine
├── collector.py         # Read OpenClaw JSONL transcripts, extract & filter
├── recorder.py          # Atomic writes to Obsidian vault, YAML frontmatter
├── llm.py               # Summary composition via Doudou (file-based handoff)
├── reflection.py        # Nightly reflection — Opus knowledge extraction
├── scheduler.py         # APScheduler: 22:30 digest, 30-min nudge cycle
├── stt.py               # Voice message transcription (OpenAI Whisper)
├── config.py            # All tokens, paths, constants
│
├── specs/               # 📋 All PRDs, specs, and bug reports
│   ├── SPEC.md          # Core spec — 27 numbered definitions
│   ├── TESTING.md       # Three-tier testing strategy
│   ├── prd-*.md         # PRDs for features and bugfixes
│   └── *.md             # Historical specs and battleplans
│
├── tests/               # 🧪 Test suites
│   ├── test_*.py        # Unit + integration tests (pytest)
│   ├── run_e2e.py       # Standalone E2E runner (NOT pytest — see note below)
│   └── conftest.py      # Shared fixtures
│
├── scripts/             # 🔧 Utility scripts
│   └── backfill.py      # Historical digest backfill (Feb 7 → Mar 1)
│
└── venv/                # Python virtual environment
```

---

## Specs & PRDs

**All specifications live in `specs/`.** This is the single source of truth for what to build and what to fix.

### Naming Convention

| Type | Pattern | Example |
|------|---------|---------|
| Feature PRD | `prd-<feature>.md` | `prd-nightly-reflection.md` |
| Bugfix PRD | `prd-<area>-bugfix.md` | `prd-reflection-bugfix.md` |
| Core spec | `SPEC.md` | Numbered definitions (SPEC-01..27) |
| Testing | `TESTING.md` | Three-tier testing strategy |

### Workflow

1. **Bug reported** → Write PRD in `specs/prd-<name>.md` with tasks `T1..TN`
2. **PRD approved** → Write failing tests (TDD)
3. **Implement** → Check off tasks in PRD as completed
4. **E2E verify** → All tests pass via `run_e2e.py` + unit/integration via `pytest`
5. **Never claim "done" without E2E green**

### Active PRDs

| PRD | Status | Description |
|-----|--------|-------------|
| `prd-reflection-bugfix.md` | 🔴 Active | Bug #1: Reflection not sent to user, retry, /reflect command |
| `prd-nightly-reflection.md` | ✅ Done | Original reflection feature (v2.1) |

---

## Testing

### Three Tiers (Mandatory)

| Tier | Tool | What | How to Run |
|------|------|------|------------|
| Unit | pytest | Individual functions, parsing, formatting | `pytest tests/test_recorder.py -v` |
| Integration | pytest | Module interactions, mock Telegram handlers | `pytest tests/test_integration.py -v` |
| E2E | `run_e2e.py` | Real Telegram UI via Peekaboo/AppleScript | `python3 tests/run_e2e.py` |

### Running Tests

```bash
cd ~/digest-bot
source venv/bin/activate

# Unit + integration (run each file individually — bulk run has known pytest-asyncio hang)
for f in tests/test_*.py; do
    [[ "$f" == *live_e2e* || "$f" == *test_e2e* ]] && continue
    python -m pytest "$f" -q
done

# E2E (standalone runner — Telegram Desktop must be open, bot must be running)
python3 tests/run_e2e.py              # All suites
python3 tests/run_e2e.py --test basic # Basic commands only
python3 tests/run_e2e.py -v           # Verbose output
```

### Known Issue

`pytest` hangs when running 3+ tests involving `subprocess.run(osascript)` or async handlers in bulk.
Workaround: run test files individually, use `run_e2e.py` for live tests.

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
