# AGENTS.md — Sleep Digest Bot

> AI coding agents: read this file first. It's your onboarding.

## What This Is

A standalone Telegram bot that collects OpenClaw conversation transcripts,
generates summaries via LLM, and writes nightly digest files to an Obsidian vault.

## Tech Stack

- **Language:** Python 3.13 (async)
- **Framework:** python-telegram-bot v21
- **LLM:** `openclaw agent --local` (Opus) for summaries + reflections
- **Storage:** Markdown files in Obsidian vault with YAML frontmatter
- **Scheduling:** APScheduler for timed collections
- **Process:** launchd on macOS (plist: `com.digest-bot`)

## Critical Conventions

### Timezone: ALWAYS Use SGT
```python
from datetime import datetime, timezone, timedelta
SGT = timezone(timedelta(hours=8))
datetime.now(SGT)  # ✅ NEVER datetime.now()
```
**Why:** Naive `datetime.now()` caused a P1 production bug (2026-03-03).
Comparing naive vs aware datetimes → silent TypeError → 0 messages collected.

### Error Handling: NO Bare Except
```python
# ❌ NEVER
except Exception:
    pass

# ✅ ALWAYS
except Exception as e:
    logger.error("Context: %s", e)
    raise  # or handle explicitly
```
**Why:** `except Exception: pass` silently swallowed the timezone TypeError.

### Session IDs: Natural Keys, Not Random
When spawning parallel `openclaw agent` subprocesses, derive session IDs from
the source being processed — not UUIDs.
```python
# ❌ Bad: UUID spam, no context reuse
session_id = f"digest-bot-summary-{uuid.uuid4().hex[:8]}"

# ✅ Good: natural partition key
session_id = f"digest-summary-{sanitize(source_name)}"
```

## Development Process (Mandatory)

**5-Step Lifecycle:** Intake → TDD → Implement → E2E → Deploy

1. **PRD first** — Write spec in `specs/` before any code
2. **Tests first** — Write failing tests, verify they fail
3. **Implement** — Make tests green
4. **E2E verify** — Run full suite: `python3 tests/run_e2e.py --verbose`
5. **Deploy** — `launchctl kickstart -k gui/$(id -u)/com.digest-bot`

**Never skip E2E.** Unit tests with mocks cannot catch subprocess-level bugs
(lock contention, timezone mismatches, process orphans).

## Key Commands

```bash
# Run all unit/integration tests
python3 -m pytest tests/ -v

# Run E2E suite (requires bot running in test mode)
python3 tests/run_e2e.py --verbose

# Run specific E2E suite
python3 tests/run_e2e.py --test collection --verbose

# Restart bot
launchctl kickstart -k gui/$(id -u)/com.digest-bot

# Check logs
tail -f /tmp/digest-bot.log
```

## Architecture Overview

```
main.py          — Bot entry, command handlers, state machine
collector.py     — Session transcript reader + message extractor
collection_engine.py — Parallel, retriable, supersedable collection
llm.py           — LLM interface (sync + async compose_summary)
recorder.py      — Digest file writer (YAML frontmatter + markdown)
reflection.py    — Nightly reflection agent orchestration
config.py        — All constants, paths, timezone (SGT)
```

→ Full architecture: `docs/architecture/overview.md`

## File Layout

```
specs/           — Active PRDs (numbered DIGEST-XXX)
docs/            — Architecture, decisions, completed specs
tests/           — Unit tests (test_*.py) + E2E runner (run_e2e.py)
scripts/         — Utility scripts
```

## Do / Don't

| Do | Don't |
|----|-------|
| Use `datetime.now(SGT)` | Use `datetime.now()` |
| Log errors with context | Use bare `except: pass` |
| Write PRD before code | Jump straight to implementation |
| Run E2E after changes | Trust unit tests alone |
| Derive IDs from natural keys | Use random UUIDs for system IDs |
| Import SGT from config | Define timezone locally |
| Commit + push together | Commit without push |
