# Architecture Overview

## System Diagram

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Telegram    │────▶│   main.py    │────▶│  collector.py    │
│  (Boyang)    │◀────│  Bot + FSM   │     │  Transcript I/O  │
└─────────────┘     └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────┴───────┐
                    │  collection  │
                    │  _engine.py  │──── asyncio.gather (parallel)
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ llm.py   │ │ llm.py   │ │ llm.py   │
        │ Session A│ │ Session B│ │ Session C│
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │             │             │
             ▼             ▼             ▼
        openclaw agent --local (subprocess per session)
                           │
                    ┌──────┴───────┐
                    │ recorder.py  │
                    │ Digest File  │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │ Obsidian     │
                    │ Vault (Sync) │
                    └──────────────┘
```

## State Machine

The bot operates as a finite state machine per user:

```
IDLE ──(text)──▶ ACTIVE ──(text)──▶ ACTIVE
  │                │                   │
  │            (30min idle)         (/sleep)
  │                │                   │
  │                ▼                   ▼
  │             IDLE              FINALIZING
  │                                    │
  │                              (reflection)
  │                                    │
  │                                    ▼
  └────────────────────────────────  IDLE
```

States: `IDLE` → `ACTIVE` → `FINALIZING` → `IDLE`

## Collection Engine (DIGEST-009)

### Generation Counter Supersession
Each `collect()` call increments a generation counter. If a new collection
starts while an old one is running, the old one's results are discarded
(generation mismatch).

### Parallel Execution
All sessions are summarized simultaneously via `asyncio.gather()`.
Each session gets a **derived session ID** based on the source session name
(not random UUID) to avoid lock contention.

### Retry
3 attempts per session, exponential backoff (5s, 10s).
All-or-nothing: coverage advances only when ALL sessions succeed.

## Nightly Reflection (DIGEST-007)

After `/sleep`, the bot:
1. Collects remaining messages
2. Finalizes the digest file
3. Spawns an Opus agent that reads all conversations and writes to workspace files
4. Sends a markdown summary to Boyang via Telegram
5. Includes git diff stat of workspace changes

## Key Design Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
| Derived session IDs | Natural partition key, no lock contention | 2026-03-03 |
| All-or-nothing collection | Partial coverage gaps are worse than retrying | 2026-03-03 |
| `except Exception: pass` banned | Silently swallowed P1 TypeError | 2026-03-03 |
| SGT mandatory everywhere | Naive datetime caused 0-message bug | 2026-03-03 |
| Subprocess `start_new_session=True` | Enables clean process group termination | 2026-03-03 |
| Test mode via user allowlist | Same bot, two behaviors (production + test) | 2026-03-02 |
