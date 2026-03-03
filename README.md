# Sleep Digest Bot 🌙

Standalone Telegram bot that collects OpenClaw conversation transcripts nightly,
generates intelligent summaries via Doudou (OpenClaw AI agent), and writes
them to an Obsidian vault for archival.

**AI agents: read [`AGENTS.md`](AGENTS.md) first.**

---

## Commands

| Command | Description |
|---------|-------------|
| `/digest` | Start or update nightly digest |
| `/sleep` | Run reflection, finalize, goodnight |
| `/status` | Check current state |
| `/reflect` | Re-run reflection on latest digest |

## Architecture

→ Full details: [`docs/architecture/overview.md`](docs/architecture/overview.md)

```
Telegram → main.py → collector.py → collection_engine.py (parallel)
                                         ↓
                                    llm.py → openclaw agent (per session)
                                         ↓
                                    recorder.py → Obsidian vault
                                         ↓ (/sleep)
                                    reflection.py → workspace memory files
```

## Development

### 5-Step Lifecycle (Mandatory)

1. **Intake** — PRD in `specs/`, assign DIGEST-XXX ID
2. **TDD** — Write failing tests first
3. **Implement** — Make tests green
4. **E2E** — `python3 tests/run_e2e.py --verbose`
5. **Deploy** — `launchctl kickstart -k gui/$(id -u)/com.digest-bot`

### Quick Commands

```bash
# Unit tests (run files individually — bulk hangs on async)
python3 -m pytest tests/test_recorder.py -v
python3 -m pytest tests/test_reflection.py -v
python3 -m pytest tests/test_collection_engine.py -v
python3 -m pytest tests/test_derived_sessions.py -v

# E2E (bot must be running, Telegram Desktop open)
python3 tests/run_e2e.py --verbose
python3 tests/run_e2e.py --test collection

# Restart
launchctl kickstart -k gui/$(id -u)/com.digest-bot

# Logs
tail -f /tmp/digest-bot.log
```

## Project Structure

```
digest-bot/
├── AGENTS.md            # AI agent onboarding (read first)
├── README.md            # This file
├── TODO.md              # Work tracking (active + completed)
├── main.py              # Bot entry, commands, state machine
├── collector.py         # Transcript reader + message extractor
├── collection_engine.py # Parallel, retriable collection
├── llm.py               # LLM interface (sync + async)
├── recorder.py          # Digest file writer (YAML + markdown)
├── reflection.py        # Nightly reflection orchestration
├── config.py            # Constants, paths, SGT timezone
├── scheduler.py         # APScheduler: timed collections
├── stt.py               # Voice transcription (Whisper)
│
├── specs/               # Active PRDs only
├── docs/
│   ├── architecture/    # System design, format spec, testing strategy
│   └── completed/       # Archived PRDs and historical specs
├── tests/               # Unit (test_*.py) + E2E (run_e2e.py)
└── scripts/             # Utilities
```

## Service

| Item | Value |
|------|-------|
| LaunchAgent | `~/Library/LaunchAgents/com.digest-bot.plist` |
| Log | `/tmp/digest-bot.log` |
| Git remote | `github-digest:boyangwang/digest.git` |
| Output | `NotesVault/Artificial-Colloquia/Doudou-Digest/` |

## PRD Template

```markdown
# DIGEST-XXX: <Title>

> **Status:** 🔴 Draft
> **Priority:** P1
> **Tasks:** 0/N complete
> **Created:** YYYY-MM-DD

## Problem Statement
## Tasks
- [ ] **T1** — description
## Acceptance Criteria
## Files to Modify
```

---

*Test counts: 159 (134 unit/integration + 25 E2E) — 2026-03-03*
